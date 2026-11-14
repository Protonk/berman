"""Assemble, compress and verify an edition's payload; write it into or compare it with the tree.

The ZIP codec preserves bytes, Unix modes, symlink targets and directory entries under a
namespace; berman_preserve packages single recordings with it. Nothing here keeps state
between builds.
"""
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

from berman.archive.select import PREFIX, SHA

MANIFEST = 'edition-manifest.json'
SCHEMA = 'berman.reading-edition/2'


def assemble(build, generated):
    """Every ZIP member as {path: (bytes, mode)}; checksums.sha256 covers this payload only."""
    root = build.root
    payload = {}
    for entry in build.entries:
        path = entry["path"]
        mode = int(entry["mode"], 0)
        if entry["type"] == "directory":
            payload[path + "/"] = (b"", stat.S_IFDIR | mode)
        elif entry["type"] == "symlink":
            payload[path] = (os.fsencode(entry["target"]), stat.S_IFLNK | mode)
        else:
            data = (root / path).read_bytes()
            if SHA(data) != entry['sha256']:
                raise ValueError(f'Original evidence changed during assembly: {path}')
            payload[path] = (data, stat.S_IFREG | mode)
    support = list(build.documents)
    if build.edition.name == 'stewart':
        support = ["AGENTS.md", ".gitignore", *build.documents]
        support += [p.relative_to(root).as_posix() for p in sorted((root / "tools").glob("*.py"))]
        support += [name for name in ("berman", "berman.json", "BERMAN.md", "MAINTENANCE.md") if (root / name).is_file() and name not in support]
        for folder in ("recipes", "connections", "plans"):
            support += [p.relative_to(root).as_posix() for p in sorted((root / folder).rglob("*")) if p.is_file()]
        support += [p.relative_to(root).as_posix() for p in sorted((root / "hooks/verification").glob("*.json"))]
        support += [p.relative_to(root).as_posix() for p in sorted((root / "tools/development_launchers").glob("*.py"))]
        support += [p.relative_to(root).as_posix() for p in sorted((root / "hooks").glob("*.json"))]
    for path in support:
        payload[path] = ((root / path).read_bytes(), stat.S_IFREG | stat.S_IMODE((root / path).stat().st_mode))
    # The packaged index is a view of selected authority, not a live mutable index file.
    payload['source-layout.json'] = ((json.dumps(build.layout, ensure_ascii=False, indent=2) + '\n').encode(), stat.S_IFREG | 0o644)
    for item in build.source_manifests:
        if item['basis'] == 'recording manifest':
            data = (root / item['path']).read_bytes()
            if SHA(data) != item['sha256']:
                raise ValueError(f'Recording manifest changed during assembly: {item["path"]}')
            payload[item['path']] = (data, stat.S_IFREG | 0o644)
            for entry in item['manifest']['entries']:
                if entry['path'] == 'runs':
                    container = (b'', stat.S_IFDIR | int(entry['mode'], 0))
                    if 'runs/' in payload and payload['runs/'] != container:
                        raise ValueError('Recording manifests disagree on their common namespace directory')
                    payload['runs/'] = container
                    continue
                if entry['path'].startswith('runs/'):
                    continue
                path = f"recordings/{item['run']}/" + entry['path']
                mode = int(entry['mode'], 0)
                if entry['type'] == 'directory':
                    payload[path + '/'] = (b'', stat.S_IFDIR | mode)
                elif entry['type'] == 'symlink':
                    payload[path] = (os.fsencode(os.readlink(root / path)), stat.S_IFLNK | mode)
                else:
                    payload[path] = ((root / path).read_bytes(), stat.S_IFREG | mode)
    for path, data in generated.items():
        payload[path] = (data, stat.S_IFREG | 0o644)
    lines = [f"{SHA(data)}  {path}" for path, (data, mode) in sorted(payload.items()) if stat.S_ISREG(mode)]
    payload["checksums.sha256"] = (("\n".join(lines) + "\n").encode("utf-8"), stat.S_IFREG | 0o644)
    return payload


def make_zip(payload, prefix=None):
    """The namespace is an edition setting; Stewart-compatible editions keep stewart-analysis/."""
    prefix = PREFIX if prefix is None else prefix
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


