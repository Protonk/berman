"""What Berman knows about Codex: its home, callbacks, launch, results, session store and transcripts.

Codex reads the reviewed callbacks from its home and chooses its own session ID. The first
sitting's message, submitted in the checkout, binds that session to the episode. An unattended
sitting runs codex exec, whose JSONL events name the session it ran. Raw JSONL is always
retained; the reading copy is derived from it and never substitutes for it.
"""

import json
from pathlib import Path

from berman import files

HOME_VARIABLE = 'CODEX_HOME'
DEFAULT_HOME = '.codex'
STORES = ('sessions', 'archived_sessions')
# Codex grants callbacks at an interruption or the end of a session a short timeout.
EVENTS = {'UserPromptSubmit': 30, 'Stop': 30, 'Interrupt': 3, 'SessionEnd': 3}
FIRST_LINE = 128 * 1024


def assigned_session(state):
    return None


def binds(state, event, first):
    """The first sitting's message, submitted in the checkout, binds the session Codex chose."""
    return (
        event['hook_event_name'] == 'UserPromptSubmit'
        and event.get('prompt') == first
        and bool(event.get('cwd'))
        and Path(event['cwd']).resolve() == Path(state['project'])
    )


def arguments(folder, session, resuming, hooks, attended):
    """The callbacks come from the Codex home and must see the episode environment.

    The interactive CLI needs --no-daemon for that. codex exec runs its session in its own process.
    """
    directory = ['--add-dir', str(folder / 'return')]
    if attended:
        return ['--no-daemon', 'resume', *directory, session] if resuming else ['--no-daemon', *directory]
    return ['exec', *directory, '--json', *(['resume', session] if resuming else [])]


def outcome(output):
    """The session, final message and declined operations in one codex exec event stream."""
    sessions, messages, denials, limitations = [], [], [], []
    finished, malformed, events = None, 0, 0
    for line in output.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except (ValueError, UnicodeError):
            malformed += 1
            continue
        if not isinstance(event, dict):
            continue
        events += 1
        kind = event.get('type')
        item = event.get('item') if isinstance(event.get('item'), dict) else {}
        problem = event.get('error') if kind == 'turn.failed' else event
        message = problem.get('message') if isinstance(problem, dict) else None
        if kind == 'thread.started' and isinstance(event.get('thread_id'), str):
            sessions.append(event['thread_id'])
        elif kind == 'turn.started':
            messages, finished = [], None
        elif kind == 'item.completed' and item.get('type') == 'agent_message' and isinstance(item.get('text'), str):
            messages.append(item['text'])
        elif kind == 'turn.completed':
            finished = 'completed'
        elif kind == 'turn.failed':
            finished = 'failed'
            limitations.append(f'The native turn failed: {message or "no reason given"}.')
        elif kind == 'error':
            limitations.append(f'The native output reports an error: {message or "no reason given"}.')
        if kind == 'item.completed' and item.get('status') == 'declined':
            denials.append({'tool': item.get('type'), 'input': item.get('command', item.get('changes'))})
    if malformed:
        limitations.append(f'{malformed} native output lines were not JSON.')
    if len(set(sessions)) > 1:
        limitations.append('The native output names more than one session.')
    if not events:
        limitations.append('The native output holds no events.')
    elif finished is None:
        limitations.append('The native output does not show the turn finishing.')
    elif finished == 'completed' and not messages:
        limitations.append('The completed turn has no final message.')
    return {
        'session': sessions[0] if len(set(sessions)) == 1 else None,
        'response': messages[-1] if finished == 'completed' and messages else None,
        'denials': denials,
        'limitations': limitations,
    }


def meta_session(line):
    """The session ID recorded in a transcript's first line, or None."""
    try:
        value = json.loads(line)
    except (ValueError, UnicodeError):
        return None
    if not isinstance(value, dict) or value.get('type') != 'session_meta':
        return None
    meta = value.get('payload')
    if not isinstance(meta, dict):
        return None
    ident = meta.get('id') or meta.get('session_id')
    return ident if isinstance(ident, str) else None


def candidates(home, session):
    return (p for part in STORES for p in (home / part).rglob(f'*{session}*.jsonl'))


def owns(path, session):
    with files.regular(path).open('rb') as stream:
        line = stream.readline(FIRST_LINE + 1)
    return len(line) <= FIRST_LINE and meta_session(line) == session


def transcript(raw, path, session):
    """Check that the transcript belongs to the session and render its reading copy."""
    if meta_session(raw.partition(b'\n')[0]) != session:
        raise ValueError('Codex transcript metadata differs from the assigned parent')
    return render(raw)


def render(data):
    """Render visible response items and harness task errors; retain raw records."""
    lines = [
        'CODEX EPISODE SESSION: DERIVED READING COPY',
        '',
        'Source: transcript.jsonl. Each block cites its raw JSONL line and JSON pointer.',
        'Visible user/assistant messages, tool calls/results, and harness task errors.',
        'Reasoning, developer context, transport metadata and duplicate message events stay raw.',
        '',
    ]
    assistant = []
    malformed = []
    for number, raw in enumerate(data.splitlines(), 1):
        if not raw.strip():
            continue
        try:
            event = json.loads(raw)
        except (ValueError, UnicodeError):
            malformed.append(number)
            continue
        if not isinstance(event, dict):
            continue
        item = event.get('payload', {})
        if not isinstance(item, dict):
            continue
        kind, role = item.get('type'), item.get('role', '')
        blocks = []
        if event.get('type') == 'event_msg' and kind == 'task_complete' and item.get('error'):
            role, kind = 'harness', 'task_error'
            blocks.append(('/payload/error', item['error']))
        elif event.get('type') != 'response_item':
            continue
        elif kind == 'message' and role in ('user', 'assistant'):
            texts = []
            content = item.get('content', [])
            for i, block in enumerate(content if isinstance(content, list) else []):
                if isinstance(block, dict) and isinstance(block.get('text'), str):
                    texts.append(block['text'])
                    blocks.append((f'/payload/content/{i}/text', block['text']))
            if role == 'assistant':
                assistant.append(''.join(texts))
        elif kind in ('function_call', 'custom_tool_call'):
            field = 'arguments' if kind == 'function_call' else 'input'
            blocks.append(('/payload/' + field, item.get(field, '')))
        elif kind in ('function_call_output', 'custom_tool_call_output'):
            blocks.append(('/payload/output', item.get('output', '')))
        for pointer, content in blocks:
            lines += [
                '=' * 72,
                f'SOURCE: transcript.jsonl#L{number} {pointer}',
                f'TIME: {event.get("timestamp", "not recorded")} | ROLE: {role} | KIND: {kind}',
                f'NAME: {item.get("name", "")} | CALL ID: {item.get("call_id", "")}',
                content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, indent=2),
                '',
            ]
    return ('\n'.join(lines) + '\n').encode(), assistant, malformed
