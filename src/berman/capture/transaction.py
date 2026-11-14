"""Modern capture snapshots and finalization; the historical collector stays frozen."""

import json
import time

from berman import files, snapshots
from berman.capture import collector


def capture(owner, root, event):
    session = root / 'session'
    previous = snapshots.read(session)
    if any((session / name).exists() for name in ('finalized.json', 'ended.json')):
        if previous is None:
            raise ValueError('Closed capture has no committed snapshot')
        return previous['metadata']
    if previous and snapshots.pointer(session) is None:
        raise ValueError('An older active capture must be closed with its pinned runtime before adoption')
    binding = files.read(session / 'binding.json')
    old = previous['metadata'] if previous else {}
    source = owner.find_source(binding, event.get('transcript_path') or old.get('source_transcript'))
    expected = event.get('last_assistant_message')
    expected = expected if isinstance(expected, str) and expected else None
    expected_sha = files.digest(expected.encode()) if expected else old.get('expected_last_assistant_sha256')
    for attempt in range(3):
        all_data = source.read_bytes()
        end = all_data.rfind(b'\n') + 1
        raw, tail = all_data[:end], all_data[end:]
        if not raw:
            raise ValueError('No complete transcript records are available yet')
        try:
            first = json.loads(raw.splitlines()[0])
        except ValueError, UnicodeError:
            first = {}
        identity = first.get('payload', {}) if isinstance(first, dict) else {}
        identity = identity if isinstance(identity, dict) else {}
        sid = (
            identity.get('id', identity.get('session_id'))
            if isinstance(first, dict) and first.get('type') == 'session_meta'
            else None
        )
        if sid and sid != binding['session_id']:
            raise ValueError("Transcript session ID does not match this run's binding")
        reader, messages, malformed = collector.capture.render(raw)
        present = any(files.digest(message.encode()) == expected_sha for message in messages) if expected_sha else None
        if expected and not present and attempt < 2:
            time.sleep(0.08)
            continue
        break
    meta = {
        'schema': 'berman.session-capture/2',
        'harness': 'codex',
        'format': 'codex-jsonl/1',
        'run': binding['run'],
        'session_id': binding['session_id'],
        'captured_at_utc': files.now(),
        'event': event.get('hook_event_name', 'manual-refresh'),
        'turn_id': event.get('turn_id'),
        'source_transcript': str(source),
        'source_size_at_read': len(all_data),
        'saved_bytes': len(raw),
        'omitted_partial_tail_bytes': len(tail),
        'sha256': files.digest(raw),
        'partial_tail_file': 'partial-tail-' + files.digest(tail) + '.bin' if tail else None,
        'partial_tail_sha256': files.digest(tail) if tail else None,
        'source_identity': 'session_meta ID verified'
        if sid
        else 'hook-supplied association; metadata format unrecognized',
        'previous_snapshot_is_prefix': raw.startswith(previous['raw']) if previous else None,
        'last_assistant_message_present': present,
        'expected_last_assistant_sha256': expected_sha,
        'unparsed_jsonl_lines': malformed,
        'reader_sha256': files.digest(reader),
        'scope': 'Persisted full session at capture time; no assertion of research completion.',
    }
    return snapshots.publish(
        session, meta, raw, reader, tail, owner.analysis / '.state/capture-staging' / binding['run']
    )