def verify_zip(data, payload, prefix=None):
    prefix = PREFIX if prefix is None else prefix
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        expected = {prefix + path for path in payload}
        if len(archive.infolist()) != len(expected) or set(archive.namelist()) != expected:
            raise ValueError("ZIP membership does not match the payload")
        for path, (content, mode) in payload.items():
            info = archive.getinfo(prefix + path)
            if archive.read(info) != content or info.external_attr >> 16 != mode:
                raise ValueError(f"ZIP bytes or mode differ: {path}")


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n').encode()


def member(path, data, mode):
    kind = 'directory' if stat.S_ISDIR(mode) else 'symlink' if stat.S_ISLNK(mode) else 'file'
    return {'path': path, 'type': kind, 'mode': mode, 'size': len(data), 'sha256': SHA(data)}


def seal(build, generated, summary):
    """Bind source occurrences and all edition members without a self-referential digest."""
    payload = assemble(build, generated)
    members = [member(path, data, mode) for path, (data, mode) in sorted(payload.items())]
    originals = [item for item in members if item['path'].startswith('runs/') and item['path'] != 'runs/']
    source_paths = {item['path'] for item in build.source_manifests if item['basis'] == 'recording manifest'}
    documents = [item for item in members if item['path'] not in generated
                 and item['path'] not in source_paths
                 and item['path'] not in ('source-layout.json', 'checksums.sha256')
                 and not item['path'].startswith(('runs/', 'recordings/'))]
    inputs = {'originals': originals, 'documents': documents,
              'recordings': [{k: v for k, v in item.items() if k != 'manifest'} for item in build.source_manifests],
              'streams': [{'path': path, **build.stream(path)} for path in sorted(build.files) if path.endswith('.jsonl')],
              'reader': json.loads(generated['review/EDITION.json'])}
    manifest = {'schema': SCHEMA, 'collection_id': build.collection_id, 'namespace': build.edition.prefix,
                'selection': build.selection, 'runs': list(build.runs), 'inputs': inputs,
                'members': members, 'generated': sorted(generated), 'containers': ['runs/'] if 'runs/' in payload else [], 'summary': summary,
                'meaning': 'Pinned reading edition. Native evidence and reader coverage are distinct; publication declares no engagement ending.'}
    payload[MANIFEST] = (json_bytes(manifest), stat.S_IFREG | 0o644)
    return payload, manifest


def relative(name, directory=False):
    if (not isinstance(name, str) or not name or '\\' in name or '\x00' in name
            or name.startswith('/') or any(part in ('', '.', '..') for part in name.rstrip('/').split('/'))
            or (name.endswith('/') and not directory)):
        raise ValueError(f'Unsafe edition member path: {name!r}')
    return PurePosixPath(name)


