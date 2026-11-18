"""Offline hook routing and staged parent transcripts. Only assigned native sessions reach disk.

Staging stays open for the whole engagement: a callback from the bound session is kept whether
or not the process that first launched it is still running. After the first collection freezes,
callbacks go to the `late` area, which the next supplement collects. Nothing is dropped silently
once a session is bound to an operation.
"""

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import shlex
from uuid import uuid4

from berman import files, protocol, snapshots
from berman.capture import claude, collector
from berman.installation import command
from berman.sources import IDENTITY, metadata

MARKER = 'Berman-Assignment: '
EVENTS = {'codex': ('UserPromptSubmit', 'Stop', 'Interrupt', 'SessionEnd'), 'claude': claude.EVENTS}
MAX_SOURCE = protocol.FILE_LIMIT
MAX_EVENT = 4 * 1024 * 1024


def operation_dir(spool, operation):
    return Path(spool).expanduser().resolve() / 'operations' / protocol.identifier(operation)


def sitting_files(folder, number):
    """One native process per sitting. Operations launched before sittings existed keep their names."""
    if number == 1 and (folder / 'launch-intent.json').exists():
        return {
            'intent': folder / 'launch-intent.json',
            'started': folder / 'started.json',
            'exit': folder / 'exit.json',
        }
    base = folder / 'sittings' / f'{number:04d}'
    return {'intent': base / 'intent.json', 'started': base / 'started.json', 'exit': base / 'exit.json'}


def sittings(folder):
    """Numbers of the sittings whose launch intent was recorded, in order."""
    found = {int(p.parent.name) for p in (folder / 'sittings').glob('[0-9][0-9][0-9][0-9]/intent.json')}
    if (folder / 'launch-intent.json').exists():
        found.add(1)
    return sorted(found)


def open_area(folder):
    """Where a callback is staged now: the operation itself until its collection freezes, then `late`."""
    return folder / 'late' if (folder / 'collection').exists() else folder


@contextmanager
def locked(directory, name='.lock'):
    files.safe_directory(directory)
    with (directory / name).open('a+b') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def definition(spool, home, operation=None):
    argv = command('--spool', Path(spool).resolve(), 'hook', '--home', Path(home).resolve(), module='berman.executor')
    harness = 'claude' if operation else 'codex'
    if operation:
        argv += ['--operation', protocol.identifier(operation)]
    return {
        'hooks': {
            name: [{'hooks': [{'type': 'command', 'command': shlex.join(argv), 'timeout': 30}]}]
            for name in EVENTS[harness]
        }
    }


def stable_bytes(path, limit):
    before = files.regular(path).stat()
    if before.st_size > limit:
        raise ValueError(f'File exceeds {limit} bytes: {path}')
    with path.open('rb') as stream:
        raw = stream.read(before.st_size + 1)
    after = path.stat()
    if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns) or len(
        raw
    ) != after.st_size:
        raise ValueError(f'Source changed during observation: {path}')
    return raw


def sync_directory(directory):
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def find_parent(home, harness, sid):
    """Closeout fallback for absent or relocated callback paths, bounded by native parent identity."""
    if harness == 'claude':
        candidates = (home / 'projects').glob(f'*/{sid}.jsonl')
    else:
        candidates = (p for part in ('sessions', 'archived_sessions') for p in (home / part).rglob(f'*{sid}*.jsonl'))
    match = None
    for count, path in enumerate(candidates, 1):
        if count > 50000:
            raise ValueError('Parent source discovery exceeds candidate limit')
        if harness == 'codex' and (metadata(path) or {}).get('session_id') != sid:
            continue
        if match is not None:
            raise ValueError('Multiple native sources claim the bound parent identity')
        match = path
    if match is None:
        raise ValueError('No native transcript found for the bound parent')
    return match


