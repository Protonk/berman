"""Committed capture generations, shared by hook writers and read-only consumers.

Only current.json publishes a generation. Staging is outside the packet. Superseded
prefix bytes remain represented by the current transcript; non-prefix source revisions
and incomplete tails are retained explicitly. Compaction never touches a frozen capture.
"""

import json
import os
from pathlib import Path
import re
import shutil
import tempfile

from berman import files

SCHEMA = 'berman.capture-current/1'
ID = re.compile(r'[0-9a-f]{64}\Z')


def pointer(session):
    path = Path(session) / 'current.json'
    if not path.exists() and not path.is_symlink():
        return None
    value = files.read(path)
    if (
        not isinstance(value, dict)
        or value.get('schema') != SCHEMA
        or not isinstance(value.get('generation'), str)
        or not ID.fullmatch(value['generation'])
        or not isinstance(value.get('committed'), list)
        or any(not isinstance(item, str) or not ID.fullmatch(item) for item in value['committed'])
        or value['generation'] not in value['committed']
    ):
        raise ValueError(f'Invalid capture commit reference: {path}')
    return value


def retained(path, digest, size, checked):
    """Verify a retained byte obligation once per read, including repeated history rows."""
    if not isinstance(digest, str) or not ID.fullmatch(digest):
        raise ValueError('Invalid retained capture digest')
    if size is not None and (type(size) is not int or size < 0):
        raise ValueError('Invalid retained capture size')
    if path not in checked:
        data = files.regular(path).read_bytes()
        checked[path] = (files.digest(data), len(data))
    observed, length = checked[path]
    if observed != digest or (size is not None and length != size):
        raise ValueError(f'Retained capture bytes differ: {path}')


def obligations(session, rows, tail):
    """Check non-prefix predecessors and prior tails without rehashing every growing prefix."""
    checked = {}
    for previous, successor in zip(rows, rows[1:], strict=False):
        prefix = successor.get('previous_snapshot_is_prefix')
        if type(prefix) is not bool:
            raise ValueError('Capture history lacks an explicit prefix relationship')
        if not prefix:
            retained(
                session / 'displaced' / (previous['sha256'] + '.jsonl'),
                previous['sha256'],
                previous['saved_bytes'],
                checked,
            )
            retained(
                session / 'displaced' / (previous['reader_sha256'] + '.txt'), previous['reader_sha256'], None, checked
            )
    current_tail = (files.digest(tail), len(tail))
    for row in rows[:-1]:
        if row.get('omitted_partial_tail_bytes'):
            expected = (row['partial_tail_sha256'], row['omitted_partial_tail_bytes'])
            if expected != current_tail:
                retained(session / 'tails' / (expected[0] + '.bin'), expected[0], expected[1], checked)


def read_generation(folder):
    metadata = files.read(folder / 'capture.json')
    raw = files.regular(folder / 'transcript.jsonl').read_bytes()
    reader = files.regular(folder / 'conversation.txt').read_bytes()
    history = files.regular(folder / 'captures.jsonl').read_bytes().splitlines()
    if (
        files.digest(raw) != metadata['sha256']
        or len(raw) != metadata['saved_bytes']
        or files.digest(reader) != metadata['reader_sha256']
        or not history
        or json.loads(history[-1]) != metadata
    ):
        raise ValueError('Capture bytes, reader, metadata or history disagree')
    tail = b''
    if name := metadata.get('partial_tail_file'):
        if not isinstance(name, str) or Path(name).name != name:
            raise ValueError('Invalid capture tail path')
        tail = files.regular(folder / name).read_bytes()
        if files.digest(tail) != metadata['partial_tail_sha256'] or len(tail) != metadata['omitted_partial_tail_bytes']:
            raise ValueError('Capture partial tail differs')
    return {'metadata': metadata, 'raw': raw, 'reader': reader, 'tail': tail, 'history': history}


def read(session):
    """Read a published generation, retrying if its writer advanced the pointer."""
    for _ in range(3):
        ref = pointer(session)
        if ref is None:
            return None
        try:
            current = read_generation(Path(session) / 'snapshots' / ref['generation'])
            rows = [json.loads(line) for line in current['history']]
            if (
                files.digest_json(current['metadata']) != ref['generation']
                or [files.digest_json(row) for row in rows] != ref['committed']
            ):
                raise ValueError('Capture commit reference and retained history disagree')
            obligations(Path(session), rows, current['tail'])
            if pointer(session) == ref:
                return current
        except (OSError, ValueError, KeyError, TypeError):
            if pointer(session) == ref:
                raise
    raise ValueError('Capture changed repeatedly while reading')


