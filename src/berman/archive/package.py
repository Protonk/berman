"""Package guest reading editions and verify them from their retained members."""
from contextlib import contextmanager
import fcntl
import time
import io
import json
import os
from pathlib import Path
from pathlib import PurePosixPath
import re
import stat
import tempfile
import uuid
import zipfile

from berman import evidence, files

PREFIX = "berman-analysis/"

MANIFEST = 'edition-manifest.json'
SCHEMA = 'berman.reading-edition/2'


def make_zip(payload, prefix):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path, (data, mode) in sorted(payload.items()):
            info = zipfile.ZipInfo(prefix + path, date_time=(2026, 10, 22, 0, 0, 0))
            info.create_system = 3
            info.external_attr = mode << 16
            if stat.S_ISDIR(mode):
                info.external_attr |= 0x10
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data, compresslevel=9)
    return stream.getvalue()


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n').encode()


def member(path, data, mode):
    kind = 'directory' if stat.S_ISDIR(mode) else 'symlink' if stat.S_ISLNK(mode) else 'file'
    return {'path': path, 'type': kind, 'mode': mode, 'size': len(data), 'sha256': files.digest(data)}


def selection(run, supplement=None):
    request = {'edition': 'connections', 'connections': [], 'runs': [run]}
    if supplement is not None:
        request['supplement'] = supplement
    return request


def edition_path(run, name):
    """Where a member of a recording appears in its reading edition."""
    if name == evidence.MANIFEST:
        return f'recordings/{run}/manifest.json'
    if name.startswith('runs/'):
        return name
    return f'recordings/{run}/{name}'


def seal(recording, archive, generated, summary):
    """Copy every verified member directly from its recording and pin the new edition."""
    run = recording['run']
    payload = {edition_path(run, i.filename): (archive.read(i), i.external_attr >> 16) for i in archive.infolist()}
    manifest_path = edition_path(run, evidence.MANIFEST)
    source = {'run': run, 'basis': 'recording manifest', 'path': manifest_path, 'sha256': files.digest(payload[manifest_path][0])}
    layout = {'runs': [recording['custody']['inventory']]}
    payload['source-layout.json'] = (json_bytes(layout), stat.S_IFREG | 0o644)
    for path, data in generated.items():
        payload[path] = (data, stat.S_IFREG | 0o644)
    checksums = [f'{files.digest(data)}  {path}' for path, (data, mode) in sorted(payload.items()) if stat.S_ISREG(mode)]
    payload['checksums.sha256'] = (('\n'.join(checksums) + '\n').encode(), stat.S_IFREG | 0o644)
    members = [member(path, data, mode) for path, (data, mode) in sorted(payload.items())]
    originals = [item for item in members if item['path'].startswith('runs/') and item['path'] != 'runs/']
    declared = {s['path']: s['format'] for s in recording['streams']}
    streams = [{'path': r['path'], 'format': declared.get(r['path'], 'unknown'), 'authority': manifest_path,
                'basis': 'recording manifest' if r['path'] in declared else 'no stream declaration in recording manifest'}
               for r in originals if r['type'] == 'file' and r['path'].endswith('.jsonl')]
    inputs = {'originals': originals, 'documents': [], 'recordings': [source], 'streams': streams,
              'reader': json.loads(generated['review/EDITION.json'])}
    manifest = {'schema': SCHEMA, 'collection_id': recording['collection_id'], 'namespace': PREFIX,
                'selection': selection(run, (recording.get('supplement') or {}).get('sequence')), 'runs': [run],
                'inputs': inputs, 'members': members, 'generated': sorted(generated),
                'containers': ['runs/'] if 'runs/' in payload else [], 'summary': summary}
    payload[MANIFEST] = (json_bytes(manifest), stat.S_IFREG | 0o644)
    return make_zip(payload, PREFIX)


def relative(name, directory=False):
    if (not isinstance(name, str) or not name or '\\' in name or '\x00' in name
            or name.startswith('/') or any(part in ('', '.', '..') for part in name.rstrip('/').split('/'))
            or (name.endswith('/') and not directory)):
        raise ValueError(f'Unsafe edition member path: {name!r}')
    return PurePosixPath(name)


