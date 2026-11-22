"""Attribute callbacks and transcript bytes to the actor in one open episode."""

import json
import os
from pathlib import Path
import re
import shlex
import sys
from uuid import uuid4
from berman import claude, codex, files, snapshots

IDENTITY = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}')
FILE_LIMIT = 256 * 1024 * 1024
EVENTS = {'claude': claude.EVENTS, 'codex': ('UserPromptSubmit', 'Stop', 'Interrupt', 'SessionEnd')}


def codex_session_id(path):
    with files.regular(path).open('rb') as stream:
        line = stream.readline(128 * 1024 + 1)
    if len(line) > 128 * 1024:
        return None
    try:
        value = json.loads(line)
    except (ValueError, UnicodeError):
        return None
    if not isinstance(value, dict) or value.get('type') != 'session_meta':
        return None
    value = value.get('payload')
    if not isinstance(value, dict):
        return None
    ident = value.get('id') or value.get('session_id')
    return ident if isinstance(ident, str) and IDENTITY.fullmatch(ident) else None


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
        if harness == 'codex' and codex_session_id(path) != sid:
            continue
        if match is not None:
            raise ValueError('Multiple native sources claim the bound parent identity')
        match = path
    if match is None:
        raise ValueError('No native transcript found for the bound parent')
    return match


def observe(folder, preparation, session_id, event):
    """Publish a verified transcript generation for the episode's bound parent."""
    harness = preparation['actor']
    home = Path(preparation['home'])
    previous = snapshots.read(folder / 'session')
    old = previous['metadata'] if previous else {}
    path = Path(event.get('transcript_path') or old.get('source_transcript') or '')
    roots = ('projects',) if harness == 'claude' else ('sessions', 'archived_sessions')
    if event['hook_event_name'] == 'closeout' and (path == Path('.') or not path.exists()):
        path = find_parent(home, harness, session_id)
    if '..' in path.parts or not any(path.is_relative_to(home / root) for root in roots):
        raise ValueError('No transcript path in the assigned native session store')
    data = stable_bytes(path, FILE_LIMIT)
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
        reader, messages, malformed = codex.render(raw)

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
        'episode': preparation['id'],
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


def hook_settings(harness):
    command = shlex.join([sys.executable, '-I', '-B', '-m', 'berman.capture', harness])
    return {
        'hooks': {
            event: [
                {
                    'hooks': [
                        {
                            'type': 'command',
                            'command': command,
                            'timeout': 3 if harness == 'codex' and event in ('Interrupt', 'SessionEnd') else 30,
                        }
                    ]
                }
            ]
            for event in EVENTS[harness]
        }
    }


def record(folder, harness, event):
    """Only a launched episode can bind its opening. A different or closed session writes nothing."""
    if not isinstance(event, dict):
        raise ValueError('Expected a hook event')
    sid = event.get('session_id')
    if not isinstance(sid, str) or not IDENTITY.fullmatch(sid) or event.get('hook_event_name') not in EVENTS[harness]:
        return
    with files.lock(folder, '.capture.lock'):
        state = files.read(folder / 'episode.json')
        if state['actor'] != harness or (folder / 'ending.json').exists() or not (folder / 'sittings').is_dir():
            return
        bound = folder / 'session-id.json'
        if harness == 'claude':
            if sid != state['id']:
                return
        elif not bound.exists():
            if (
                event['hook_event_name'] != 'UserPromptSubmit'
                or event.get('prompt') != state['opening']
                or not event.get('cwd')
                or Path(event['cwd']).resolve() != Path(state['project'])
            ):
                return
        if bound.exists() and files.read(bound) != sid:
            return
        files.keep(bound, files.json_bytes(sid))
        area = folder / 'telemetry'
        row = {'observed_at': files.now(), 'event': event}
        path = area / 'events' / (uuid4().hex + '.json')
        files.write(path, row)
        try:
            result = observe(area, state, sid, event)
            row['generation'] = files.digest_json(result)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            row['limitation'] = str(exc)
        files.write(path, row)


def main():
    # The ordinary harness path has no episode, even when a user has installed the Codex hook.
    folder = os.environ.get('BERMAN_EPISODE')
    if folder:
        raw = sys.stdin.buffer.read(4 * 1024 * 1024 + 1)
        if len(raw) > 4 * 1024 * 1024:
            raise ValueError('Callback exceeds 4 MiB')
        record(Path(folder), sys.argv[1], json.loads(raw))
    print('{}')


if __name__ == '__main__':
    main()