def inspect_edition(data, expected_sha256=None):
    """Verify a publication from its own contract, without sources or the current builder."""
    digest = SHA(data)
    if expected_sha256 is not None and digest != expected_sha256:
        raise ValueError('Reading edition ZIP digest differs from its published identity')
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = archive.namelist()
            candidates = [name for name in names if name == MANIFEST or name.endswith('/' + MANIFEST)]
            if len(candidates) != 1 or len(names) != len(set(names)):
                raise ValueError('Reading edition needs one manifest and unique ZIP members')
            manifest_name = candidates[0]
            manifest = json.loads(archive.read(manifest_name))
            if not isinstance(manifest, dict) or manifest.get('schema') != SCHEMA:
                raise ValueError('Unsupported reading edition schema')
            uuid.UUID(manifest['collection_id'])
            runs = manifest['runs']
            if (not isinstance(runs, list) or not runs or not all(isinstance(run, str) and re.fullmatch(r'run-[0-9]{3,6}', run) for run in runs)
                    or len(runs) != len(set(runs))):
                raise ValueError('Invalid reading edition run identities')
            if not isinstance(manifest['summary'], dict) or any(type(value) is not int or value < 0 for value in manifest['summary'].values()):
                raise ValueError('Invalid reading edition summary')
            prefix = manifest['namespace']
            if prefix:
                relative(prefix, directory=True)
                if not prefix.endswith('/'):
                    raise ValueError('Reading edition namespace must end with a slash')
            if prefix + MANIFEST != manifest_name:
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
                        or len(raw) != row['size'] or SHA(raw) != row['sha256']):
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
            authorities = set()
            for item in inputs['recordings']:
                if item['basis'] == 'recording manifest':
                    raw = archive.read(prefix + item['path'])
                    source = json.loads(raw)
                    if (source.get('schema') != 'berman.recording/2'
                            or source.get('collection_id') != manifest['collection_id'] or source.get('run') != item['run']):
                        raise ValueError('Reading edition recording authority differs')
                    from berman import evidence
                    evidence.validate(source)
                    recording_paths = {entry['path'] + ('/' if entry['type'] == 'directory' else '')
                                       for entry in source['entries']}
                    def recording_path(name, manifest_path=item['path'], run=item['run']):
                        if name == evidence.MANIFEST:
                            return manifest_path
                        return name if name.startswith('runs/') else f'recordings/{run}/' + name
                    modes = {name: by_path[recording_path(name)]['mode'] for name in recording_paths}
                    modes[evidence.MANIFEST] = by_path[item['path']]['mode']
                    evidence.verify_members(source, modes, lambda name: archive.read(prefix + recording_path(name)))
                    authorities.add(item['path'])
                    authorities.update(recording_path(name) for name in recording_paths if not name.startswith('runs/'))
                elif item['basis'] == 'historical inventory entry':
                    raw = json.dumps(inventory[item['run']], ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
                else:
                    raise ValueError('Unknown reading edition input authority')
                if SHA(raw) != item['sha256']:
                    raise ValueError(f'Reading edition recording input differs: {item["run"]}')
            if {item['run'] for item in inputs['recordings']} != set(runs) or len(inputs['recordings']) != len(runs):
                raise ValueError('Reading edition recording authorities do not cover its runs')
            groups = [{row['path'] for row in inputs[kind]} for kind in ('originals', 'documents')]
            if manifest['containers'] not in ([], ['runs/']):
                raise ValueError('Invalid reading edition namespace containers')
            groups += [authorities, set(manifest['generated']), set(manifest['containers']), {'source-layout.json', 'checksums.sha256'}]
            if set.union(*groups) != set(by_path) or sum(len(group) for group in groups) != len(by_path):
                raise ValueError('Reading edition inputs and generated outputs do not partition its members')
            for stream in inputs['streams']:
                if stream['path'] not in by_path or stream['format'] not in ('codex-jsonl/1', 'claude-jsonl/1', 'unknown'):
                    raise ValueError('Invalid reading edition stream declaration')
            stream_paths = [stream['path'] for stream in inputs['streams']]
            if len(stream_paths) != len(set(stream_paths)) or set(stream_paths) != {path for path in groups[0] if path.endswith('.jsonl')}:
                raise ValueError('Reading edition stream coverage differs from its original JSONL files')
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
    return 'archive/editions/selections/' + SHA(json_bytes(selection)) + '.json'


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
    from berman.evidence import collection
    if manifest['collection_id'] != collection(root) or manifest['selection'] != selection:
        raise ValueError('Published reading edition belongs to another collection or selection')
    return manifest, dict(result, archive_path=pointer['path'], selection_pointer=pointer_path(selection))


def verify_published(root, selection):
    return published(root, selection)[1]


def atomic(root, name, data, immutable=False):
    """Publish complete bytes; an interrupted write never occupies a final immutable name."""
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
        if immutable:
            try:
                os.link(stage, target)
            except FileExistsError:
                if not target.is_file() or target.read_bytes() != data:
                    raise ValueError(f'Existing immutable edition differs: {name}') from None
        else:
            os.replace(stage, target)
    finally:
        stage.unlink(missing_ok=True)


def publish(build, payload):
    data = make_zip(payload, build.edition.prefix)
    _, result = inspect_edition(data)
    digest = result['archive_sha256']
    path = f'archive/editions/{digest}.zip'
    atomic(build.root, path, data, immutable=True)
    # Verify disk bytes before making this publication the selected one.
    verify_edition(build.root / path, digest)
    pointer = {'schema': 'berman.edition-selection/1', 'selection': build.selection, 'path': path, 'sha256': digest}
    atomic(build.root, pointer_path(build.selection), json_bytes(pointer))
    return dict(result, mode='published and verified', archive_path=path, selection_pointer=pointer_path(build.selection))
