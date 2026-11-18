#!/usr/bin/env python3
"""Deterministic Claude stand-in for CI. Its transcript and callbacks are synthetic."""

import json
import os
from pathlib import Path
import shlex
import subprocess
import sys


def main(args):
    if args == ['--version']:
        print('Berman CI deterministic worker 1')
        return
    packet = Path(args[args.index('--add-dir') + 1])
    instructions = packet / 'INSTRUCTIONS.md'
    if str(instructions) not in args[-1] or str(packet / 'notes') not in instructions.read_text():
        raise ValueError('The opening must lead to the instructions and the assigned notes directory')
    value = Path('challenge.txt').read_text().strip()
    Path('answer.py').write_text(f'VALUE = {value!r}\n')
    checked = subprocess.check_output([sys.executable, 'check.py'], text=True)
    (packet / 'notes/account.md').write_text(f'{value}\n{checked}')
    session = args[args.index('--session-id') + 1]
    source = Path(os.environ['CLAUDE_CONFIG_DIR']) / 'projects/fixture' / (session + '.jsonl')
    source.parent.mkdir(parents=True, exist_ok=True)
    message = {'role': 'assistant', 'content': 'Deterministic CI worker completed the fixture.'}
    source.write_text(json.dumps({'type': 'assistant', 'sessionId': session, 'message': message}) + '\n')
    hooks = json.loads(Path(args[args.index('--settings') + 1]).read_text())['hooks']
    for name in ('UserPromptSubmit', 'Stop', 'SessionEnd'):
        event = dict(hook_event_name=name, session_id=session, cwd=os.getcwd(), transcript_path=str(source))
        if name == 'UserPromptSubmit':
            event['prompt'] = args[-1]
        command = shlex.split(hooks[name][0]['hooks'][0]['command'])
        response = subprocess.check_output(command, input=json.dumps(event), text=True)
        if json.loads(response) != {}:
            raise ValueError(response)


if __name__ == '__main__':
    main(sys.argv[1:])
