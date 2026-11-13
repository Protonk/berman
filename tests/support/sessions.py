"""Simulated Codex sessions for prepared runs: a transcript and hook callbacks, with no harness or model.

The callbacks enter the same routed collector a real hook reaches. Nothing here establishes hook
trust or native capture.
"""

import json

from berman.capture.collector import MARKER


def transcript(store, sid, reply='done'):
    source = store.home / 'sessions' / (sid + '.jsonl')
    source.parent.mkdir(parents=True, exist_ok=True)
    records = [
        {'type': 'session_meta', 'payload': {'id': sid}},
        {'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant', 'content': [{'text': reply}]}},
    ]
    source.write_text(''.join(json.dumps(row) + '\n' for row in records))
    return source


def callback(store, run, name, sid=None, **fields):
    """Deliver one callback for run's session; UserPromptSubmit carries the run's opening marker."""
    sid = sid or 'fixture-' + run
    event = {
        'session_id': sid,
        'transcript_path': str(transcript(store, sid)),
        'turn_id': 'one',
        'hook_event_name': name,
    }
    if name == 'UserPromptSubmit':
        event['prompt'] = MARKER + run
    return store.collector.hook(dict(event, **fields))


def start(store, run, stop=True, end=False):
    """Register a session with a prepared run, then optionally deliver Stop and SessionEnd."""
    callback(store, run, 'UserPromptSubmit')
    if stop:
        callback(store, run, 'Stop')
    if end:
        callback(store, run, 'SessionEnd')
