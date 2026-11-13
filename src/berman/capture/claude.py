"""Claude's per-launch capture adapter; no shared Claude or Codex hooks are installed.

The existing routed collector supplies packet locking, status and preservation commands.
Claude transcript discovery, registration and rendering are explicit here. Raw JSONL is
always retained; the reading copy is derived and never substitutes for it.
"""
import json
from pathlib import Path
import time

from berman import files
from berman.capture.runtime import LegacyCollector
from berman._legacy.api import RUN

ADAPTER = 'berman-claude'
MARKER = 'Berman-Claude-Fieldwork-Run: '
EVENTS = ('SessionStart', 'UserPromptSubmit', 'Stop', 'StopFailure', 'PreCompact', 'PostCompact', 'SessionEnd')


def render(data):
    lines = ['CLAUDE FIELDWORK SESSION: DERIVED READING COPY', '',
             'Source: transcript.jsonl. Blocks cite raw JSONL lines and JSON pointers.',
             'Visible messages and tool calls/results. Other content remains in the raw source.', '']
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
            lines += ['=' * 72, f'SOURCE: transcript.jsonl#L{number} {pointer}',
                      f'TIME: {event.get("timestamp", "not recorded")} | ROLE: {role} | KIND: {kind}',
                      f'NAME: {block.get("name", "")} | CALL ID: {block.get("id", block.get("tool_use_id", ""))}',
                      value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2), '']
        if role == 'assistant':
            messages.append(''.join(texts))
    return ('\n'.join(lines) + '\n').encode(), messages, malformed, identities


