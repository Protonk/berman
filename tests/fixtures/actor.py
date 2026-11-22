#!/usr/bin/env python3
"""A scripted interactive harness used to exercise the installed command without a model."""

import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

args = sys.argv[1:]
project = Path.cwd()
(project / 'ordinary.txt').write_text('ordinary project work\n')
if 'BERMAN_EPISODE' not in os.environ:
    sys.exit(0)
actor = Path(sys.argv[0]).name
folder = Path(os.environ['BERMAN_EPISODE'])
out = Path(args[args.index('--add-dir') + 1])
assert out == folder / 'return'
home = Path(os.environ['CODEX_HOME' if actor == 'codex' else 'CLAUDE_CONFIG_DIR'])
if actor == 'codex':
    assert '--no-daemon' in args
    sid = '12345678-1234-1234-1234-123456789012'
    if 'resume' in args:
        assert sid in args
    definition = home / 'hooks.json'
    transcript = home / 'sessions' / (sid + '.jsonl')
    first = {'type': 'session_meta', 'payload': {'id': sid}}
    message = {
        'type': 'response_item',
        'payload': {'type': 'message', 'role': 'assistant', 'content': [{'text': 'done'}]},
    }
else:
    sid = args[args.index('--resume' if '--resume' in args else '--session-id') + 1]
    definition = Path(args[args.index('--settings') + 1])
    transcript = home / 'projects' / 'example' / (sid + '.jsonl')
    first = {'sessionId': sid, 'type': 'system'}
    message = {'sessionId': sid, 'message': {'role': 'assistant', 'content': 'done'}}
if '--' in args:
    prompt = args[args.index('--') + 1]
else:
    prompt = ''
if not transcript.exists():
    assert prompt == (folder / 'opening.md').read_text()
    transcript.parent.mkdir(parents=True, exist_ok=True)
    transcript.write_text(json.dumps(first) + '\n')
with transcript.open('a') as stream:
    stream.write(json.dumps(message) + '\n')
(out / 'decision.md').write_text('We kept the reader because two callers still use it.\n')
if os.environ.get('FIXTURE_WITHOUT_HOOKS'):
    sys.exit(0)
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
