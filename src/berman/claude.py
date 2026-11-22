"""What Berman knows about Claude Code: its home, callbacks, launch, results, session store and transcripts.

Berman launches Claude Code with the episode ID as its session ID and passes the callbacks as
per-launch settings. An unattended sitting runs in print mode and reports one JSON result. Raw
JSONL is always retained; the reading copy is derived from it and never substitutes for it.
"""

import json

from berman import files

HOME_VARIABLE = 'CLAUDE_CONFIG_DIR'
DEFAULT_HOME = '.claude'
STORES = ('projects',)
EVENTS = dict.fromkeys(
    ('SessionStart', 'UserPromptSubmit', 'Stop', 'StopFailure', 'PreCompact', 'PostCompact', 'SessionEnd'), 30
)


def assigned_session(state):
    return state['id']


def binds(state, event, first):
    """Only the assigned session can bind the episode."""
    return event['session_id'] == state['id']


def arguments(folder, session, resuming, hooks, attended):
    """Write the per-launch callbacks and return the arguments for a sitting."""
    settings = folder / 'claude-hooks.json'
    files.write(settings, hooks)
    start = '--resume' if resuming else '--session-id'
    mode = [] if attended else ['--print', '--output-format', 'json']
    return [*mode, '--settings', str(settings), start, session, '--add-dir', str(folder / 'return')]


def outcome(output):
    """The session, final response and permission denials that a print-mode result reports."""
    try:
        value = json.loads(output)
    except (ValueError, UnicodeError):
        value = None
    if isinstance(value, list):
        # A verbose setting turns the result into the last item of a message list.
        value = next(
            (item for item in reversed(value) if isinstance(item, dict) and item.get('type') == 'result'), None
        )
    if not isinstance(value, dict) or value.get('type') != 'result':
        return {'session': None, 'response': None, 'denials': [], 'limitations': ['The native output holds no result.']}
    session = value.get('session_id')
    denials = [
        {'tool': item.get('tool_name'), 'input': item.get('tool_input')}
        for item in value.get('permission_denials') or ()
        if isinstance(item, dict)
    ]
    text = value.get('result')
    if value.get('subtype') == 'success' and not value.get('is_error') and isinstance(text, str):
        response, limitations = text, []
    else:
        subtype = value.get('subtype')
        errors = [item for item in value.get('errors') or () if isinstance(item, str)]
        parts = [subtype if subtype != 'success' else None, text if isinstance(text, str) else None, *errors]
        detail = '; '.join(str(part) for part in parts if part) or 'no detail'
        response, limitations = None, [f'The native result reports an error: {detail}']
    return {
        'session': session if isinstance(session, str) else None,
        'response': response,
        'denials': denials,
        'limitations': limitations,
    }


def candidates(home, session):
    return (home / 'projects').glob(f'*/{session}.jsonl')


def owns(path, session):
    """The store names each transcript after its session. Reading it checks the identity."""
    return True


def transcript(raw, path, session):
    """Check that the transcript belongs to the session and render its reading copy."""
    reader, messages, malformed, identities = render(raw)
    if path.name != session + '.jsonl' or identities != {session}:
        raise ValueError('Claude transcript identity differs from the assigned parent')
    return reader, messages, malformed


def render(data):
    lines = [
        'CLAUDE EPISODE SESSION: DERIVED READING COPY',
        '',
        'Source: transcript.jsonl. Blocks cite raw JSONL lines and JSON pointers.',
        'Visible messages and tool calls/results. Other content remains in the raw source.',
        '',
    ]
    messages, malformed, identities = [], [], set()
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
        if isinstance(event.get('sessionId'), str):
            identities.add(event['sessionId'])
        message = event.get('message')
        if not isinstance(message, dict):
            continue
        role = message.get('role', event.get('type'))
        content = message.get('content', [])
        if isinstance(content, str):
            content = [{'type': 'text', 'text': content}]
        if not isinstance(content, list):
            continue
        texts = []
        for i, block in enumerate(content):
            if not isinstance(block, dict):
                continue
            kind = block.get('type')
            field = {'text': 'text', 'tool_use': 'input', 'tool_result': 'content'}.get(kind)
            if field is None:
                continue
            value = block.get(field, '')
            if kind == 'text' and isinstance(value, str):
                texts.append(value)
            pointer = '/message/content' if isinstance(message.get('content'), str) else f'/message/content/{i}/{field}'
            lines += [
                '=' * 72,
                f'SOURCE: transcript.jsonl#L{number} {pointer}',
                f'TIME: {event.get("timestamp", "not recorded")} | ROLE: {role} | KIND: {kind}',
                f'NAME: {block.get("name", "")} | CALL ID: {block.get("id", block.get("tool_use_id", ""))}',
                value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2),
                '',
            ]
        if role == 'assistant':
            messages.append(''.join(texts))
    return ('\n'.join(lines) + '\n').encode(), messages, malformed, identities
