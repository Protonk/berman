"""Portable recording contracts. Standard library only; historical evidence is never repaired.

An occurrence is (collection, run, original path), independently of its content digest.
The manifest describes membership and references evidence; it cannot manufacture an ending.
"""

import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import shutil
import tempfile
import uuid
import zipfile

from berman import files

SCHEMA = 'berman.recording/2'
MANIFEST = 'recording.json'
RUN = re.compile(r'run-[0-9]{3,6}\Z')
SHA = re.compile(r'[0-9a-f]{64}\Z')


def collection(root):
    """The explicit portable identity, never inferred from a workstation path or name."""
    root = Path(root)
    values = []
    for name, key in (('collection.json', 'id'), ('berman.json', 'collection_id')):
        path = root / name
        if path.exists():
            value = files.read(path).get(key)
            if value is not None:
                values.append(str(uuid.UUID(value)))
    if not values:
        raise ValueError('No collection identity: record collection.json with the agreed collection UUID before export')
    if len(set(values)) != 1:
        raise ValueError('collection.json and berman.json name different collections')
    return values[0]


def relative(value):
    if not isinstance(value, str) or not value or '\\' in value or '\x00' in value:
        raise ValueError(f'Invalid recording member: {value!r}')
    path = PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts or path.as_posix() != value or value == '.':
        raise ValueError(f'Unsafe recording member: {value!r}')
    return value


def identity(manifest):
    return files.digest_json(manifest)


def validate(manifest):
    """Structural and referential checks also available in standalone, dependency-free readers."""
    if not isinstance(manifest, dict) or manifest.get('schema') != SCHEMA:
        raise ValueError(f'Expected {SCHEMA} manifest')
    if not isinstance(manifest.get('collection_id'), str):
        raise ValueError('Recording needs an explicit collection UUID')
    uuid.UUID(manifest['collection_id'])
    if not isinstance(manifest.get('run'), str) or not RUN.fullmatch(manifest['run']):
        raise ValueError('Invalid recording run identity')
    if manifest.get('profile') not in ('native', 'historical-import'):
        raise ValueError('Recording needs an explicit native or historical-import profile')
    if not isinstance(manifest.get('entries'), list) or not manifest['entries']:
        raise ValueError('Recording has no inventoried members')
    seen = {}
    for item in manifest['entries']:
        if not isinstance(item, dict):
            raise ValueError('Recording entries must be objects')
        name = relative(item['path'])
        if not (
            name in ('runs', 'runs/' + manifest['run'], 'evidence')
            or name.startswith('runs/' + manifest['run'] + '/')
            or name.startswith('evidence/')
        ):
            raise ValueError(f'Recording member is outside its occurrence: {name}')
        if name == MANIFEST or name in seen:
            raise ValueError(f'Duplicate or reserved recording member: {name}')
        kind = item['type']
        if kind not in ('file', 'directory', 'symlink'):
            raise ValueError(f'Unsupported recording member type: {name}')
        mode = int(item['mode'], 0)
        if mode < 0 or mode > 0o7777:
            raise ValueError(f'Invalid member permissions: {name}')
        if kind == 'file':
            if type(item.get('size')) is not int or item['size'] < 0 or not SHA.fullmatch(item.get('sha256', '')):
                raise ValueError(f'Invalid file size or digest: {name}')
        elif kind == 'symlink' and not isinstance(item.get('target'), str):
            raise ValueError(f'Invalid symlink target: {name}')
        if 'mtime_ns' in item and type(item['mtime_ns']) is not int:
            raise ValueError(f'Invalid recorded mtime: {name}')
        seen[name] = item
    for name in seen:
        for parent in PurePosixPath(name).parents:
            if parent.as_posix() in seen and seen[parent.as_posix()]['type'] != 'directory':
                raise ValueError(f'Recording member has a non-directory parent: {name}')
    evidence = manifest.get('evidence')
    if not isinstance(evidence, dict):
        raise ValueError('Recording needs explicit evidence references')
    for fact in ('ending', 'capture', 'operation', 'migration', 'binding', 'closure'):
        ref = evidence.get(fact)
        if ref is not None and (not isinstance(ref, str) or ref not in seen or seen[ref]['type'] != 'file'):
            raise ValueError(f'Recording {fact} refers to missing evidence')
    coverage = manifest.get('coverage')
    if not isinstance(coverage, dict) or not isinstance(coverage.get('unknown'), list):
        raise ValueError('Recording needs an explicit coverage report')
    if not all(isinstance(value, str) and value for value in coverage['unknown']):
        raise ValueError('Invalid coverage limitation')
    if manifest['profile'] == 'native' and any(
        evidence.get(key) is None for key in ('ending', 'capture', 'operation', 'migration', 'binding', 'closure')
    ):
        raise ValueError('A new recording requires capture, binding, closure, ending, operation and migration evidence')
    for fact in ('ending', 'capture', 'operation', 'migration', 'binding', 'closure'):
        if evidence.get(fact) is None and not any(fact in value for value in coverage['unknown']):
            raise ValueError(f'Missing {fact} evidence must be named in coverage.unknown')
    for stream in manifest.get('streams', []):
        if (
            stream['path'] not in seen
            or seen[stream['path']]['type'] != 'file'
            or stream['format'] not in ('codex-jsonl/1', 'claude-jsonl/1', 'unknown')
        ):
            raise ValueError('Invalid recording stream descriptor')
    custody = manifest.get('custody', {}).get('inventory')
    if (
        not isinstance(custody, dict)
        or custody.get('run') != manifest['run']
        or custody.get('current_root') != 'runs/' + manifest['run']
    ):
        raise ValueError('Recording custody inventory must name this occurrence')
    base = 'runs/' + manifest['run'] + '/'
    originals = {
        name.removeprefix(base): {**item, 'path': name.removeprefix(base)}
        for name, item in seen.items()
        if name.startswith(base)
    }
    if originals != {item['path']: item for item in custody['entries']}:
        raise ValueError('Recording occurrence members and custody inventory disagree')
    attribution = manifest.get('attribution')
    if (
        not isinstance(attribution, dict)
        or 'connection' not in attribution
        or not isinstance(attribution.get('basis'), str)
    ):
        raise ValueError('Recording needs an explicit connection attribution or unknown')
    ref = attribution.get('evidence')
    if ref is not None and (not isinstance(ref, str) or ref not in seen or seen[ref]['type'] != 'file'):
        raise ValueError('Connection attribution refers to missing evidence')
    if attribution['connection'] is not None and (not isinstance(attribution['connection'], str) or not ref):
        raise ValueError('A connection attribution requires retained evidence')
    return manifest