def immutable(path, data, staging):
    """Publish bytes without replacement, using an external same-filesystem staging file."""
    if path.exists() or path.is_symlink():
        if files.regular(path).read_bytes() != data:
            raise ValueError(f'Existing immutable capture member differs: {path}')
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    files.safe_directory(path.parent)
    fd, temporary = tempfile.mkstemp(prefix='write-', dir=staging)
    temporary = Path(temporary)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.link(temporary, path)
    except FileExistsError:
        if files.regular(path).read_bytes() != data:
            raise ValueError(f'Concurrent immutable capture member differs: {path}') from None
    temporary.unlink()


def recover(session, staging):
    """Retain unpublished generations; finish only already-authorized supersession cleanup."""
    current = pointer(session)
    captured = read(session) if current else None
    rows = {files.digest_json(json.loads(line)): json.loads(line) for line in captured['history']} if captured else {}
    committed = set(current['committed']) if current else set()
    folders = list((Path(session) / 'snapshots').glob('*'))
    checked = {}
    # Preflight every retirement before changing any generation. A partially removed old
    # directory still has its byte obligations recorded in the committed history.
    for folder in folders:
        if not ID.fullmatch(folder.name):
            raise ValueError(f'Unknown capture generation member: {folder}')
        files.safe_directory(folder)
        if folder.name in committed and folder.name != current['generation']:
            previous = rows[folder.name]
            prefix = captured['raw'][: previous['saved_bytes']]
            if len(prefix) != previous['saved_bytes'] or files.digest(prefix) != previous['sha256']:
                retained(
                    Path(session) / 'displaced' / (previous['sha256'] + '.jsonl'),
                    previous['sha256'],
                    previous['saved_bytes'],
                    checked,
                )
    for folder in folders:
        if current and folder.name == current['generation']:
            continue
        if folder.name in committed:
            # Published history and retained bytes were both checked before any deletion.
            shutil.rmtree(folder)
        else:
            destination = Path(staging) / ('unpublished-' + folder.name)
            if destination.exists():
                raise ValueError('Both unpublished capture recovery locations exist')
            os.rename(folder, destination)


def publish(session, metadata, raw, reader, tail, staging):
    session, staging = Path(session), Path(staging)
    session.mkdir(parents=True, exist_ok=True)
    staging.mkdir(parents=True, exist_ok=True)
    files.safe_directory(session)
    files.safe_directory(staging)
    if session.stat().st_dev != staging.stat().st_dev:
        raise ValueError('Capture staging and packet must share a filesystem')
    recover(session, staging)
    previous = read(session)
    current = pointer(session)
    expected_prefix = raw.startswith(previous['raw']) if previous else None
    if metadata.get('previous_snapshot_is_prefix') is not expected_prefix:
        raise ValueError('Capture prefix relationship differs from the observed previous bytes')
    if previous:
        if not raw.startswith(previous['raw']):
            immutable(session / 'displaced' / (files.digest(previous['raw']) + '.jsonl'), previous['raw'], staging)
            immutable(session / 'displaced' / (files.digest(previous['reader']) + '.txt'), previous['reader'], staging)
        if previous['tail']:
            immutable(session / 'tails' / (files.digest(previous['tail']) + '.bin'), previous['tail'], staging)
    generation = files.digest_json(metadata)
    if current and generation == current['generation']:
        return metadata
    if tail and (
        not isinstance(metadata.get('partial_tail_file'), str)
        or Path(metadata['partial_tail_file']).name != metadata['partial_tail_file']
    ):
        raise ValueError('Invalid capture tail path')
    history = (previous['history'] if previous else []) + [json.dumps(metadata, ensure_ascii=False).encode()]
    stage = Path(tempfile.mkdtemp(prefix='generation-', dir=staging))
    content = {
        'capture.json': files.json_bytes(metadata),
        'transcript.jsonl': raw,
        'conversation.txt': reader,
        'captures.jsonl': b'\n'.join(history) + b'\n',
    }
    if tail:
        content[metadata['partial_tail_file']] = tail
    for name, data in content.items():
        files.atomic(stage / name, data)
    read_generation(stage)  # Check staged bytes before publishing their pointer.
    target = session / 'snapshots' / generation
    target.parent.mkdir(exist_ok=True)
    files.safe_directory(target.parent)
    if target.exists():
        raise ValueError('Capture generation identity already exists without publication')
    stage.rename(target)
    value = {
        'schema': SCHEMA,
        'generation': generation,
        'committed': [*(current['committed'] if current else []), generation],
        'retention': 'Superseded prefix bytes remain in the current transcript; non-prefix revisions and tails are retained.',
    }
    # A single replace changes visibility of raw bytes, reader, metadata and history together.
    fd, temporary = tempfile.mkstemp(prefix='commit-', dir=staging)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(files.json_bytes(value))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, session / 'current.json')
    recover(session, staging)
    return metadata
