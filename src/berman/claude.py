"""Claude Code session callbacks and the reading copy of a Claude transcript.

Berman registers these callbacks in per-launch settings and renders each staged parent
transcript with `render` (berman.capture). Raw JSONL is always retained; the reading copy
is derived from it and never substitutes for it.
"""

import json

EVENTS = ('SessionStart', 'UserPromptSubmit', 'Stop', 'StopFailure', 'PreCompact', 'PostCompact', 'SessionEnd')


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
