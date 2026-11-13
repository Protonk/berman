#!/usr/bin/env python3
"""Collect Codex fieldwork sessions through passive hooks or explicit commands."""

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import time
import uuid


MARKER = "Stewart-Fieldwork-Run: "
RUN = re.compile(r"run-[0-9]{3,6}")
SESSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
ANALYSIS = Path(__file__).resolve().parent.parent
CODEX_HOME = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))


def now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()


def regular(path):
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError(f"Expected a regular file: {path}")
    if any(p.is_symlink() for p in [path, *path.parents]):
        raise ValueError(f"Symlink in capture path: {path}")
    return path


def read_json(path):
    return json.loads(regular(path).read_text())


def atomic(path, data):
    if path.is_symlink() or any(p.is_symlink() for p in path.parents):
        raise ValueError(f"Symlink in capture destination: {path}")
    fd, temp = tempfile.mkstemp(prefix=".capture-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)


def render(data):
    """Render visible response items and harness task errors; retain raw records."""
    lines = ["CODEX FIELDWORK SESSION: DERIVED READING COPY", "",
             "Source: transcript.jsonl. Each block cites its raw JSONL line and JSON pointer.",
             "Visible user/assistant messages, tool calls/results, and harness task errors.",
             "Reasoning, developer context, transport metadata and duplicate message events stay raw.", ""]
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
        item = event.get("payload", {})
        if not isinstance(item, dict):
            continue
        kind, role = item.get("type"), item.get("role", "")
        blocks = []
        if event.get('type') == 'event_msg' and kind == 'task_complete' and item.get('error'):
            role, kind = 'harness', 'task_error'
            blocks.append(('/payload/error', item['error']))
        elif event.get('type') != 'response_item':
            continue
        elif kind == "message" and role in ("user", "assistant"):
            texts = []
            content = item.get('content', [])
            for i, block in enumerate(content if isinstance(content, list) else []):
                if isinstance(block, dict) and isinstance(block.get("text"), str):
                    texts.append(block["text"])
                    blocks.append((f"/payload/content/{i}/text", block["text"]))
            if role == "assistant":
                assistant.append("".join(texts))
        elif kind in ("function_call", "custom_tool_call"):
            field = "arguments" if kind == "function_call" else "input"
            blocks.append(("/payload/" + field, item.get(field, "")))
        elif kind in ("function_call_output", "custom_tool_call_output"):
            blocks.append(("/payload/output", item.get("output", "")))
        for pointer, content in blocks:
            lines += ["=" * 72, f"SOURCE: transcript.jsonl#L{number} {pointer}",
                      f"TIME: {event.get('timestamp', 'not recorded')} | ROLE: {role} | KIND: {kind}",
                      f"NAME: {item.get('name', '')} | CALL ID: {item.get('call_id', '')}",
                      content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, indent=2), ""]
    return ("\n".join(lines) + "\n").encode(), assistant, malformed