def observe(folder, preparation, session_id, event, known=None):
    """Publish one verified native source using the existing committed-generation format.

    `folder` is the staging area that receives the generation. `known` is a source path already
    verified for this parent in another area, used when the callback names none.
    """
    harness = preparation['harness']
    home = Path(preparation['binding']['home'])
    previous = snapshots.read(folder / 'session')
    old = previous['metadata'] if previous else {}
    path = Path(event.get('transcript_path') or old.get('source_transcript') or known or '')
    roots = ('projects',) if harness == 'claude' else ('sessions', 'archived_sessions')
    if event['hook_event_name'] == 'closeout' and (path == Path('.') or not path.exists()):
        path = find_parent(home, harness, session_id)
    if '..' in path.parts or not any(path.is_relative_to(home / root) for root in roots):
        raise ValueError('No transcript path in the assigned native session store')
    data = stable_bytes(path, MAX_SOURCE)
    end = data.rfind(b'\n') + 1
    raw, tail = data[:end], data[end:]
    if not raw:
        raise ValueError('No complete transcript record')
    if harness == 'claude':
        reader, messages, malformed, ids = claude.render(raw)
        if path.name != session_id + '.jsonl' or ids != {session_id}:
            raise ValueError('Claude transcript identity differs from the assigned parent')
    else:
        first = json.loads(raw.partition(b'\n')[0])
        meta = first.get('payload') if isinstance(first, dict) and first.get('type') == 'session_meta' else None
        if not isinstance(meta, dict) or (meta.get('id') or meta.get('session_id')) != session_id:
            raise ValueError('Codex transcript metadata differs from the assigned parent')
        reader, messages, malformed = collector.capture.render(raw)

    expected = event.get('last_assistant_message')
    expected_hash = (
        files.digest(expected.encode())
        if isinstance(expected, str) and expected
        else old.get('expected_last_assistant_sha256')
    )
    record = {
        'schema': 'berman.session-capture/2',
        'harness': harness,
        'format': harness + '-jsonl/1',
        'run': preparation['run'],
        'session_id': session_id,
        'source_transcript': str(path),
        'captured_at_utc': files.now(),
        'event': event['hook_event_name'],
        'turn_id': event.get('turn_id'),
        'source_size_at_read': len(data),
        'saved_bytes': len(raw),
        'sha256': files.digest(raw),
        'reader_sha256': files.digest(reader),
        'previous_snapshot_is_prefix': raw.startswith(previous['raw']) if previous else None,
        'partial_tail_file': 'partial-tail-' + files.digest(tail) + '.bin' if tail else None,
        'partial_tail_sha256': files.digest(tail) if tail else None,
        'omitted_partial_tail_bytes': len(tail),
        'unparsed_jsonl_lines': malformed,
        'expected_last_assistant_sha256': expected_hash,
        'last_assistant_message_present': any(files.digest(m.encode()) == expected_hash for m in messages)
        if expected_hash
        else None,
        'source_identity': 'Native session identity verified against the bound parent.',
    }
    return snapshots.publish(folder / 'session', record, raw, reader, tail, folder / 'scratch')


def known_source(folder):
    """The parent source path last verified in the first staging area, if any."""
    try:
        latest = snapshots.read(folder / 'session')
    except OSError, ValueError, KeyError, TypeError:
        return None
    return latest['metadata'].get('source_transcript') if latest else None


def route(spool, home, event, operation=None):
    """Claude reserves a UUID. Codex binds once to the exact prepared opening prompt."""
    if not isinstance(event, dict):
        raise ValueError('Expected a native callback object')
    sid, name = event.get('session_id'), event.get('hook_event_name')
    harness = 'claude' if operation else 'codex'
    if not isinstance(sid, str) or not IDENTITY.fullmatch(sid) or name not in EVENTS[harness]:
        return {}
    spool, home = Path(spool).resolve(), Path(home).resolve()
    registry = spool / 'sessions' / files.digest(str(home).encode()) / sid
    if not operation:
        if registry.exists():
            operation = files.read(registry)['operation']
        else:
            prompt = event.get('prompt')
            if name != 'UserPromptSubmit' or not isinstance(prompt, str) or not prompt.startswith(MARKER):
                return {}
            operation = prompt.splitlines()[0][len(MARKER) :]
            try:
                protocol.identifier(operation)
            except ValueError:
                return {}
    folder = operation_dir(spool, operation)
    if not folder.is_dir() or not sittings(folder):
        return {}
    with locked(folder):
        prepared = files.read(folder / 'preparation.json')
        if prepared['harness'] != harness or str(home) != prepared['binding']['home']:
            return {}
        if harness == 'claude':
            if sid != prepared['reserved_session_id']:
                return {}
        elif not registry.exists():
            if (
                event.get('prompt') != prepared['prompt']
                or name != 'UserPromptSubmit'
                or not event.get('cwd')
                or Path(event['cwd']).resolve() != Path(prepared['binding']['project'])
            ):
                return {}
            existing = folder / 'session-id.json'
            if existing.exists() and files.read(existing) != sid:
                return {}
            registry.parent.mkdir(parents=True, exist_ok=True)
            with locked(registry.parent):
                files.keep(registry, files.json_bytes({'operation': operation}))
        elif files.read(registry)['operation'] != operation:
            return {}
        files.keep(folder / 'session-id.json', files.json_bytes(sid))
        # The engagement, not the launching process, bounds observation. A resumed parent keeps
        # staging here; after a frozen collection its callbacks wait in `late` for a supplement.
        area = open_area(folder)
        callback = {'observed_at_utc': files.now(), 'event': event}
        target = area / 'events' / (uuid4().hex + '.json')
        files.write(target, callback)
        try:
            observed = observe(area, prepared, sid, event, known=known_source(folder))
            callback['generation'] = files.digest_json(observed)
        except (OSError, ValueError) as exc:
            callback['limitation'] = str(exc)
        files.write(target, callback)
        sync_directory(target.parent)
    return {}
