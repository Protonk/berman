"""What Berman knows about Codex: its home, callbacks, launch, session store and transcripts.

Codex reads the reviewed callbacks from its home and chooses its own session ID. The opening
prompt, submitted in the checkout, binds that session to the episode. Raw JSONL is always
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


def binds(state, event):
    """The opening prompt, submitted in the checkout, binds the session Codex chose."""
    return (
        event['hook_event_name'] == 'UserPromptSubmit'
        and event.get('prompt') == state['opening']
        and bool(event.get('cwd'))
        and Path(event['cwd']).resolve() == Path(state['project'])
    )


def arguments(folder, session, resuming, hooks):
    """The callbacks come from the Codex home. --no-daemon lets them inherit the episode environment."""
    if resuming:
        return ['--no-daemon', 'resume', '--add-dir', str(folder / 'return'), session]
    return ['--no-daemon', '--add-dir', str(folder / 'return')]


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
