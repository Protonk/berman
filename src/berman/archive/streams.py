"""Explicit native-stream readers; raw records and decoded coverage stay distinct."""

import json

FORMATS = ('codex-jsonl/1', 'claude-jsonl/1', 'unknown')


def decode(data, format_name, source):
    """Return record coverage and visible blocks with exact source coordinates.

    ``source`` supplies collection/run/path/digest, never a participant attribution.
    Unknown formats and records remain raw; no native role implies a human actor.
    """
    records, blocks = [], []
    for line, raw in enumerate(data.splitlines(), 1):
        if not raw.strip():
            continue
        base = dict(source, line=line, format=format_name)
        record = dict(base, raw_availability='included', decode_status='unsupported',
                      record_kind='', native_role='', time='', uuid='', blocks=0, detail='')
        try:
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError('JSON record is not an object')
        except (ValueError, UnicodeError):
            record.update(decode_status='malformed', detail='Not a JSON object; original bytes retained')
            records.append(record)
            continue
        record.update(record_kind=value.get('type', value.get('event', value.get('kind', 'record'))),
                      time=value.get('timestamp', value.get('time', '')), uuid=value.get('uuid', ''))
        if format_name == 'codex-jsonl/1':
            selected, status, role = codex(value)
        elif format_name == 'claude-jsonl/1':
            selected, status, role = claude(value)
        else:
            selected, status, role = [], 'unsupported', ''
        record.update(decode_status=status, native_role=role, blocks=len(selected))
        if status == 'unsupported':
            record['detail'] = 'Format or record shape is not supported by this decoder'
        elif status == 'not-selected':
            record['detail'] = 'Metadata, private reasoning or duplicate events are not selected'
        for selected_block in selected:
            blocks.append(dict(base, time=record['time'], native_role=role, **selected_block))
        records.append(record)
    return records, blocks


def block(pointer, content, kind, name='', call_id=''):
    return {'pointer': pointer, 'content': content, 'kind': kind, 'name': name, 'call_id': call_id}


def codex(value):
    kind, payload = value.get('type'), value.get('payload')
    if not isinstance(kind, str):
        return [], 'unsupported', ''
    if kind not in ('response_item', 'event_msg'):
        metadata = {'session_meta', 'turn_context', 'world_state', 'token_usage_record',
                    'inter_agent_communication_metadata', 'compacted'}
        return [], 'not-selected' if kind in metadata else 'unsupported', ''
    if not isinstance(payload, dict):
        return [], 'unsupported', ''
    role, pkind = payload.get('role', ''), payload.get('type')
    if not isinstance(role, str) or not isinstance(pkind, str):
        return [], 'unsupported', ''
    if kind == 'event_msg':
        if pkind == 'task_complete' and payload.get('error'):
            return [block('/payload/error', payload['error'], 'task_error')], 'decoded', role
        excluded = {'agent_message', 'user_message', 'agent_reasoning', 'token_count', 'task_started', 'task_complete'}
        return [], 'not-selected' if pkind in excluded else 'unsupported', role
    if pkind == 'message':
        if role not in ('user', 'assistant'):
            return [], 'not-selected', role
        content = payload.get('content')
        if not isinstance(content, list):
            return [], 'unsupported', role
        selected, unsupported = [], False
        for index, item in enumerate(content):
            if isinstance(item, dict) and item.get('type') in ('reasoning', 'thinking', 'redacted_thinking'):
                continue
            if (isinstance(item, dict) and item.get('type') in (None, 'text', 'input_text', 'output_text')
                    and isinstance(item.get('text'), str)):
                selected.append(block(f'/payload/content/{index}/text', item['text'], 'message'))
            else:
                unsupported = True
        return selected, coverage(selected, unsupported), role
    fields = {'function_call': 'arguments', 'custom_tool_call': 'input',
              'function_call_output': 'output', 'custom_tool_call_output': 'output'}
    if pkind in fields:
        field = fields[pkind]
        if field not in payload:
            return [], 'unsupported', role
        return [block('/payload/' + field, payload[field], pkind,
                      payload.get('name', ''), payload.get('call_id', ''))], 'decoded', role
    return [], 'not-selected' if pkind == 'reasoning' else 'unsupported', role


def claude(value):
    message = value.get('message')
    if not isinstance(message, dict):
        if value.get('type') == 'result' and 'result' in value:
            return [block('/result', value['result'], 'result')], 'decoded', ''
        return [], 'unsupported', ''
    role = message.get('role', value.get('type', ''))
    if not isinstance(role, str):
        return [], 'unsupported', ''
    content = message.get('content')
    if isinstance(content, str):
        return [block('/message/content', content, 'text')], 'decoded', role
    if not isinstance(content, list):
        return [], 'unsupported', role
    selected, unsupported = [], False
    for index, item in enumerate(content):
        if not isinstance(item, dict):
            unsupported = True
            continue
        kind = item.get('type')
        if not isinstance(kind, str):
            unsupported = True
            continue
        field = {'text': 'text', 'tool_use': 'input', 'tool_result': 'content'}.get(kind)
        if field is None:
            unsupported |= kind not in ('thinking', 'redacted_thinking')
            continue
        if field not in item:
            unsupported = True
            continue
        selected.append(block(f'/message/content/{index}/{field}', item[field], kind,
                              item.get('name', ''), item.get('id', item.get('tool_use_id', ''))))
    return selected, coverage(selected, unsupported), role


def coverage(selected, unsupported):
    if selected:
        return 'partial' if unsupported else 'decoded'
    return 'unsupported' if unsupported else 'not-selected'