def inspect_edition(data, expected_sha256=None):
    """Verify a publication from its own contract, without sources or the current builder."""
    digest = files.digest(data)
    if expected_sha256 is not None and digest != expected_sha256:
        raise ValueError('Reading edition ZIP digest differs from its published identity')
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = archive.namelist()
            manifest_name = PREFIX + MANIFEST
            if manifest_name not in names or len(names) != len(set(names)):
                raise ValueError('Reading edition needs its manifest and unique ZIP members')
            manifest = json.loads(archive.read(manifest_name))
            if not isinstance(manifest, dict) or manifest.get('schema') != SCHEMA:
                raise ValueError('Unsupported reading edition schema')
            uuid.UUID(manifest['collection_id'])
            runs = manifest['runs']
            if (not isinstance(runs, list) or len(runs) != 1 or not isinstance(runs[0], str)
                    or not evidence.RUN.fullmatch(runs[0])):
                raise ValueError('Guest reading edition requires one run')
            if not isinstance(manifest['summary'], dict) or any(type(value) is not int or value < 0 for value in manifest['summary'].values()):
                raise ValueError('Invalid reading edition summary')
            prefix = manifest['namespace']
            if prefix != PREFIX:
                raise ValueError('Reading edition manifest namespace differs')
            if archive.getinfo(manifest_name).external_attr >> 16 != stat.S_IFREG | 0o644:
                raise ValueError('Reading edition manifest mode differs')
            rows = manifest['members']
            if not isinstance(rows, list):
                raise ValueError('Reading edition members must be a list')
            by_path = {}
            for row in rows:
                path = row['path']
                relative(path, directory=row['type'] == 'directory')
                if path == MANIFEST or path in by_path:
                    raise ValueError('Duplicate or self-referential edition member')
                mode = row['mode']
                expected_type = {'file': stat.S_IFREG, 'directory': stat.S_IFDIR, 'symlink': stat.S_IFLNK}.get(row['type'])
                if (type(mode) is not int or stat.S_IFMT(mode) != expected_type
                        or (row['type'] == 'directory') != path.endswith('/')):
                    raise ValueError(f'Invalid edition member type or mode: {path}')
                info = archive.getinfo(prefix + path)
                raw = archive.read(info)
                if (info.external_attr >> 16 != mode or type(row['size']) is not int
                        or len(raw) != row['size'] or files.digest(raw) != row['sha256']):
                    raise ValueError(f'Reading edition member bytes or mode differ: {path}')
                by_path[path] = row
            if set(names) != {prefix + name for name in by_path} | {manifest_name}:
                raise ValueError('Reading edition ZIP membership differs from its manifest')
            inputs = manifest['inputs']
            for kind in ('originals', 'documents'):
                for row in inputs[kind]:
                    if by_path.get(row['path']) != row:
                        raise ValueError(f'Reading edition input differs from its member: {row["path"]}')
            if {r['path'] for r in inputs['originals']} != {p for p in by_path if p.startswith('runs/') and p != 'runs/'}:
                raise ValueError('Reading edition original membership differs from its inputs')
            layout = json.loads(archive.read(prefix + 'source-layout.json'))
            inventory = {entry['run']: entry for entry in layout['runs']}
            if set(inventory) != set(runs) or len(inventory) != len(layout['runs']):
                raise ValueError('Reading edition run membership differs from its inventory')
            run = runs[0]
            sources = inputs['recordings']
            if len(sources) != 1 or sources[0]['run'] != run:
                raise ValueError('Reading edition needs one recording for its run')
            item = sources[0]
            if item['basis'] != 'recording manifest':
                raise ValueError('Reading edition requires a split recording manifest')
            if item['path'] != edition_path(item['run'], evidence.MANIFEST):
                raise ValueError('Reading edition source manifest has a different path')
            raw = archive.read(prefix + item['path'])
            source = json.loads(raw)
            if (source.get('schema') != 'berman.recording/2'
                    or source.get('collection_id') != manifest['collection_id'] or source.get('run') != item['run']):
                raise ValueError('Reading edition recording authority differs')
            evidence.validate(source)
            recording_paths = {entry['path'] + ('/' if entry['type'] == 'directory' else '')
                               for entry in source['entries']}
            modes = {name: by_path[edition_path(item['run'], name)]['mode'] for name in recording_paths}
            modes[evidence.MANIFEST] = by_path[item['path']]['mode']
            evidence.verify_members(source, modes, lambda name: archive.read(prefix + edition_path(run, name)))
            authorities = {item['path']}
            authorities.update(edition_path(item['run'], name) for name in recording_paths if not name.startswith('runs/'))
            if (manifest['selection'] != selection(source['run'], (source.get('supplement') or {}).get('sequence'))
                    or inventory[source['run']] != source['custody']['inventory']
                    or {p for p in recording_paths if p.startswith('runs/') and p != 'runs/'} != {row['path'] for row in inputs['originals']}):
                raise ValueError('Reading edition inventory or selection differs from its recording')
            if files.digest(raw) != item['sha256']:
                raise ValueError(f'Reading edition recording input differs: {item["run"]}')
            groups = [{row['path'] for row in inputs[kind]} for kind in ('originals', 'documents')]
            if manifest['containers'] not in ([], ['runs/']):
                raise ValueError('Invalid reading edition namespace containers')
            groups += [authorities, set(manifest['generated']), set(manifest['containers']), {'source-layout.json', 'checksums.sha256'}]
            if set.union(*groups) != set(by_path) or sum(len(group) for group in groups) != len(by_path):
                raise ValueError('Reading edition inputs and generated outputs do not partition its members')
            declared = {stream['path']: stream['format'] for stream in source['streams']}
            streams = {stream['path']: stream['format'] for stream in inputs['streams']}
            expected = {path: declared.get(path, 'unknown') for path in groups[0]
                        if by_path[path]['type'] == 'file' and path.endswith('.jsonl')}
            if len(streams) != len(inputs['streams']) or streams != expected:
                raise ValueError('Reading edition streams differ from its recording manifest')
            reader = json.loads(archive.read(prefix + 'review/EDITION.json'))
            if inputs['reader'] != reader or reader.get('collection_id') != manifest['collection_id']:
                raise ValueError('Reading edition reader input differs')
    except (zipfile.BadZipFile, KeyError, TypeError, AttributeError, UnicodeError, RuntimeError, NotImplementedError) as exc:
        raise ValueError(f'Malformed reading edition: {exc}') from exc
    result = dict(manifest['summary'], mode='verified', collection_id=manifest['collection_id'],
                  archive_bytes=len(data), archive_sha256=digest, zip_members=len(names), freshness='not-checked')
    return manifest, result