class ClaudeCollector(LegacyCollector):
    adapter = ADAPTER
    marker = MARKER

    def __init__(self, fieldwork, claude_home, analysis):
        self.claude_home = Path(claude_home)
        # Registries and session locks belong to Berman, not the user's shared harness home.
        super().__init__(fieldwork, Path(analysis) / '.claude-fieldwork', analysis)
        self.state = self.codex_home / 'sessions'

    def for_run(self, run):
        result = type(self)(self.fieldwork, self.claude_home, self.analysis)
        result.bound_run = run
        return result

    def check_owner(self, sid):
        self.registry(sid)  # Validate identity; this adapter has its own registry namespace.

    def run_path(self, run, readonly=False):
        if not isinstance(run, str) or not RUN.fullmatch(run):
            raise ValueError('Invalid run identity')
        root = self.fieldwork / run
        if readonly and not root.exists():
            root = self.analysis / 'runs' / run
        files.safe_directory(root)
        files.regular(root / 'INSTRUCTIONS.md')
        files.safe_directory(root / 'notes')
        return root

    def source(self, value):
        if not isinstance(value, str) or not value:
            raise ValueError('Claude has not supplied a transcript path')
        path = Path(value)
        if not path.is_absolute() or '..' in path.parts or not path.is_relative_to(self.claude_home / 'projects'):
            raise ValueError('Claude transcript must be beneath the recorded Claude projects store')
        return files.regular(path)

    def find_source(self, binding, supplied=None):
        value = supplied or binding.get('transcript_path')
        if not value:
            raise ValueError('No Claude transcript path recorded; wait for a callback')
        path = self.source(value)
        if path.name != binding['session_id'] + '.jsonl':
            raise ValueError('Claude transcript filename differs from the bound session identity')
        return path

    def owner(self, run, sid):
        record = files.read(self.analysis / 'operations' / run / 'operation.json')
        if (record.get('run') != run or record.get('capture', {}).get('adapter') != ADAPTER
                or record.get('packet') != str(self.fieldwork / run)
                or record.get('harness', {}).get('home') != str(self.claude_home)
                or record.get('harness', {}).get('session_id') != sid):
            raise ValueError('Claude hook does not match its reserved packet, harness or session')
        return record

    def register(self, event, run):
        sid = event['session_id']
        self.owner(run, sid)
        if self.archived(run):
            raise ValueError('Claude run is already archived')
        root = self.run_path(run)
        folder = root / 'session'
        if folder.is_symlink():
            raise ValueError('Session destination is a symlink')
        if any((folder / name).exists() for name in ('finalized.json', 'ended.json')):
            raise ValueError('Capture is closed; use a supplement for later activity')
        path = folder / 'binding.json'
        if path.exists():
            if files.read(path)['session_id'] != sid:
                raise ValueError('Packet is already bound to another session')
        else:
            if folder.exists() and any(folder.iterdir()):
                raise ValueError('Unbound session files already exist')
            folder.mkdir(mode=0o700, exist_ok=True)
            files.atomic(path, files.json_bytes({
                'schema': 'berman.session-binding/1', 'run': run, 'session_id': sid,
                'registered_at_utc': files.now(), 'start_turn_id': None,
                'run_directory_at_registration': str(root), 'transcript_path': event.get('transcript_path'),
                'marker': MARKER + run, 'collector': ADAPTER, 'harness': 'claude',
                'workspace': str(self.analysis), 'cwd': event.get('cwd'),
                'scope': 'Reserved fresh Claude session. Full parent transcript; child conversations are not implied.'}))
        registry = self.registry(sid)
        previous = files.read(registry) if registry.exists() else {}
        if previous.get('run', run) != run:
            raise ValueError('Claude session belongs to another run')
        files.atomic(registry, files.json_bytes(dict(previous, run=run, session_id=sid,
                     active_turn=True, workspace=str(self.analysis), harness='claude',
                     adapter=ADAPTER, packet=str(root))))

    def hook(self, event):
        name, sid = event.get('hook_event_name'), event.get('session_id')
        if name not in EVENTS:
            return {}
        run = self.bound_run
        self.owner(run, sid)
        if self.archived(run):
            return {}  # Preserved evidence is immutable.
        with self.session_lock(sid), self.run_scope(run), self.locked():
            root = self.run_path(run)
            folder = root / 'session'
            if (folder / 'finalized.json').exists():
                return {'systemMessage': f'Berman: {run} is frozen; use berman supplement to retain later activity.'}
            if name == 'UserPromptSubmit':
                if not (folder / 'binding.json').exists():
                    prompt = event.get('prompt', '')
                    first = next((s for s in prompt.splitlines() if s.strip()), '') if isinstance(prompt, str) else ''
                    if first != MARKER + run:
                        raise ValueError('The first prompt must be this run\'s exact prepared assignment')
                self.register(event, run)
            if not (folder / 'binding.json').exists():
                return {}  # SessionStart precedes the researcher-prepared first prompt.
            hook_path = folder / 'hook-events.jsonl'
            if hook_path.exists():
                files.regular(hook_path)
            with hook_path.open('ab') as stream:
                stream.write(files.json_bytes(event).replace(b'\n', b'') + b'\n')
            if name == 'UserPromptSubmit':
                return {}
            self.capture(root, event)
            if name in ('Stop', 'StopFailure', 'SessionEnd'):
                state = files.read(self.registry(sid))
                state.update(active_turn=False, last_terminal_event=name)
                files.atomic(self.registry(sid), files.json_bytes(state))
        return {}

    def capture(self, root, event):
        folder = root / 'session'
        if any((folder / name).exists() for name in ('finalized.json', 'ended.json')):
            return files.read(folder / 'capture.json')
        binding = files.read(folder / 'binding.json')
        source = self.find_source(binding, event.get('transcript_path'))
        old = files.read(folder / 'capture.json') if (folder / 'capture.json').exists() else {}
        expected = event.get('last_assistant_message')
        expected_sha = files.digest(expected.encode()) if isinstance(expected, str) and expected else old.get('expected_last_assistant_sha256')
        for attempt in range(3):
            raw = source.read_bytes()
            end = raw.rfind(b'\n') + 1
            data, tail = raw[:end], raw[end:]
            if not data:
                raise ValueError('No complete Claude transcript records yet')
            readable, messages, malformed, identities = render(data)
            if identities != {binding['session_id']}:
                raise ValueError('Claude transcript lacks the bound session identity or contains another session')
            present = any(files.digest(m.encode()) == expected_sha for m in messages) if expected_sha else None
            if expected and not present and attempt < 2:
                time.sleep(0.08)
                continue
            break
        previous = folder / 'transcript.jsonl'
        prefix = None
        if previous.exists():
            prior = files.regular(previous).read_bytes()
            prefix = data.startswith(prior)
            if not prefix:
                files.keep(folder / ('previous-' + files.digest(prior) + '.jsonl'), prior)
        tail_name = 'partial-tail-' + files.digest(tail) + '.bin' if tail else None
        record = {'schema': 'berman.session-capture/1', 'harness': 'claude', 'run': binding['run'],
                  'session_id': binding['session_id'], 'captured_at_utc': files.now(),
                  'event': event.get('hook_event_name', 'manual-refresh'), 'turn_id': None,
                  'source_transcript': str(source), 'source_size_at_read': len(raw),
                  'saved_bytes': len(data), 'omitted_partial_tail_bytes': len(tail),
                  'sha256': files.digest(data), 'partial_tail_file': tail_name,
                  'partial_tail_sha256': files.digest(tail) if tail else None,
                  'source_identity': 'Exact reserved filename and every observed sessionId verified',
                  'previous_snapshot_is_prefix': prefix, 'last_assistant_message_present': present,
                  'expected_last_assistant_sha256': expected_sha, 'unparsed_jsonl_lines': malformed,
                  'reader_sha256': files.digest(readable),
                  'scope': 'Persisted parent session at capture time; no assertion of research completion or child coverage.'}
        if binding.get('transcript_path') != str(source):
            binding.setdefault('transcript_path_history', []).append({'previous': binding.get('transcript_path'),
                            'current': str(source), 'updated_at_utc': files.now()})
            binding['transcript_path'] = str(source)
            files.atomic(folder / 'binding.json', files.json_bytes(binding))
        files.atomic(previous, data)
        if tail:
            files.keep(folder / tail_name, tail)
        files.atomic(folder / 'conversation.txt', readable)
        files.atomic(folder / 'capture.json', files.json_bytes(record))
        log = folder / 'captures.jsonl'
        if log.exists():
            files.regular(log)
        with log.open('ab') as stream:
            stream.write(json.dumps(record, ensure_ascii=False).encode() + b'\n')
        return record

    def command(self, name, run, **kwargs):
        if name in ('adopt', 'replace'):
            raise ValueError('Claude capture uses its reserved session identity; adoption/replacement is not supported')
        return super().command(name, run, **kwargs)
