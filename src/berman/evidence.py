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
    return str(uuid.UUID(files.read(Path(root) / 'collection.json')['id']))


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
    if manifest.get('profile') != 'split':
        raise ValueError(f"Unsupported recording profile {manifest.get('profile')!r}; expected split")
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
    for fact, ref in evidence.items():
        if ref is not None and (not isinstance(ref, str) or ref not in seen or seen[ref]['type'] != 'file'):
            raise ValueError(f'Recording {fact} refers to missing evidence')
    coverage = manifest.get('coverage')
    if not isinstance(coverage, dict) or not isinstance(coverage.get('unknown'), list):
        raise ValueError('Recording needs an explicit coverage report')
    if not all(isinstance(value, str) and value for value in coverage['unknown']):
        raise ValueError('Invalid coverage limitation')
    ref = evidence.get('controller')
    if ref != 'evidence/controller.json' or ref not in seen or seen[ref]['type'] != 'file':
        raise ValueError('Split recording requires retained controller evidence')
    if evidence.get('ending') != 'evidence/ending.json':
        raise ValueError('Split recording requires retained ending evidence')
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


PARENT_GAP = 'The parent transcript generation was observed on the host but is not complete in custody. It is not verified here.'


def missing_line(row):
    """The one coverage statement for a member observed on the host and absent from custody."""
    return f'Observed on the host but not accepted into custody: {row["path"]} ({row["problem"]})'


def split_part(manifest, controller):
    """The capture, acceptance, transfer map and declaration this split recording packages.

    A first recording packages the run's own facts. A supplement packages the one supplement
    the controller record carries. Which of the two a package is comes from its manifest, and
    the retained records must then all say the same.
    """
    supplement = manifest.get('supplement')
    if supplement is None:
        if 'supplement' in controller or manifest['evidence'].get('supplement') is not None:
            raise ValueError('Controller record carries a supplement the recording does not name')
        return controller['facts'], controller['transferred'], None
    item = controller.get('supplement')
    if (not isinstance(supplement, dict) or not isinstance(item, dict) or type(supplement.get('sequence')) is not int
            or supplement['sequence'] < 1 or item.get('sequence') != supplement['sequence']
            or not isinstance(item.get('declaration'), dict) or not isinstance(item.get('facts'), dict)
            or not SHA.fullmatch(supplement.get('follows_recording_sha256', ''))):
        raise ValueError('Split supplement and controller record disagree')
    return item['facts'], item['transferred'], item['declaration']


def split_absent(manifest, controller):
    """Members the acceptance names as missing, by path. Their shape is checked with the authority."""
    rows = split_part(manifest, controller)[0]['accept'].get('missing', [])
    if not isinstance(rows, list) or any(not isinstance(row, dict) or not isinstance(row.get('path'), str) for row in rows):
        raise ValueError('Split acceptance lists missing evidence in an unknown form')
    return {row['path']: row for row in rows}


def check_split_authority(manifest, controller, ending, capture, read=None):
    if (controller.get('collection_id') != manifest['collection_id'] or controller.get('run') != manifest['run']
            or controller.get('connection') != manifest['attribution']['connection']
            or manifest['attribution']['evidence'] != manifest['evidence']['controller']):
        raise ValueError('Split controller record and recording occurrence disagree')
    facts = controller['facts']
    declared = facts.get('ending')
    if not isinstance(declared, dict) or not ending or ending != dict(declared, declared_at_utc=declared.get('at')):
        raise ValueError('Retained controller and ending declaration disagree')
    assignment = controller['assignment']
    if any(assignment.get(key) != controller[key] for key in ('run', 'collection_id', 'connection')):
        raise ValueError('Split assignment names another occurrence')
    part, transferred, declaration = split_part(manifest, controller)
    supplement = manifest.get('supplement')
    sittings = controller.get('sittings', {})
    if not isinstance(sittings, dict):
        raise ValueError('Split sitting receipts are not a mapping')
    for observation in (part['capture'], facts['capture'], facts.get('preparation'), facts.get('launch'), *sittings.values()):
        if observation is None:
            continue
        if (any(observation.get(key) != assignment.get(key) for key in ('run', 'collection_id', 'operation', 'connection', 'harness'))
                or observation.get('assignment_sha256') != files.digest_json(assignment)):
            raise ValueError('Split observation names another assignment')
    capture_receipt = part['capture']
    if not declared.get('operation') or capture_receipt.get('ending_id') != declared['operation']:
        raise ValueError('Split capture closeout names another ending')
    if supplement is None:
        # A collection made for a supplement says so in its own receipt, whatever wraps it.
        if 'supplement_id' in capture_receipt or 'sequence' in capture_receipt:
            raise ValueError('A first recording cannot package a supplement collection')
    else:
        ref = manifest['evidence'].get('supplement')
        first = facts.get('preserve')
        if (ref != 'evidence/supplement.json' or read is None or json.loads(read(ref)) != declaration
                or not declaration.get('reason') or not declaration.get('operation')
                or declaration.get('sequence') != supplement['sequence']
                or capture_receipt.get('supplement_id') != declaration['operation']
                or capture_receipt.get('sequence') != supplement['sequence']
                or not isinstance(first, dict) or first.get('package_sha256') != supplement['follows_recording_sha256']):
            raise ValueError('Split supplement declaration, capture and first recording disagree')
    absent = split_absent(manifest, controller)
    lines = manifest['coverage']['unknown']
    if any(path.startswith('session/') for path in absent):
        # The parent generation cannot be checked from an incomplete set of its files. The
        # recording must then make no claim about it and must say so.
        if capture is not None or manifest['evidence'].get('capture') is not None or PARENT_GAP not in lines:
            raise ValueError('Split recording claims a parent transcript that is incomplete in custody')
    else:
        if capture_receipt.get('parent_sha256') != (capture and capture['sha256']):
            raise ValueError('Split parent transcript differs from native closeout')
        if capture and (capture_receipt.get('session_id') != capture.get('session_id')
                        or capture.get('harness') != assignment.get('harness')):
            raise ValueError('Split parent transcript attribution differs from native closeout')
    limits = capture_receipt.get('limitations')
    if not isinstance(limits, list) or any(limit not in lines for limit in limits):
        raise ValueError('Split recording omits native coverage limitations')
    rows = capture_receipt['members']
    inventory = {row['path']: (row['size'], row['sha256']) for row in rows}
    members = {row['path']: (row['size'], row['sha256']) for row in manifest['custody']['inventory']['entries']
               if row['type'] == 'file'}
    accepted = part['accept']
    # Evidence observed on the host and never accepted stays named: once in the acceptance,
    # with its length, digest and reason, and by exactly that statement in coverage.
    if ((absent and not accepted.get('missing_reason')) or len(absent) != len(accepted.get('missing', []))
            or any(path not in inventory or inventory[path] != (row.get('size'), row.get('sha256'))
                   or not isinstance(row.get('problem'), str) or missing_line(row) not in lines
                   or (row['problem'] == 'never transferred' and transferred.get(path) == row['sha256'])
                   for path, row in absent.items())):
        raise ValueError('Split recording does not account for evidence missing at acceptance')
    expected = {key: value for key, value in inventory.items() if key not in absent}
    fingerprint = files.digest(json.dumps(rows, sort_keys=True, separators=(',', ':'), allow_nan=False).encode())
    if (members != expected or len(inventory) != len(rows) or accepted.get('inventory_sha256') != fingerprint
            or accepted.get('accepted_members') != len(expected)
            or any(transferred.get(key) != value[1] for key, value in expected.items())
            or (not absent and transferred != {key: value[1] for key, value in inventory.items()})):
        raise ValueError('Recorded observations, transfers, acceptance and preserved members disagree')


