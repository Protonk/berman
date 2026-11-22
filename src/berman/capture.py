"""Attribute callbacks and transcript bytes to the actor in one open episode.

Installed callbacks run this module by name (python -m berman.capture), so its module path is
part of every reviewed Codex hooks.json.
"""

import json
import os
from pathlib import Path
import re
import shlex
import sys
from uuid import uuid4

from berman import claude, codex, files, snapshots

HARNESSES = {'codex': codex, 'claude': claude}
IDENTITY = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}')


def find_parent(home, harness, session):
    """Closeout fallback for absent or relocated callback paths, bounded by native parent identity."""
    match = None
    for count, path in enumerate(harness.candidates(home, session), 1):
        if count > 50000:
            raise ValueError('Parent source discovery exceeds candidate limit')
        if not harness.owns(path, session):
            continue
        if match is not None:
            raise ValueError('Multiple native sources claim the bound parent identity')
        match = path
    if match is None:
        raise ValueError('No native transcript found for the bound parent')
    return match


def observe(folder, state, session, event):
    """Publish a verified transcript generation for the episode's bound parent."""
    harness = HARNESSES[state['actor']]
    home = Path(state['home'])
    previous = snapshots.read(folder / 'session')
    old = previous['metadata'] if previous else {}
    path = Path(event.get('transcript_path') or old.get('source_transcript') or '')
    if event['hook_event_name'] == 'closeout' and (path == Path('.') or not path.exists()):
        path = find_parent(home, harness, session)
    if '..' in path.parts or not any(path.is_relative_to(home / root) for root in harness.STORES):
        raise ValueError('No transcript path in the assigned native session store')
    data = files.stable_bytes(path, files.FILE_LIMIT)
    end = data.rfind(b'\n') + 1
    raw, tail = data[:end], data[end:]
    if not raw:
        raise ValueError('No complete transcript record')
    reader, messages, malformed = harness.transcript(raw, path, session)

    expected = event.get('last_assistant_message')
    expected_hash = (
        files.digest(expected.encode())
        if isinstance(expected, str) and expected
        else old.get('expected_last_assistant_sha256')
    )
    record = {
        'schema': 'berman.session-capture/2',
        'harness': state['actor'],
        'format': state['actor'] + '-jsonl/1',
        'episode': state['id'],
        'session_id': session,
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


def hook_settings(name):
    command = shlex.join([sys.executable, '-I', '-B', '-m', 'berman.capture', name])
    return {
        'hooks': {
            event: [{'hooks': [{'type': 'command', 'command': command, 'timeout': timeout}]}]
            for event, timeout in HARNESSES[name].EVENTS.items()
        }
    }


def record(folder, name, event):
    """Only a launched episode can bind its opening. A different or closed session writes nothing."""
    if not isinstance(event, dict):
        raise ValueError('Expected a hook event')
    harness = HARNESSES[name]
    sid = event.get('session_id')
    if not isinstance(sid, str) or not IDENTITY.fullmatch(sid) or event.get('hook_event_name') not in harness.EVENTS:
        return
    with files.lock(folder, '.capture.lock'):
        state = files.read(folder / 'episode.json')
        if state['actor'] != name or (folder / 'ending.json').exists() or not (folder / 'sittings').is_dir():
            return
        bound = folder / 'session-id.json'
        if bound.exists():
            if files.read(bound) != sid:
                return
        elif not harness.binds(state, event):
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