def mode(item):
    return {'file': stat.S_IFREG, 'directory': stat.S_IFDIR, 'symlink': stat.S_IFLNK}[item['type']] | int(
        item['mode'], 0
    )


def verify_members(manifest, members, read):
    """Exact membership, bytes and modes; observed filesystem mtimes are not content identity."""
    validate(manifest)
    expected = {item['path'] + ('/' if item['type'] == 'directory' else ''): item for item in manifest['entries']}
    if set(members) != set(expected) | {MANIFEST}:
        raise ValueError('Recording membership differs from its manifest')
    if members[MANIFEST] != stat.S_IFREG | 0o644:
        raise ValueError('Recording manifest mode differs')
    for name, item in expected.items():
        if members[name] != mode(item):
            raise ValueError(f'Recording mode differs: {name}')
        data = read(name)
        if item['type'] == 'file':
            if len(data) != item['size'] or files.digest(data) != item['sha256']:
                raise ValueError(f'Recording bytes differ: {name}')
        elif item['type'] == 'symlink':
            if data != os.fsencode(item['target']):
                raise ValueError(f'Recording link differs: {name}')
        elif data:
            raise ValueError(f'Recording directory has content: {name}')
    check_authority(manifest, read)


def check_authority(manifest, read, snapshot_bytes=True):
    """Check the retained assertions, without replacing them with a manifest boolean."""
    refs = manifest['evidence']

    def load(key):
        return json.loads(read(refs[key])) if refs.get(key) else None

    ending, operation, capture = load('ending'), load('operation'), load('capture')
    for key in refs:
        if refs[key] is not None and not isinstance(load(key), dict):
            raise ValueError(f'Retained {key} must be an object')
    if ending is not None:
        if not isinstance(ending, dict) or not ending.get('reason') or not ending.get('declared_at_utc'):
            raise ValueError('Retained ending is not a declaration')
        if operation is not None and operation.get('ending') != ending:
            raise ValueError('Retained operation and ending disagree')
    if operation is not None and operation.get('run') != manifest['run']:
        raise ValueError('Retained operation names another run')
    if capture is not None and capture.get('run') != manifest['run']:
        raise ValueError('Retained capture names another run')
    if capture is not None:
        folder = PurePosixPath(refs['capture']).parent
        if snapshot_bytes:
            raw = read(str(folder / 'transcript.jsonl'))
            if len(raw) != capture['saved_bytes'] or files.digest(raw) != capture['sha256']:
                raise ValueError('Retained capture and transcript bytes disagree')
            if capture.get('reader_sha256') and files.digest(read(str(folder / 'conversation.txt'))) != capture['reader_sha256']:
                raise ValueError('Retained capture and reader disagree')
            history = read(str(folder / 'captures.jsonl')).splitlines()
            if not history or json.loads(history[-1]) != capture:
                raise ValueError('Retained capture and history disagree')
        binding, closure = load('binding'), load('closure')
        if binding is not None and (
            binding.get('run') != manifest['run'] or binding.get('session_id') != capture['session_id']
        ):
            raise ValueError('Retained binding and capture disagree')
        if closure is not None:
            if closure.get('capture_sha256') != capture['sha256']:
                raise ValueError('Retained closure and capture disagree')
            if closure.get('generation') and closure['generation'] != files.digest_json(capture):
                raise ValueError('Retained closure and capture generation disagree')
            for key in ('run', 'session_id', 'saved_bytes'):
                if key in closure and closure[key] != capture[key]:
                    raise ValueError(f'Retained closure and capture {key} disagree')
    attribution = manifest.get('attribution', {})
    if (
        operation is not None
        and operation.get('connection')
        and attribution.get('connection') != operation['connection']['id']
    ):
        raise ValueError('Retained operation and connection attribution disagree')
    ref = attribution.get('evidence')
    if ref and attribution.get('basis') == 'retained catalogue row':
        row = json.loads(read(ref))['record']
        if row.get('run') != manifest['run'] or row.get('connection') != attribution['connection']:
            raise ValueError('Retained catalogue and attribution disagree')
    if ref and attribution.get('basis') == 'retained packet metadata':
        if json.loads(read(ref)).get('connection') != attribution['connection']:
            raise ValueError('Retained packet metadata and attribution disagree')


