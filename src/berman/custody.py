"""Guest custody of portable recordings. Lifecycle decisions belong to the controller."""

import argparse
from dataclasses import replace
import errno
import io
import json
import os
from pathlib import Path
import shutil
import stat
import struct
import sys
import tempfile
import zipfile
import zlib

from berman import evidence, files, protocol
from berman.archive import builder, package
from berman.archive.select import BERMAN_PREFIX, Build, Edition, selection

# Allow room for authority copies and JSON inventory in addition to native observations.
MAX_PACKAGE = protocol.TOTAL_LIMIT + 32 * 1024**2
MAX_MEMBERS = 16384
MAX_DIRECTORY = 8 * 1024**2
MAX_MANIFEST = 16 * 1024**2


def _read(path, limit):
    path = files.regular(Path(path))
    with path.open('rb') as source:
        if os.fstat(source.fileno()).st_size > limit:
            raise ValueError(f'File exceeds custody bound: {path.name}')
        data = source.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f'File exceeds custody bound: {path.name}')
    return data


def _inspect(data, expected):
    """Bound the directory before zipfile indexes it and the inflater before verifying members."""
    if len(data) > MAX_PACKAGE:
        raise ValueError('Recording exceeds custody bound')
    if not isinstance(expected, str) or not evidence.SHA.fullmatch(expected) or files.digest(data) != expected:
        raise ValueError('Recording differs from the expected SHA-256')
    # Berman produces ordinary single-disk ZIPs without a comment or ZIP64 directory.
    if len(data) < 22:
        raise ValueError('Truncated recording ZIP')
    tag, disk, start, local, count, length, offset, comment = struct.unpack_from('<4s4H2LH', data, len(data) - 22)
    if (
        tag != b'PK\x05\x06'
        or disk
        or start
        or comment
        or local != count
        or count > MAX_MEMBERS
        or length > MAX_DIRECTORY
        or offset + length != len(data) - 22
        or data[-42:-38] == b'PK\x06\x07'
    ):
        raise ValueError('Unsupported or oversized ZIP directory')
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
            if len(infos) != count or archive.getinfo(evidence.MANIFEST).file_size > MAX_MANIFEST:
                raise ValueError('Recording directory or manifest exceeds custody bound')
            if sum(i.file_size for i in infos) > MAX_PACKAGE:
                raise ValueError('Expanded recording exceeds custody bound')
            for info in infos:
                name = info.filename.removesuffix('/')
                evidence.relative(name)
                if (
                    info.filename != info.orig_filename
                    or len(info.filename.encode()) > 4096
                    or len(Path(name).parts) > 64
                    or info.flag_bits & 1
                    or info.file_size > protocol.FILE_LIMIT
                ):
                    raise ValueError('Unsupported recording member or member exceeds custody bound')
                # A central-directory size alone cannot bound an intentionally misdeclared DEFLATE stream.
                if info.compress_type == zipfile.ZIP_DEFLATED:
                    offset = info.header_offset
                    filename, extra = struct.unpack_from('<HH', data, offset + 26)
                    begin = offset + 30 + filename + extra
                    inflater = zlib.decompressobj(-15)
                    expanded = inflater.decompress(
                        memoryview(data)[begin : begin + info.compress_size], info.file_size + 1
                    )
                    if len(expanded) != info.file_size or not inflater.eof or inflater.unused_data:
                        raise ValueError('Compressed member differs from declared length')
                    del expanded
                elif info.compress_type != zipfile.ZIP_STORED or info.file_size != info.compress_size:
                    raise ValueError('Unsupported recording compression')
        return evidence.inspect_package(data, expected)
    except (
        zipfile.BadZipFile,
        EOFError,
        struct.error,
        zlib.error,
        KeyError,
        TypeError,
        AttributeError,
        RuntimeError,
    ) as exc:
        raise ValueError(f'Invalid portable recording: {exc}') from exc