def status(owner, run):
    active, archived = owner.fieldwork / run, owner.analysis / 'runs' / run
    result = {
        'run': run,
        'state': 'missing',
        'searched': [str(active), str(archived)],
        'binding': None,
        'capture': None,
        'ended': False,
        'finalized': False,
        'warnings': [],
    }
    if active.exists() and archived.exists():
        return dict(
            result,
            state='identity-collision',
            warnings=['Both active and archived directories exist; neither was selected'],
        )
    root = active if active.exists() else archived
    if not root.exists():
        return result
    result.update(location='active' if root == active else 'archived', path=str(root))
    try:
        owner.run_path(run, readonly=True)
        session = root / 'session'
        result['state'] = 'prepared'
        if not (session / 'binding.json').exists():
            if session.exists() and any(session.iterdir()):
                result.update(state='unbound-session-files', warnings=['Session files exist without a binding'])
            return result
        binding = files.read(session / 'binding.json')
        result.update(binding=binding, state='registered-no-snapshot')
        registration = owner.registry(binding['session_id'])
        state = files.read(registration) if registration.exists() else {}
        pending = state.get('run') == run and state.get('active_turn') is True
        result['prompt_without_terminal_callback'] = pending
        if pending:
            result['warnings'].append(
                'A submitted prompt has no later terminal callback; the turn may be active or may have failed without Stop.'
            )
        captured = snapshots.read(session)
        if captured is None:
            if (session / 'snapshots').exists() or (session / 'transcript.jsonl').exists():
                result.update(state='snapshot-inconsistent', snapshot_consistent=False)
                result['warnings'].append('Snapshot material exists without a committed capture')
            return result
        meta = captured['metadata']
        if meta['run'] != run or meta['session_id'] != binding['session_id']:
            raise ValueError('Capture and binding identities disagree')
        result.update(capture=meta, state='snapshot', snapshot_consistent=True, snapshot_path=str(captured['folder']))
        for filename, key, state_name in (
            ('finalized.json', 'finalized', 'finalized'),
            ('ended.json', 'ended', 'ended'),
        ):
            if (session / filename).exists():
                closure = files.read(session / filename)
                if closure['capture_sha256'] != meta['sha256']:
                    raise ValueError('Capture closure and snapshot disagree')
                for field in ('run', 'session_id', 'saved_bytes'):
                    if field in closure and closure[field] != meta[field]:
                        raise ValueError(f'Capture closure and snapshot {field} disagree')
                if closure.get('generation') and closure['generation'] != (snapshots.pointer(session) or {}).get('generation'):
                    raise ValueError('Capture closure and committed generation disagree')
                result[key] = True
                if result['state'] != 'finalized':
                    result['state'] = state_name
        for key in ('omitted_partial_tail_bytes', 'unparsed_jsonl_lines'):
            if meta.get(key):
                result['warnings'].append(f'{key}: {meta[key]}')
        if meta.get('last_assistant_message_present') is False:
            result['warnings'].append('Expected assistant message is missing')
        try:
            source = owner.find_source(binding, meta['source_transcript'])
            data = source.read_bytes()
            result['source'] = {
                'path': str(source),
                'bytes': len(data),
                'differs_from_snapshot': files.digest(data) != meta['sha256'],
            }
            if result['finalized'] and result['source']['differs_from_snapshot']:
                result['warnings'].append(
                    'Source contains bytes outside the frozen snapshot; a supplement can preserve them'
                )
        except (OSError, ValueError) as exc:
            result['warnings'].append(str(exc))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        result.update(state='damaged', snapshot_consistent=False, warnings=[str(exc)])
    return result


def command(owner, name, run, reason=None, transcript_path=None, **unused):
    root = owner.run_path(run)
    session = root / 'session'
    with owner.locked():
        if (session / 'ended.json').exists() and owner.status(run).get('snapshot_consistent') is not True:
            raise ValueError('Ended snapshot is inconsistent; finalization cannot assert a valid boundary')
        if (session / 'finalized.json').exists():
            if owner.status(run).get('snapshot_consistent') is not True:
                raise ValueError('Finalized snapshot is inconsistent; frozen bytes were not changed')
            return files.read(session / 'finalized.json')
        meta = owner.capture(root, {'hook_event_name': 'manual-' + name, 'transcript_path': transcript_path})
        if name != 'finalize':
            return meta
        captured = snapshots.read(session)
        raw = captured['raw']
        terminal = None
        for line in captured['history']:
            item = json.loads(line)
            if item['event'] in ('Stop', 'SessionEnd'):
                terminal = item
        registration = owner.registry(meta['session_id'])
        state = files.read(registration) if registration.exists() else {}
        problems = []
        if not (session / 'ended.json').exists():
            if state.get('active_turn'):
                problems.append('A prompt was submitted without a later terminal callback')
            if terminal is None:
                problems.append('No successful Stop or SessionEnd snapshot')
            elif files.digest(raw[: terminal['saved_bytes']]) != terminal['sha256']:
                problems.append('Source changed since the terminal snapshot')
            else:
                for line in raw[terminal['saved_bytes'] :].splitlines():
                    try:
                        value = json.loads(line)
                        allowed = (
                            value.get('type') == 'event_msg' and value.get('payload', {}).get('type') == 'task_complete'
                        )
                    except ValueError, AttributeError:
                        allowed = False
                    if not allowed:
                        problems.append('Additional records beyond the terminal snapshot (not just task_complete)')
                        break
        if meta.get('last_assistant_message_present') is False:
            problems.append('Expected assistant message is missing')
        if terminal and terminal.get('last_assistant_message_present') is False:
            problems.append('Expected assistant message was absent at the terminal callback')
        if meta.get('omitted_partial_tail_bytes') or meta.get('unparsed_jsonl_lines'):
            problems.append('Partial or unparsed transcript records remain')
        if problems and not reason:
            raise ValueError(
                'Not finalized: '
                + '; '.join(problems)
                + '. Wait/refresh, or use --allow-incomplete with a reason for an intentional boundary.'
            )
        final = {
            'schema': 'berman.session-finalization/3',
            'run': run,
            'session_id': meta['session_id'],
            'finalized_at_utc': files.now(),
            'capture_sha256': meta['sha256'],
            'saved_bytes': len(raw),
            'generation': (snapshots.pointer(session) or {}).get('generation'),
            'boundary': {
                'terminal_event': terminal,
                'ended': (session / 'ended.json').exists(),
                'warnings': problems,
                'override_reason': reason,
            },
            'meaning': 'Snapshot frozen for preservation; does not assert scientific completion.',
        }
        files.atomic(session / 'finalized.json', files.json_bytes(final))
        return final