def verify_edition(path, expected_sha256=None):
    path = Path(path)
    if expected_sha256 is None and re.fullmatch(r'[0-9a-f]{64}\.zip', path.name):
        expected_sha256 = path.stem
    _, result = inspect_edition(path.read_bytes(), expected_sha256)
    return dict(result, archive_path=str(path))


def pointer_path(selection):
    return 'archive/editions/selections/' + files.digest(json_bytes(selection)) + '.json'


def published(root, selection):
    """Resolve the explicit selection pointer; neither discover sources nor rebuild."""
    pointer = json.loads((Path(root) / pointer_path(selection)).read_bytes())
    if not isinstance(pointer, dict) or pointer.get('schema') != 'berman.edition-selection/1' or pointer.get('selection') != selection:
        raise ValueError('Reading edition selection pointer differs')
    digest = pointer.get('sha256')
    if not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest):
        raise ValueError('Invalid published reading edition digest')
    if pointer.get('path') != f'archive/editions/{digest}.zip':
        raise ValueError('Invalid published reading edition location')
    manifest, result = inspect_edition((Path(root) / pointer['path']).read_bytes(), digest)
    if manifest['collection_id'] != evidence.collection(root) or manifest['selection'] != selection:
        raise ValueError('Published reading edition belongs to another collection or selection')
    return manifest, dict(result, archive_path=pointer['path'], selection_pointer=pointer_path(selection))


def install(root, name, data):
    """Install complete immutable bytes, or verify an identical prior installation."""
    relative(name)
    root = Path(root)
    target = root / name
    if target.is_symlink() or any(p.is_symlink() for p in target.parents if p != root.parent):
        raise ValueError(f'Unsafe edition publication destination: {name}')
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.edition-', dir=target.parent)
    stage = Path(temporary)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        stage.chmod(0o644)
        try:
            os.link(stage, target)
        except FileExistsError:
            if not target.is_file() or target.read_bytes() != data:
                raise ValueError(f'Existing immutable edition differs: {name}') from None
    finally:
        stage.unlink(missing_ok=True)


@contextmanager
def publication_lock(root):
    directory = Path(root) / 'archive/editions'
    directory.mkdir(parents=True, exist_ok=True)
    fd = os.open(directory / '.publication.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        deadline = time.monotonic() + 30
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise ValueError('Another reading publication is in progress; retry this command') from None
                time.sleep(0.05)
        yield
    finally:
        os.close(fd)


def publish_locked(root, request, data):
    manifest, result = inspect_edition(data)
    if manifest['selection'] != request or manifest['collection_id'] != evidence.collection(root):
        raise ValueError('Reading edition belongs to a different collection or selection')
    digest = result['archive_sha256']
    path = f'archive/editions/{digest}.zip'
    install(root, path, data)
    # Verify disk bytes before making this publication the selected one.
    verify_edition(root / path, digest)
    pointer = {'schema': 'berman.edition-selection/1', 'selection': request, 'path': path,
               'sha256': digest, 'previous': None, 'retiring': []}
    install(root, pointer_path(request), json_bytes(pointer))
    return dict(result, mode='published and verified', archive_path=path, selection_pointer=pointer_path(request))