def _dirsync(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _receipt(report):
    keys = ('collection_id', 'run', 'package_sha256', 'manifest_sha256', 'bytes', 'coverage')
    return {
        'schema': 'berman.custody-recording/1',
        **{key: report[key] for key in keys},
        'relative_path': f'custody/{report["run"]}/recording.zip',
    }


def _accepted(root, receipt):
    run = receipt['run']
    if not evidence.RUN.fullmatch(run) or receipt['collection_id'] != evidence.collection(root):
        raise ValueError('Recording occurrence differs from study')
    folder = root / 'custody' / run
    if files.read(folder / 'receipt.json') != receipt:
        raise ValueError('Recording receipt differs from retained custody receipt')
    data = _read(folder / 'recording.zip', MAX_PACKAGE)
    report = _inspect(data, receipt['package_sha256'])
    if _receipt(report) != receipt:
        raise ValueError('Retained recording differs from custody receipt')
    return data, report


def verify_recording(root, receipt):
    """Verify a custody receipt and its portable recording using only guest bytes."""
    return _accepted(Path(root), receipt)[1]


def _commit(root, data, expected):
    report = _inspect(data, expected)
    receipt = _receipt(report)
    if evidence.collection(root) != receipt['collection_id']:
        raise ValueError('Recording collection differs from guest study')
    parent = root / 'custody'
    parent.mkdir(exist_ok=True, mode=0o700)
    files.safe_directory(parent)
    _dirsync(root)
    folder = parent / receipt['run']
    if not folder.exists():
        stage = Path(tempfile.mkdtemp(dir=parent, prefix='.incoming-'))
        try:
            files.atomic(stage / 'recording.zip', data)
            files.write(stage / 'receipt.json', receipt)
            _dirsync(stage)
            try:
                stage.rename(folder)
            except OSError as exc:
                if exc.errno not in (errno.ENOTEMPTY, errno.EEXIST):
                    raise
        finally:
            if stage.exists():
                shutil.rmtree(stage)
    _dirsync(parent)
    verify_recording(root, receipt)
    return receipt


def import_recording(root, source, *, expected_sha256, size=None):
    """Accept one staged recording/2 ZIP, or exactly size bytes from a blocking binary stream."""
    if size is not None and (type(size) is not int or not 0 < size <= MAX_PACKAGE):
        raise ValueError('Invalid recording transfer length')
    if hasattr(source, 'read'):
        if size is None:
            raise ValueError('A streamed recording requires an explicit byte length')
        buffer = io.BytesIO()
        while buffer.tell() < size:
            chunk = source.read(min(1024**2, size - buffer.tell()))
            if not chunk:
                raise ValueError('Recording transfer interrupted')
            buffer.write(chunk)
        if source.read(1):
            raise ValueError('Recording transfer exceeds declared length')
        data = buffer.getvalue()
    else:
        data = _read(source, size if size is not None else MAX_PACKAGE)
    if size is not None and len(data) != size:
        raise ValueError('Recording transfer length differs')
    return _commit(Path(root), data, expected_sha256)


def preserve_split_run(root, metadata, blobs):
    """Package a controller state and its accepted CAS paths. Retry with identical facts."""
    root = Path(root)
    run = metadata['run']
    if not evidence.RUN.fullmatch(run) or metadata['collection_id'] != evidence.collection(root):
        raise ValueError('Split run identity differs from study')
    facts = {key: metadata['facts'][key] for key in ('ending', 'capture', 'accept')}
    facts.update({key: metadata['facts'][key] for key in ('preparation', 'launch') if key in metadata['facts']})
    controller = {key: metadata[key] for key in ('assignment', 'transferred', 'collection_id', 'run', 'connection')}
    if 'decision' in metadata:
        controller['decision'] = metadata['decision']
    controller['facts'] = facts
    capture = facts['capture']
    observed = protocol.inventory(capture['members'])
    if set(observed) != set(blobs) or metadata['transferred'] != {k: v[1] for k, v in observed.items()}:
        raise ValueError('Transferred files differ from the observed inventory')
    base = 'runs/' + run
    payload = {}
    directories = {'runs', base, 'evidence'}
    entries = []

    def add(name, data):
        directories.update(str(p) for p in Path(name).parents if p != Path('.'))
        entries.append({'path': name, 'type': 'file', 'size': len(data), 'mode': '0o600', 'sha256': files.digest(data)})
        payload[name] = (data, stat.S_IFREG | 0o600)

    for name, (size, digest) in observed.items():
        if len(name.encode()) > 4000 or len(Path(name).parts) > 60:
            raise ValueError('Evidence path exceeds portable recording bound')
        data = _read(blobs[name], size)
        if len(data) != size or files.digest(data) != digest:
            raise ValueError(f'Guest blob differs from observed bytes: {name}')
        add(f'{base}/{name}', data)
    ending = dict(facts['ending'], declared_at_utc=facts['ending']['at'])
    controller_bytes = protocol.json_bytes(controller)
    if len(controller_bytes) > MAX_MANIFEST:
        raise ValueError('Controller receipts exceed recording bound')
    add('evidence/controller.json', controller_bytes)
    add('evidence/ending.json', protocol.json_bytes(ending))
    for name in directories:
        entries.append({'path': name, 'type': 'directory', 'mode': '0o700'})
        payload[name + '/'] = (b'', stat.S_IFDIR | 0o700)
    entries.sort(key=lambda item: item['path'])
    refs = dict.fromkeys(('operation', 'migration', 'capture', 'binding', 'closure'))
    refs.update(controller='evidence/controller.json', ending='evidence/ending.json')
    if capture.get('parent_sha256'):
        refs['capture'], _ = evidence.committed_capture(run, lambda name: payload[name][0])
    inventory = [dict(row, path=row['path'][len(base) + 1 :]) for row in entries if row['path'].startswith(base + '/')]
    coverage = list(capture.get('limitations', []))
    coverage.extend(f'No {key} receipt retained' for key in ('preparation', 'launch') if key not in facts)
    # Decode only transcripts explicitly attributed by a co-located native capture record.
    streams = []
    for name in sorted(observed):
        if not name.endswith('.jsonl'):
            continue
        ref = str(Path(name).with_name('capture.json'))
        fmt = 'unknown'
        associated = Path(name).parts
        native_source = refs['capture'] == f'{base}/{ref}' or (
            len(associated) == 3
            and associated[0] == 'associated'
            and associated[1] in (capture.get('associated_sessions') or ())
        )
        if native_source and Path(name).name == 'transcript.jsonl' and ref in observed:
            meta = json.loads(payload[f'{base}/{ref}'][0])
            if meta.get('format') in ('codex-jsonl/1', 'claude-jsonl/1') and meta.get('sha256') == observed[name][1]:
                fmt = meta['format']
        streams.append(
            {'path': f'{base}/{name}', 'format': fmt, 'basis': f'{base}/{ref}' if fmt != 'unknown' else None}
        )
    manifest = {
        'schema': evidence.SCHEMA,
        'profile': 'split',
        'run': run,
        'collection_id': metadata['collection_id'],
        'entries': entries,
        'evidence': refs,
        'coverage': {'unknown': coverage},
        'streams': streams,
        'attribution': {
            'basis': 'retained controller assignment',
            'evidence': refs['controller'],
            'connection': metadata['connection'],
        },
        'custody': {
            'inventory': {'run': run, 'current_root': base, 'entries': inventory},
            'meaning': 'Accepted native file bytes. Modes describe the portable package.',
        },
    }
    raw_manifest = protocol.json_bytes(manifest)
    if len(raw_manifest) > MAX_MANIFEST or len(payload) >= MAX_MEMBERS:
        raise ValueError('Recording inventory exceeds custody bound')
    saved_receipt = root / 'custody' / run / 'receipt.json'
    if saved_receipt.exists():
        saved = files.read(saved_receipt)
        if verify_recording(root, saved)['manifest'] != manifest:
            raise ValueError('Existing recording differs from requested preservation')
        return saved
    payload[evidence.MANIFEST] = (raw_manifest, stat.S_IFREG | 0o644)
    data = package.make_zip(payload, '')
    return _commit(root, data, files.digest(data))


def _build(view, archive, manifest):
    """Project verified file bytes for the existing readers, using recorded metadata in the edition."""
    run = manifest['run']
    ident = manifest['collection_id']
    files.write(view / 'collection.json', {'id': ident})
    authority = view / 'recordings' / run
    authority.mkdir(parents=True)
    (authority / 'manifest.json').write_bytes(archive.read(evidence.MANIFEST))
    rows = []
    original = manifest['custody']['inventory']
    for item in manifest['entries']:
        name, kind = item['path'], item['type']
        is_original = name.startswith('runs/')
        dest = (view if is_original or name == 'runs' else authority) / name
        if kind == 'file':
            dest.parent.mkdir(parents=True, exist_ok=True)
            with dest.open('xb') as out:
                out.write(archive.read(name))
        elif kind == 'directory':
            dest.mkdir(exist_ok=True, parents=True)
        if is_original:
            target = item.get('target', '')
            link_bytes = os.fsencode(target)
            rows.append(
                dict(
                    item,
                    size=item.get('size', len(link_bytes) if kind == 'symlink' else ''),
                    sha256=item.get('sha256', files.digest(link_bytes) if kind == 'symlink' else ''),
                    mtime_ns=item.get('mtime_ns', original.get('root_mtime_ns', '') if name == 'runs/' + run else ''),
                    target=target,
                )
            )
    attr = manifest['attribution']
    connection = attr['connection']
    choices = (connection,) if connection else ()
    details = {
        'id': connection,
        'name': connection or 'unattributed',
        'basis': attr['basis'],
        'readers': [],
        'checkouts': [],
        'harness': 'unknown',
    }
    if manifest['profile'] == 'split':
        details['harness'] = json.loads(archive.read(manifest['evidence']['controller']))['assignment'].get(
            'harness', 'unknown'
        )
    elif manifest['evidence'].get('operation'):
        operation = json.loads(archive.read(manifest['evidence']['operation']))
        details['harness'] = (operation.get('harness') or {}).get('id', 'unknown')
        if operation.get('project'):
            details['checkouts'] = [operation['project']]
    return Build(
        view,
        Edition('connections', 'BERMAN FIELDWORK', BERMAN_PREFIX, choices),
        {'runs': [original]},
        sorted(rows, key=lambda row: row['path']),
        (run,),
        {run: details},
        True,
        True,
        selection('connections', runs=(run,)),
    )


def publish_edition(root, receipt):
    """Publish one verified custody occurrence as a standard, independently readable edition."""
    root = Path(root)
    data, report = _accepted(root, receipt)
    request = selection('connections', runs=(receipt['run'],))
    with package.publication_lock(root), zipfile.ZipFile(io.BytesIO(data)) as archive:
        pointer = root / package.pointer_path(request)
        if pointer.exists():
            pinned, result = package.published(root, request)
            sources = pinned['inputs']['recordings']
            if len(sources) != 1 or sources[0]['sha256'] != files.digest(archive.read(evidence.MANIFEST)):
                raise ValueError('Existing edition selects different recording authority')
            result = dict(result, mode='published and verified')
        else:
            with tempfile.TemporaryDirectory(prefix='.reader-', dir=root / 'custody') as directory:
                build = _build(Path(directory), archive, report['manifest'])
                generated, summary = builder.render(build)
                links = {
                    f'recordings/{receipt["run"]}/{row["path"]}': os.fsencode(row['target'])
                    for row in report['manifest']['entries']
                    if row['type'] == 'symlink'
                }
                payload, _ = package.seal(build, generated, summary, retained_links=links)
                edition = package.make_zip(payload, build.edition.prefix)
            result = package.publish_locked(replace(build, root=root), edition)
        # SQLite can commit its publish fact as soon as this function returns.
        for directory in (pointer.parent, pointer.parent.parent, root / 'archive', root):
            _dirsync(directory)
        return result


def preserve_and_publish(root, metadata, blobs):
    receipt = preserve_split_run(root, metadata, blobs)
    return {'recording': receipt, 'edition': publish_edition(root, receipt)}


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Import one portable recording from standard input into guest custody.'
    )
    parser.add_argument('--root', required=True, type=Path, help='guest study with collection.json')
    parser.add_argument('--size', required=True, type=int)
    parser.add_argument('--sha256', required=True)
    args = parser.parse_args(argv)
    try:
        receipt = import_recording(args.root, sys.stdin.buffer, expected_sha256=args.sha256, size=args.size)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.exit(1, f'Custody: {exc}\n')
    print(json.dumps(receipt))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
