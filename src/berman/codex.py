"""Render the reading copy for a captured Codex session."""

import json


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
