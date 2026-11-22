#!/usr/bin/env python3
"""A scripted harness used to exercise the installed command without a model.

It answers each message with a numbered reply in its native transcript and runs the episode
callbacks. In Claude print mode or codex exec it reports its result on stdout. Environment
variables named FIXTURE_* select the failures a test needs.
"""

import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time
from uuid import uuid4

args = sys.argv[1:]
actor = Path(sys.argv[0]).name
project = Path.cwd()
option = os.environ.get
home = Path(option('CODEX_HOME' if actor == 'codex' else 'CLAUDE_CONFIG_DIR'))
if log := option('FIXTURE_LOG'):
    with open(log, 'a') as stream:
        stream.write(json.dumps({'actor': actor, 'args': args, 'episode': option('BERMAN_EPISODE')}) + '\n')
(project / 'ordinary.txt').write_text('ordinary project work\n')

if 'BERMAN_EPISODE' not in os.environ:
    if option('FIXTURE_INDEPENDENT'):
        # Ordinary work in the same checkout and home leaves its own transcript and runs any home callbacks.
        sid = str(uuid4())
        if actor == 'codex':
            transcript = home / 'sessions' / (sid + '.jsonl')
            lines = [{'type': 'session_meta', 'payload': {'id': sid}}]
        else:
            transcript = home / 'projects' / 'example' / (sid + '.jsonl')
            lines = [{'sessionId': sid, 'type': 'system'}]
        transcript.parent.mkdir(parents=True, exist_ok=True)
        transcript.write_text(''.join(json.dumps(line) + '\n' for line in lines) + 'independent work\n')
        hooks = home / 'hooks.json'
        if hooks.exists():
            command = json.loads(hooks.read_text())['hooks']['Stop'][0]['hooks'][0]['command']
            event = {'hook_event_name': 'Stop', 'session_id': sid, 'transcript_path': str(transcript)}
            result = subprocess.run(
                shlex.split(command), input=json.dumps(event), text=True, capture_output=True, check=True
            )
            assert json.loads(result.stdout) == {}
    sys.exit(0)

folder = Path(os.environ['BERMAN_EPISODE'])
out = Path(args[args.index('--add-dir') + 1])
assert out == folder / 'return'
prompt = args[args.index('--') + 1] if '--' in args else ''
if actor == 'codex':
    unattended = args[0] == 'exec'
    assert unattended or '--no-daemon' in args
    sid = '12345678-1234-1234-1234-123456789012'
    if 'resume' in args:
        assert sid in args[args.index('resume') :]
    definition = home / 'hooks.json'
    transcript = home / 'sessions' / (sid + '.jsonl')
    first = {'type': 'session_meta', 'payload': {'id': sid}}
else:
    unattended = '--print' in args
    if unattended:
        assert args[args.index('--output-format') + 1] == 'json'
    sid = args[args.index('--resume' if '--resume' in args else '--session-id') + 1]
    definition = Path(args[args.index('--settings') + 1])
    transcript = home / 'projects' / 'example' / (sid + '.jsonl')
    first = {'sessionId': sid, 'type': 'system'}
if unattended:
    assert not sys.stdin.isatty() and sys.stdin.read() == ''

if not transcript.exists():
    assert prompt.startswith((folder / 'opening.md').read_text())
    transcript.parent.mkdir(parents=True, exist_ok=True)
    transcript.write_text(json.dumps(first) + '\n')
turns = transcript.read_text().count('"role": "user"') + 1
reply = f'done {turns}: {prompt.splitlines()[-1] if prompt else "resumed"}'
if actor == 'codex':
    said = [
        {'type': 'response_item', 'payload': {'type': 'message', 'role': role, 'content': [{'text': text}]}}
        for role, text in (('user', prompt), ('assistant', reply))
    ]
else:
    said = [
        {'sessionId': sid, 'message': {'role': role, 'content': text}}
        for role, text in (('user', prompt), ('assistant', reply))
    ]
with transcript.open('a') as stream:
    stream.writelines(json.dumps(line) + '\n' for line in said)
if 'Record a decision' in (folder / 'opening.md').read_text():
    (out / 'decision.md').write_text('We kept the reader because two callers still use it.\n')
if seconds := option('FIXTURE_SLEEP'):
    time.sleep(float(seconds))

if not option('FIXTURE_WITHOUT_HOOKS'):
    hooks = json.loads(definition.read_text())['hooks']
    for event in ('UserPromptSubmit', 'Stop', 'SessionEnd'):
        payload = {
            'hook_event_name': event,
            'session_id': sid,
            'cwd': str(project),
            'prompt': prompt,
            'transcript_path': str(transcript),
        }
        result = subprocess.run(
            shlex.split(hooks[event][0]['hooks'][0]['command']),
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            check=True,
        )
        assert json.loads(result.stdout) == {}

if not unattended:
    sys.exit(0)
failed = bool(option('FIXTURE_FAIL'))
reported = option('FIXTURE_REPORTED_SESSION', sid)
if option('FIXTURE_SILENT'):
    print('native failure detail', file=sys.stderr)
elif actor == 'claude':
    denials = [{'tool_name': 'Write', 'tool_use_id': 'toolu_1', 'tool_input': {'file_path': str(out / 'x.md')}}]
    # A failed request can still carry subtype success, with the error text as its result.
    result = {
        'type': 'result',
        'subtype': 'success',
        'is_error': failed,
        'result': 'API Error: overloaded' if failed else reply,
        'session_id': reported,
        'permission_denials': denials if option('FIXTURE_DENY') else [],
    }
    print(json.dumps(result))
else:
    events = [{'type': 'thread.started', 'thread_id': reported}, {'type': 'turn.started'}]
    if option('FIXTURE_DENY'):
        item = {'id': 'item_0', 'type': 'command_execution', 'command': 'touch x', 'status': 'declined'}
        events.append({'type': 'item.completed', 'item': item})
    # A turn can report progress in a message and still fail.
    events.append({'type': 'item.completed', 'item': {'id': 'item_1', 'type': 'agent_message', 'text': reply}})
    if failed:
        events.append({'type': 'turn.failed', 'error': {'message': 'stream disconnected'}})
    else:
        events.append({'type': 'turn.completed', 'usage': {}})
    print('\n'.join(json.dumps(event) for event in events))
if failed:
    print('the turn could not finish', file=sys.stderr)
    sys.exit(1)