def committed_capture(run, read):
    session = f'runs/{run}/session'
    pointer = json.loads(read(f'{session}/current.json'))
    generation = pointer['generation']
    if not isinstance(generation, str) or not SHA.fullmatch(generation):
        raise ValueError('Invalid capture generation')
    ref = f'{session}/snapshots/{generation}/capture.json'
    capture = json.loads(read(ref))
    if files.digest_json(capture) != generation:
        raise ValueError('Native capture pointer disagrees with generation')
    return ref, capture


def primary_capture(manifest, controller, read):
    """Return the primary generation established by this collection, including early split pointers."""
    capture_ref = manifest['evidence'].get('capture')
    capture = json.loads(read(capture_ref)) if capture_ref else None
    parent_gap = any(path.startswith('session/') for path in split_absent(manifest, controller))
    if split_part(manifest, controller)[0]['capture'].get('parent_sha256') and not parent_gap:
        ref, capture = committed_capture(manifest['run'], read)
        if capture_ref is not None and capture_ref != ref:
            raise ValueError('Split manifest and native pointer select different captures')
        capture_ref = ref
    return capture_ref, capture


def check_authority(manifest, read):
    """Check the facts named in the manifest against the retained records and transcript bytes."""
    refs = manifest['evidence']
    records = {key: json.loads(read(ref)) for key, ref in refs.items() if ref is not None}
    for key, record in records.items():
        if not isinstance(record, dict):
            raise ValueError(f'Retained {key} must be an object')
    ending, controller = records['ending'], records['controller']
    capture_ref, capture = primary_capture(manifest, controller, read)
    check_split_authority(manifest, controller, ending, capture, read)
    if not ending.get('reason') or not ending.get('declared_at_utc'):
        raise ValueError('Retained ending is not a declaration')
    if capture is not None and capture.get('run') != manifest['run']:
        raise ValueError('Retained capture names another run')
    if capture is not None:
        folder = PurePosixPath(capture_ref).parent
        raw = read(str(folder / 'transcript.jsonl'))
        if len(raw) != capture['saved_bytes'] or files.digest(raw) != capture['sha256']:
            raise ValueError('Retained capture and transcript bytes disagree')
        if capture.get('reader_sha256') and files.digest(read(str(folder / 'conversation.txt'))) != capture['reader_sha256']:
            raise ValueError('Retained capture and reader disagree')
        history = read(str(folder / 'captures.jsonl')).splitlines()
        if not history or json.loads(history[-1]) != capture:
            raise ValueError('Retained capture and history disagree')


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
        'meaning': 'The recorded members agree with the controller receipts and ending. Coverage lists recorded gaps.',
    }


def verify_directory(root):
    root = Path(root)
    manifest = validate(files.read(root / MANIFEST))
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
                link = stage / item['path']
                link.symlink_to(item['target'])
                permissions = int(item['mode'], 0)
                if stat.S_IMODE(link.lstat().st_mode) != permissions:
                    if os.chmod not in os.supports_follow_symlinks:
                        raise ValueError(f"Cannot materialize recorded link permissions: {item['path']}")
                    link.chmod(permissions, follow_symlinks=False)
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