def verify_package(path, expected_sha256=None):
    path = files.regular(Path(path))
    data = path.read_bytes()
    return inspect_package(data, expected_sha256)


def inspect_package(data, expected_sha256=None):
    """Verify exactly the bytes the consumer will use, without reopening a mutable source path."""
    digest = files.digest(data)
    if expected_sha256 is not None and digest != expected_sha256:
        raise ValueError('Recording package differs from its trusted digest')
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if len(archive.namelist()) != len(set(archive.namelist())):
            raise ValueError('Recording contains duplicate members')
        manifest = json.loads(archive.read(MANIFEST))
        members = {info.filename: info.external_attr >> 16 for info in archive.infolist()}
        verify_members(manifest, members, archive.read)
    return {
        'schema': 'berman.recording-verification/1',
        'integrity': 'passed',
        'manifest': manifest,
        'manifest_sha256': identity(manifest),
        'package_sha256': digest,
        'bytes': len(data),
        'coverage': manifest['coverage'],
        'collection_id': manifest['collection_id'],
        'run': manifest['run'],
        'meaning': 'Recorded members and evidence agree. Integrity does not imply complete capture or an ending.',
    }


def verify_directory(root):
    root = Path(root)
    manifest = files.read(root / MANIFEST)
    members = {}
    for row in files.inventory(root):
        name = row['path'] + ('/' if row['type'] == 'directory' else '')
        members[name] = mode(row)

    def read(name):
        path = root / name
        if name.endswith('/'):
            return b''
        return os.fsencode(os.readlink(path)) if path.is_symlink() else files.regular(path).read_bytes()

    verify_members(manifest, members, read)
    return {
        'integrity': 'passed',
        'manifest': manifest,
        'manifest_sha256': identity(manifest),
        'coverage': manifest['coverage'],
        'collection_id': manifest['collection_id'],
        'run': manifest['run'],
    }


def materialize(source, destination, expected_sha256=None):
    """Create a new disposable view; never overwrite or follow preserved symlink targets."""
    data = files.regular(Path(source)).read_bytes()
    report = inspect_package(data, expected_sha256)
    destination = Path(destination).absolute()
    if destination.exists() or destination.is_symlink():
        raise ValueError('Materialization destination must be new')
    files.safe_directory(destination.parent)
    # Keep links as links only when they name contained, existing material. Historical
    # external/parent-traversing links remain readable as bytes in the ZIP, never followed.
    links = [item for item in report['manifest']['entries'] if item['type'] == 'symlink']
    for item in links:
        target = PurePosixPath(item['target'])
        if target.is_absolute() or '..' in target.parts or not item['target'] or '\\' in item['target']:
            raise ValueError('Materialization refuses external or parent-traversing links; read the preserved package')
    stage = Path(tempfile.mkdtemp(prefix='.recording-view-', dir=destination.parent))
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = sorted(archive.infolist(), key=lambda item: (not item.is_dir(), item.filename))
            for info in infos:
                name = relative(info.filename.rstrip('/'))
                path = stage / name
                if stat.S_ISLNK(info.external_attr >> 16):
                    continue
                if info.is_dir():
                    path.mkdir(parents=True, exist_ok=True)
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(archive.read(info))
            for item in links:
                (stage / item['path']).symlink_to(item['target'])
            for item in links:
                resolved = (stage / item['path']).resolve(strict=True)
                if not resolved.is_relative_to(stage):
                    raise ValueError('Materialized link escapes the recording')
            # Apply directory permissions last, after their children have been written.
            for info in reversed(infos):
                if stat.S_ISLNK(info.external_attr >> 16):
                    continue
                (stage / info.filename.rstrip('/')).chmod(stat.S_IMODE(info.external_attr >> 16))
        verify_directory(stage)
        stage.rename(destination)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return dict(
        report,
        destination=str(destination),
        meaning='Verified working copy; recorded timestamps remain in recording.json.',
    )