class Collector:
    def __init__(self, fieldwork, codex_home, analysis):
        self.fieldwork = Path(fieldwork)
        self.codex_home = Path(codex_home)
        self.analysis = Path(analysis)
        self.state = self.codex_home / "stewart-fieldwork"

    def run_path(self, run, readonly=False):
        if not RUN.fullmatch(run):
            raise ValueError("Run must have the form run-004 (3 to 6 digits)")
        root = self.fieldwork / run
        if readonly and not root.exists():
            root = self.analysis / "runs" / run
        if not root.is_dir() or any(p.is_symlink() for p in [root, *root.parents]):
            raise ValueError(f"Run directory must already exist without symlinks: {root}")
        for name in ("INSTRUCTIONS.md", "narrative.md"):
            regular(root / name)
        if not (root / "scratch").is_dir() or (root / "scratch").is_symlink():
            raise ValueError(f"Missing regular scratch directory: {root}")
        return root

    def archived(self, run):
        if (self.analysis / 'runs' / run).exists():
            return True
        layout = self.analysis / 'source-layout.json'
        return layout.exists() and any(r['run'] == run for r in read_json(layout)['runs'])

    def find_source(self, binding, supplied=None):
        value = supplied or binding.get('transcript_path')
        if value:
            try:
                return self.source(value)
            except FileNotFoundError:
                pass
        # Relocation recovery uses an exact session identity, never recency.
        matches = []
        for part in ('sessions', 'archived_sessions'):
            for path in (self.codex_home / part).rglob('*' + binding['session_id'] + '*.jsonl'):
                try:
                    path = self.source(str(path))
                    with path.open('rb') as stream:
                        first = json.loads(stream.readline())
                    payload = first.get('payload', {})
                    if first.get('type') == 'session_meta' and payload.get('id', payload.get('session_id')) == binding['session_id']:
                        matches.append(path)
                except (OSError, ValueError, AttributeError, UnicodeError):
                    continue
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise ValueError('Several transcripts match this session; supply --transcript-path explicitly')
        raise ValueError('No persisted transcript found for this binding; supply --transcript-path or wait for a callback with a path')

    def status(self, run):
        if not RUN.fullmatch(run):
            raise ValueError('Run must have the form run-004 (3 to 6 digits)')
        active, archived = self.fieldwork / run, self.analysis / 'runs' / run
        result = {'run': run, 'state': 'missing', 'searched': [str(active), str(archived)],
                  'binding': None, 'capture': None, 'ended': False, 'finalized': False, 'warnings': []}
        if active.exists() and archived.exists():
            return dict(result, state='identity-collision', warnings=['Both active and archived directories exist; neither was selected'])
        root = active if active.exists() else archived
        if not root.exists():
            return result
        result.update(location='active' if root == active else 'archived', path=str(root))
        try:
            self.run_path(run, readonly=True)
            result['state'] = 'prepared'
            folder = root / 'session'
            if not (folder / 'binding.json').exists():
                if folder.exists() and any(folder.iterdir()):
                    result.update(state='unbound-session-files', warnings=['Session files exist without a binding'])
                return result
            binding = read_json(folder / 'binding.json')
            result.update(binding=binding, state='registered-no-snapshot')
            registry = self.registry(binding['session_id'])
            association = read_json(registry) if registry.exists() else {}
            pending = association.get('run') == run and association.get('active_turn') is True
            result['prompt_without_terminal_callback'] = pending
            if pending:
                result['warnings'].append('A submitted prompt has no later terminal callback; the turn may be active or may have failed without Stop. Refresh to preserve current persisted bytes.')
            capture = read_json(folder / 'capture.json') if (folder / 'capture.json').exists() else None
            result['capture'] = capture
            result['ended'] = (folder / 'ended.json').exists()
            result['finalized'] = (folder / 'finalized.json').exists()
            if capture:
                raw = regular(folder / 'transcript.jsonl').read_bytes()
                consistent = digest(raw) == capture['sha256'] and len(raw) == capture['saved_bytes']
                reader_hash = capture.get('reader_sha256')
                if reader_hash:
                    consistent &= digest(regular(folder / 'conversation.txt').read_bytes()) == reader_hash
                history = regular(folder / 'captures.jsonl').read_bytes().splitlines()
                consistent &= bool(history) and json.loads(history[-1]) == capture
                if result['finalized']:
                    consistent &= read_json(folder / 'finalized.json')['capture_sha256'] == digest(raw)
                result.update(snapshot_consistent=consistent,
                              state='snapshot' if consistent else 'snapshot-inconsistent')
                if consistent and result['finalized']:
                    result['state'] = 'finalized'
                elif consistent and result['ended']:
                    result['state'] = 'ended'
                if not consistent:
                    result['warnings'].append('Saved bytes and metadata disagree; refresh an active capture before finalization')
                for key in ('omitted_partial_tail_bytes', 'unparsed_jsonl_lines'):
                    if capture.get(key):
                        result['warnings'].append(f'{key}: {capture[key]}')
                if capture.get('last_assistant_message_present') is False:
                    result['warnings'].append('Expected assistant message is missing')
                try:
                    source = self.find_source(binding)
                    current = source.read_bytes()
                    result['source'] = {'path': str(source), 'bytes': len(current),
                                        'differs_from_snapshot': digest(current) != capture['sha256']}
                    if result['finalized'] and digest(current) != capture['sha256']:
                        result['warnings'].append('Source contains bytes outside the frozen snapshot; a supplement can preserve them')
                except (OSError, ValueError) as error:
                    result['warnings'].append(str(error))
            elif (folder / 'transcript.jsonl').exists():
                result.update(state='snapshot-inconsistent', snapshot_consistent=False)
                result['warnings'].append('Transcript exists without capture metadata; refresh to recover')
        except (OSError, ValueError, KeyError) as error:
            result.update(state='damaged', warnings=[str(error)])
        return result

    def registry(self, sid):
        if not isinstance(sid, str) or not SESSION.fullmatch(sid):
            raise ValueError("Invalid session ID")
        return self.state / (sid + ".json")

    @contextmanager
    def locked(self):
        if any(p.is_symlink() for p in [self.state, *self.state.parents]):
            raise ValueError("Symlink in collector state directory")
        self.state.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = self.state / ".lock"
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            deadline = time.monotonic() + 0.6
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise ValueError("Another capture is still running; the next event can refresh it")
                    time.sleep(0.02)
            yield
        finally:
            os.close(fd)

    def source(self, value):
        if not isinstance(value, str) or not value:
            raise ValueError("Codex has not supplied a transcript path yet")
        path = Path(value)
        if not path.is_absolute() or not any(path.is_relative_to(self.codex_home / part) for part in ("sessions", "archived_sessions")):
            raise ValueError("Transcript must be inside Codex sessions or archived_sessions")
        if ".." in path.parts:
            raise ValueError("Parent traversal in transcript path")
        return regular(path)

    def capture(self, root, event):
        folder = root / "session"
        if (folder / "finalized.json").exists() or (folder / "ended.json").exists():
            return read_json(folder / "capture.json")
        binding = read_json(folder / "binding.json")
        source = self.find_source(binding, event.get('transcript_path'))
        expected = event.get("last_assistant_message")
        expected = expected if isinstance(expected, str) and expected else None
        old_record = read_json(folder / "capture.json") if (folder / "capture.json").exists() else {}
        expected_sha = digest(expected.encode()) if expected else old_record.get("expected_last_assistant_sha256")
        for attempt in range(3):
            with source.open("rb") as stream:
                size = os.fstat(stream.fileno()).st_size
                data = stream.read(size)
            if len(data) != size:
                raise ValueError("Transcript changed size while reading; retry capture")
            end = data.rfind(b"\n") + 1
            tail = len(data) - end
            tail_bytes = data[end:]
            data = data[:end]
            if not data:
                raise ValueError("No complete transcript records are available yet")
            try:
                first = json.loads(data.splitlines()[0])
            except (ValueError, UnicodeError):
                first = {}
            if not isinstance(first, dict):
                first = {}
            identity = first.get("payload", {})
            if not isinstance(identity, dict):
                identity = {}
            source_id = identity.get("id", identity.get("session_id")) if first.get("type") == "session_meta" else None
            if source_id and source_id != binding["session_id"]:
                raise ValueError("Transcript session ID does not match this run's binding")
            readable, messages, malformed = render(data)
            present = any(digest(message.encode()) == expected_sha for message in messages) if expected_sha else None
            if expected and not present and attempt < 2:
                time.sleep(0.08)
                continue
            break
        previous = folder / "transcript.jsonl"
        previous_is_prefix = None
        if previous.exists():
            previous_data = regular(previous).read_bytes()
            previous_is_prefix = data.startswith(previous_data)
            if not previous_is_prefix:
                retained = folder / ("previous-" + digest(previous_data) + ".jsonl")
                if not retained.exists():
                    atomic(retained, previous_data)
        record = {"schema": "stewart.session-capture/1", "run": binding["run"],
                  "session_id": binding["session_id"], "captured_at_utc": now(),
                  "event": event.get("hook_event_name", "manual-refresh"), "turn_id": event.get("turn_id"),
                  "source_transcript": str(source), "source_size_at_read": size,
                  "saved_bytes": len(data), "omitted_partial_tail_bytes": tail, "sha256": digest(data),
                  "partial_tail_file": "partial-tail-" + digest(tail_bytes) + ".bin" if tail else None,
                  "partial_tail_sha256": digest(tail_bytes) if tail else None,
                  "source_identity": "session_meta ID verified" if source_id else "hook-supplied association; metadata format unrecognized",
                  "previous_snapshot_is_prefix": previous_is_prefix,
                  "last_assistant_message_present": present, "expected_last_assistant_sha256": expected_sha,
                  "unparsed_jsonl_lines": malformed, 'reader_sha256': digest(readable),
                  "scope": "Full session as of capture, including prior history if the thread was reused. This is not a claim that the run completed."}
        if binding.get('transcript_path') != str(source):
            binding.setdefault('transcript_path_history', []).append({'previous': binding.get('transcript_path'),
                'current': str(source), 'updated_at_utc': now()})
            binding['transcript_path'] = str(source)
            atomic(folder / 'binding.json', json_bytes(binding))
        atomic(previous, data)
        if tail and not (folder / record["partial_tail_file"]).exists():
            atomic(folder / record["partial_tail_file"], tail_bytes)
        atomic(folder / "conversation.txt", readable)
        atomic(folder / "capture.json", json_bytes(record))
        log = folder / "captures.jsonl"
        if log.exists():
            regular(log)
        with log.open("ab") as stream:
            stream.write((json.dumps(record, ensure_ascii=False) + "\n").encode())
        return record

    def register(self, event, run):
        sid = event.get("session_id")
        state_path = self.registry(sid)
        if self.archived(run):
            raise ValueError('Run number already exists in the archive; choose a new run number')
        root = self.run_path(run)
        instructions = (root / 'INSTRUCTIONS.md').read_text()
        destinations = re.findall(r'(run-[0-9]{3,6})/(?:narrative\.md|scratch(?:/|`)|notes(?:/|`)|git-commands\.log)', instructions)
        if any(value != run for value in destinations):
            raise ValueError('Recording instructions name another run; correct the destination paths before registering')
        for receipt in root.glob('session-history/*/replacement.json'):
            if (receipt.parent / 'session').exists() and read_json(receipt)['session_id'] == sid:
                raise ValueError('This session was replaced; register the replacement from a fresh session')
        folder = root / "session"
        if folder.is_symlink():
            raise ValueError("Session directory cannot be a symlink")
        if (folder / "finalized.json").exists() or (folder / "ended.json").exists():
            raise ValueError("This run's capture is closed; use a new run directory")
        binding_path = folder / "binding.json"
        if binding_path.exists():
            if read_json(binding_path)["session_id"] != sid:
                raise ValueError("This run is already bound to another session")
            atomic(state_path, json_bytes({'run': run, 'session_id': sid, 'active_turn': True,
                                          'last_prompt_turn_id': event.get('turn_id')}))
            return
        if folder.exists() and any(folder.iterdir()):
            raise ValueError("Session destination contains files without a collector binding")
        old = read_json(state_path) if state_path.exists() else None
        if old and old["run"] != run:
            old_root = self.fieldwork / old["run"]
            if old_root.is_dir():
                self.run_path(old["run"])
                if not (old_root / "session/finalized.json").exists() and not (old_root / "session/ended.json").exists():
                    prior = self.capture(old_root, {"hook_event_name": "run-marker-changed"})
                    atomic(old_root / "session/ended.json", json_bytes({"ended_at_utc": now(), "next_run": run,
                           "next_start_turn_id": event.get("turn_id"), "capture_sha256": prior["sha256"],
                           "meaning": "Registration switched to another run; no later turns refresh this capture. The boundary prompt may be present in the full-session snapshot."}))
        folder.mkdir(mode=0o700, exist_ok=True)
        binding = {"schema": "stewart.session-binding/1", "run": run, "session_id": sid,
                   "registered_at_utc": now(), "start_turn_id": event.get("turn_id"),
                   "run_directory_at_registration": str(root), "transcript_path": event.get("transcript_path"),
                   "marker": MARKER + run, "cwd": event.get("cwd"), "model": event.get("model"),
                   "scope": "Full-session copies may contain earlier tasks; start_turn_id identifies this run's registration boundary."}
        atomic(binding_path, json_bytes(binding))
        atomic(state_path, json_bytes({'run': run, 'session_id': sid, 'active_turn': True,
                                      'last_prompt_turn_id': event.get('turn_id')}))

    def hook(self, event):
        name = event.get("hook_event_name")
        if name == "UserPromptSubmit":
            prompt = event.get("prompt", "")
            if not isinstance(prompt, str):
                return {}
            state_path = self.registry(event.get('session_id'))
            if state_path.exists():
                with self.locked():
                    state = read_json(state_path)
                    if not state.get('retired'):
                        state.update(active_turn=True, last_prompt_turn_id=event.get('turn_id'))
                        atomic(state_path, json_bytes(state))
            first = next((line for line in prompt.splitlines() if line.strip()), "")
            if not first.startswith(MARKER):
                if re.search(r'(?im)^\s*(?:[*_>`~]+\s*)?(?:Stewart-Fieldwork-Run|Fieldwork-Run)\s*:', prompt):
                    return {'systemMessage': 'Stewart fieldwork capture: no new registration. Use exactly Stewart-Fieldwork-Run: run-NNN as the complete first nonblank line, without formatting.'}
                return {}
            run = first[len(MARKER):]
            if not RUN.fullmatch(run):
                raise ValueError("Use exactly Stewart-Fieldwork-Run: run-004 on the first nonblank line")
            with self.locked():
                self.register(event, run)
            return {}
        if name not in ("Stop", "Interrupt", "SessionEnd"):
            return {}
        sid = event.get("session_id")
        state_path = self.registry(sid)
        if not state_path.exists():
            return {}
        with self.locked():
            state = read_json(state_path)
            if state.get('retired'):
                return {}
            run = state['run']
            if not (self.fieldwork / run).exists():
                if (self.analysis / 'runs' / run).is_dir():
                    return {}  # Migrated evidence stays immutable.
                return {'systemMessage': f'Stewart fieldwork capture: active packet {run} is missing; it was not found in the archive either. Restore its directory before refreshing.'}
            root = self.run_path(run)
            if (root / 'session/finalized.json').exists():
                if name == 'Stop' and not state.get('post_finalization_warned'):
                    state.update(post_finalization_warned=True, post_finalization_event=event.get('turn_id'), active_turn=False)
                    atomic(state_path, json_bytes(state))
                    return {'systemMessage': f'Stewart fieldwork capture: {run} is finalized. This later Stop cannot extend it. Use status and supplement --reason to preserve additional activity.'}
                return {}
            record = self.capture(root, event)
            state.update(active_turn=False, last_terminal_event=name)
            atomic(state_path, json_bytes(state))
            if record.get("last_assistant_message_present") is False:
                return {"systemMessage": "Stewart capture saved, but the final assistant message was not yet found. Refresh before migration."}
        return {}

    def command(self, name, run, reason=None, transcript_path=None, relationship=None, session_id=None):
        if name == 'status':
            return self.status(run)
        if self.archived(run) and (self.fieldwork / run).exists():
            raise ValueError('Run identity collides with the archive; no active packet was changed')
        root = self.run_path(run)
        folder = root / "session"
        with self.locked():
            if name == 'adopt':
                if not reason or not session_id or not transcript_path:
                    raise ValueError('adopt requires --session-id, --transcript-path and --reason')
                source = self.source(transcript_path)
                with source.open('rb') as stream:
                    first = json.loads(stream.readline())
                identity = first.get('payload', {})
                if first.get('type') != 'session_meta' or identity.get('id', identity.get('session_id')) != session_id:
                    raise ValueError('Manual adoption requires matching session_meta identity')
                self.register({'session_id': session_id, 'transcript_path': str(source), 'turn_id': None}, run)
                binding = read_json(folder / 'binding.json')
                binding['adoption'] = {'reason': reason, 'at_utc': now(), 'boundary': 'Unknown original registration boundary; full persisted session retained'}
                atomic(folder / 'binding.json', json_bytes(binding))
                return self.capture(root, {'hook_event_name': 'manual-adopt'})
            if name == 'replace':
                if not reason or relationship not in ('continuation', 'new-encounter'):
                    raise ValueError('replace requires --reason and --relationship continuation|new-encounter')
                if (folder / 'finalized.json').exists():
                    raise ValueError('Capture is finalized; use a new run, or supplement its frozen record')
                binding = read_json(folder / 'binding.json')
                target = root / 'session-history' / uuid.uuid4().hex
                target.mkdir(parents=True)
                receipt = {'schema': 'stewart.session-replacement/1', 'run': run, 'session_id': binding['session_id'],
                           'reason': reason, 'relationship': relationship, 'at_utc': now(),
                           'phase': 'prepared',
                           'meaning': 'Capture displaced; narrative and scratch retained unchanged'}
                atomic(target / 'replacement.json', json_bytes(receipt))
                folder.rename(target / 'session')
                receipt['phase'] = 'preserved'
                atomic(target / 'replacement.json', json_bytes(receipt))
                atomic(self.registry(binding['session_id']), json_bytes({'run': run, 'session_id': binding['session_id'],
                       'retired': True, 'replacement_receipt': str(target / 'replacement.json')}))
                return dict(receipt, preserved_at=str(target), next='Submit the same marker from the replacement session')
            if name == 'supplement':
                if not reason or not (folder / 'finalized.json').exists():
                    raise ValueError('supplement requires a finalized active packet and --reason')
                target = root / 'session-supplements' / uuid.uuid4().hex
                (target / 'session').mkdir(parents=True)
                atomic(target / 'session/binding.json', (folder / 'binding.json').read_bytes())
                record = self.capture(target, {'hook_event_name': 'manual-supplement', 'transcript_path': transcript_path})
                receipt = {'schema': 'stewart.session-supplement/1', 'reason': reason, 'at_utc': now(),
                           'frozen_sha256': read_json(folder / 'finalized.json')['capture_sha256'], 'supplement_sha256': record['sha256']}
                atomic(target / 'supplement.json', json_bytes(receipt))
                return dict(receipt, path=str(target))
            if (folder / "finalized.json").exists():
                if self.status(run).get('snapshot_consistent') is not True:
                    raise ValueError('Finalized snapshot is inconsistent; frozen bytes were not changed')
                return read_json(folder / "finalized.json")
            record = self.capture(root, {'hook_event_name': 'manual-' + name, 'transcript_path': transcript_path})
            if name == "finalize":
                data = regular(folder / "transcript.jsonl").read_bytes()
                if digest(data) != record["sha256"]:
                    raise ValueError("Capture bytes do not match capture.json")
                problems, terminal = [], None
                for line in regular(folder / 'captures.jsonl').read_bytes().splitlines():
                    item = json.loads(line)
                    if item['event'] in ('Stop', 'SessionEnd'):
                        terminal = item
                registry = self.registry(record['session_id'])
                state = read_json(registry) if registry.exists() else {}
                if not (folder / 'ended.json').exists():
                    if state.get('active_turn'):
                        problems.append('A prompt was submitted without a later terminal callback')
                    if not terminal:
                        problems.append('No successful Stop or SessionEnd snapshot')
                    else:
                        size = terminal['saved_bytes']
                        if digest(data[:size]) != terminal['sha256']:
                            problems.append('Source changed since the terminal snapshot')
                        else:
                            for raw in data[size:].splitlines():
                                try:
                                    item = json.loads(raw)
                                    allowed = item.get('type') == 'event_msg' and item.get('payload', {}).get('type') == 'task_complete'
                                except (ValueError, AttributeError):
                                    allowed = False
                                if not allowed:
                                    problems.append('Additional records beyond the terminal snapshot (not just task_complete)')
                                    break
                if record.get('last_assistant_message_present') is False:
                    problems.append('Expected assistant message is missing')
                if record.get('omitted_partial_tail_bytes') or record.get('unparsed_jsonl_lines'):
                    problems.append('Partial or unparsed transcript records remain')
                if problems and not reason:
                    raise ValueError('Not finalized: ' + '; '.join(problems) + '. Wait/refresh, or use --allow-incomplete with a reason for an intentional boundary.')
                final = {"schema": "stewart.session-finalization/2", "run": run, "session_id": record["session_id"],
                         "finalized_at_utc": now(), "capture_sha256": record["sha256"], "saved_bytes": len(data),
                         'boundary': {'terminal_event': terminal, 'ended': (folder / 'ended.json').exists(),
                                      'warnings': problems, 'override_reason': reason},
                         "meaning": "Snapshot frozen for preservation; does not assert scientific completion."}
                atomic(folder / "finalized.json", json_bytes(final))
                return final
            return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("hook", "status", "refresh", "finalize", 'replace', 'supplement', 'adopt'))
    parser.add_argument("run", nargs="?")
    parser.add_argument('--fieldwork-root', type=Path, default=ANALYSIS.parent / 'stewart-fieldwork')
    parser.add_argument('--analysis-root', type=Path, default=ANALYSIS)
    parser.add_argument('--codex-home', type=Path, default=CODEX_HOME)
    parser.add_argument('--transcript-path')
    parser.add_argument('--session-id')
    parser.add_argument('--reason')
    parser.add_argument('--relationship', choices=('continuation', 'new-encounter'))
    parser.add_argument('--allow-incomplete', metavar='REASON')
    args = parser.parse_args()
    collector = Collector(args.fieldwork_root, args.codex_home, args.analysis_root)
    if args.command == "hook":
        try:
            event = json.load(sys.stdin)
            result = collector.hook(event)
        except Exception as error:
            result = {"systemMessage": "Stewart fieldwork capture: " + str(error)}
        print(json.dumps(result))  # Never block, continue a turn, or inject instructions.
        return
    if not args.run:
        parser.error("run is required for status, refresh and finalize")
    try:
        print(json.dumps(collector.command(args.command, args.run, reason=args.allow_incomplete or args.reason,
                                          transcript_path=args.transcript_path, relationship=args.relationship,
                                          session_id=args.session_id), indent=2))
    except (ValueError, OSError, KeyError) as error:
        parser.exit(1, str(error) + "\n")


if __name__ == "__main__":
    main()
