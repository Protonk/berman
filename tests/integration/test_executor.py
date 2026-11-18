"""Drive real subprocess and hook entry points with disposable native homes and synthetic transcripts."""

import concurrent.futures
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from berman import executor, files, protocol
from berman.executor import capture

# This executable generates its own callback sequence and a native source tree.
# The executor has no testing-only launch path.
HARNESS = r"""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

argv = sys.argv[1:]
if argv == ['--version']:
    print('synthetic harness 1')
    raise SystemExit(0)
claude = '--session-id' in argv
home = Path(os.environ['CLAUDE_CONFIG_DIR' if claude else 'CODEX_HOME'])
sid = argv[argv.index('--session-id') + 1] if claude else 'parent-session'
settings = Path(argv[argv.index('--settings') + 1]) if claude else home / 'hooks.json'
hooks = json.loads(settings.read_text())['hooks']
packet = Path(argv[argv.index('--add-dir') + 1])
assert 'PreToolUse' not in hooks
assert '{{notes_directory}}' not in (packet / 'INSTRUCTIONS.md').read_text()
(packet / 'notes' / 'account.md').write_text('fixture note')
if claude:
    src = home / 'projects' / 'checkout' / (sid + '.jsonl')
    first = {'sessionId': sid, 'type': 'assistant', 'message': {'role': 'assistant', 'content': 'ok'}}
    child = src.parent / sid / 'subagents' / 'agent-child.jsonl'
    child_data = {'sessionId': sid, 'type': 'assistant'}
else:
    src = home / 'sessions' / 'rollout-parent.jsonl'
    first = {'type': 'session_meta', 'payload': {'id': sid}}
    child = src.parent / 'rollout-child.jsonl'
    child_data = {'type': 'session_meta', 'payload': {'id': 'child', 'parent_thread_id': sid}}
src.parent.mkdir(parents=True, exist_ok=True)
src.write_text(json.dumps(first) + '\n')
if not claude:
    with src.open('a') as out:
        out.write(json.dumps({'type': 'turn_context', 'payload': {'sandbox_policy': {'type': 'workspace-write'}}}) + '\n')
child.parent.mkdir(parents=True, exist_ok=True)
child.write_text(json.dumps(child_data) + '\n')
for event in ['UserPromptSubmit', 'Stop', 'SessionEnd']:
    e = {'session_id': sid, 'hook_event_name': event, 'cwd': os.getcwd(), 'transcript_path': str(src)}
    if claude:
        e['permission_mode'] = 'default'
    if event == 'UserPromptSubmit':
        e['prompt'] = argv[-1]
    result = subprocess.run(shlex.split(hooks[event][0]['hooks'][0]['command']), input=json.dumps(e),
                            capture_output=True, text=True, check=True)
    assert json.loads(result.stdout) == {}, result
"""


class ExecutorTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.project = self.root / 'project'
        self.home = self.root / 'native home'
        self.spool = self.root / 'offline staging'
        self.executable = self.root / 'fixture harness'
        self.home.mkdir()
        self.project.mkdir()
        (self.project / 'README.md').write_text('checkout context')
        for args in (
            ['init', '-q'],
            ['add', 'README.md'],
            [
                '-c',
                'user.name=Fixture',
                '-c',
                'user.email=f@example.invalid',
                '-c',
                'commit.gpgsign=false',
                'commit',
                '-qm',
                'fixture',
            ],
        ):
            subprocess.run(['git', '-C', self.project, *args], check=True, capture_output=True)
        self.executable.write_text('#!' + sys.executable + '\n' + HARNESS)
        self.executable.chmod(0o700)
        self.op = str(uuid4())
        self.ending = str(uuid4())
        self.a = dict(
            schema='berman.assignment/1',
            collection_id=str(uuid4()),
            operation=self.op,
            run='run-001',
            connection='example',
            harness='codex',
            documents=['README.md', 'missing.txt'],
            checks=[{'id': 'git_version', 'argv': ['{executable:git}', '--version'], 'stdout_contains': 'git version'}],
            instructions='Read README.md. Write an observation in {{notes_directory}}.',
        )
        self.b = dict(
            connection='example', executable=str(self.executable), home=str(self.home), project=str(self.project)
        )

    def prepare(self):
        if self.a['harness'] == 'codex':
            files.write(self.home / 'hooks.json', capture.definition(self.spool, self.home))
        return executor.prepare(self.spool, self.a, self.b)

    def exercise(self, harness, *, one_turn, child):
        self.a['harness'] = harness
        self.b['policy'] = 'default' if harness == 'claude' else 'workspace-write'
        p = self.prepare()
        self.assertEqual(p, self.prepare())
        self.assertTrue(p['context']['files'][1]['missing'])
        launch = executor.launch(self.spool, self.op, one_turn=one_turn)
        self.assertEqual(launch['exit']['returncode'], 0)
        self.assertTrue(launch['session_id'])
        self.assertEqual(launch['intent']['one_turn'], one_turn)
        self.assertEqual(launch, executor.launch(self.spool, self.op, one_turn=one_turn))
        with self.assertRaisesRegex(ValueError, 'another mode'):
            executor.launch(self.spool, self.op, one_turn=not one_turn)
        frozen = executor.collect(self.spool, self.op, self.ending)
        self.assertEqual([], frozen['limitations'])
        inventory = protocol.inventory(frozen['members'])
        self.assertIn('packet/notes/account.md', inventory)
        self.assertIn('associated/' + child + '/transcript.jsonl', inventory)
        self.assertIn('final-project/README.md', inventory)
        self.assertEqual(frozen['assignment_sha256'], files.digest_json(self.a))
        self.assertEqual(frozen['events'], ['UserPromptSubmit', 'Stop', 'SessionEnd'])
        self.assertEqual([child], frozen['associated_sessions'])
        q = executor.qualify(self.spool, self.op, self.ending, expected_child=child)
        self.assertTrue(q['passed'], q)
        self.assertFalse(executor.qualify(self.spool, self.op, self.ending, expected_child='unobserved')['passed'])
        (Path(p['packet']) / 'notes/account.md').write_text('late note')
        self.assertEqual(frozen, executor.collect(self.spool, self.op, self.ending))
        path = executor.payload_path(self.spool, self.op, self.ending, 'packet/notes/account.md')
        self.assertEqual(path.read_text(), 'fixture note')
        for name in ('../assignment.json', 'session-id.json'):
            with self.assertRaises(ValueError):
                executor.payload_path(self.spool, self.op, self.ending, name)
        with self.assertRaisesRegex(ValueError, 'another ending'):
            executor.collect(self.spool, self.op, str(uuid4()))
        path.write_bytes(b'altered')
        with self.assertRaisesRegex(ValueError, 'differs'):
            executor.payload_path(self.spool, self.op, self.ending, 'packet/notes/account.md')
        self.assertFalse(any(self.project.rglob('assignment.json')))
        return launch

    def test_codex_one_turn(self):
        launch = self.exercise('codex', one_turn=True, child='child')
        self.assertIn('exec', launch['intent']['argv'])
        self.assertNotIn('--trust', launch['intent']['argv'])

    def test_claude_one_turn(self):
        launch = self.exercise('claude', one_turn=True, child='agent-child')
        self.assertIn('--print', launch['intent']['argv'])
        self.assertIn('--max-turns', launch['intent']['argv'])
        self.assertFalse((self.home / 'settings.json').exists())

    def test_interactive_default_missing_primary_bytes(self):
        self.a['harness'] = 'claude'
        p = self.prepare()
        launch = executor.launch(self.spool, self.op)
        self.assertNotIn('--print', launch['intent']['argv'])
        self.assertNotIn('--permission-mode', launch['intent']['argv'])
        source = next((self.home / 'projects').glob('*/*.jsonl'))
        source.unlink()
        frozen = executor.collect(self.spool, self.op, self.ending)
        self.assertTrue(any('Closeout parent observation' in v for v in frozen['limitations']))
        self.assertTrue(frozen['parent_sha256'])
        self.assertEqual(p['reserved_session_id'], frozen['session_id'])

    def test_other_parent_cannot_bind(self):
        p = self.prepare()
        folder = capture.operation_dir(self.spool, self.op)
        files.write(folder / 'launch-intent.json', {})
        e = dict(hook_event_name='UserPromptSubmit', prompt=p['prompt'], cwd=str(self.project), session_id='one')
        for wrong in [dict(prompt='ordinary task'), dict(cwd=str(self.root)), dict(prompt=p['prompt'] + ' extra')]:
            self.assertEqual({}, capture.route(self.spool, self.home, dict(e, **wrong)))
        self.assertFalse((folder / 'events').exists())
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            list(pool.map(lambda sid: capture.route(self.spool, self.home, dict(e, session_id=sid)), ['one', 'two']))
        self.assertEqual(1, len(list((folder / 'events').glob('*.json'))))
        sid = files.read(folder / 'session-id.json')
        wrong = 'two' if sid == 'one' else 'one'
        capture.route(self.spool, self.home, dict(e, hook_event_name='Stop', session_id=wrong))
        self.assertEqual(1, len(list((folder / 'events').glob('*.json'))))
        frozen = executor.collect(self.spool, self.op, self.ending)
        self.assertTrue(frozen['limitations'])
        self.assertIsNone(frozen['parent_sha256'])

    def test_claude_child_hook_and_ordinary_session_ignored(self):
        self.a['harness'] = 'claude'
        self.prepare()
        folder = capture.operation_dir(self.spool, self.op)
        files.write(folder / 'launch-intent.json', {})
        e = dict(session_id=str(uuid4()), hook_event_name='Stop', transcript_path=str(self.root / 'elsewhere'))
        self.assertEqual({}, capture.route(self.spool, self.home, e, self.op))
        self.assertFalse((folder / 'events').exists())

    def test_start_interruption_cannot_repeat_spawn(self):
        self.prepare()
        with patch('berman.executor.subprocess.Popen', side_effect=OSError('spawn failed')) as start:
            # fingerprint also runs a version subprocess. Bypass just that observation in this fault injection.
            with patch('berman.executor.fingerprint', return_value={}):
                with self.assertRaisesRegex(OSError, 'spawn failed'):
                    executor.launch(self.spool, self.op)
                launch = executor.launch(self.spool, self.op)
            self.assertEqual(1, start.call_count)
        self.assertIsNone(launch['started'])
        self.assertIn('spawn failed', launch['exit']['error'])
        self.assertIsNone(launch['session_id'])

    def test_mismatch_collision_exclusive_hook_and_bounds(self):
        with self.assertRaisesRegex(ValueError, 'connection'):
            executor.prepare(self.spool, self.a, dict(self.b, connection='other'))
        p = self.prepare()
        with self.assertRaisesRegex(ValueError, 'differs'):
            executor.prepare(self.spool, dict(self.a, documents=[]), self.b)
        hooks = files.read(self.home / 'hooks.json')
        hooks['hooks']['Stop'].append(hooks['hooks']['Stop'][0])
        files.write(self.home / 'hooks.json', hooks)
        with self.assertRaisesRegex(ValueError, 'exclusive'):
            executor.launch(self.spool, self.op)
        with patch.object(protocol, 'MEMBER_LIMIT', 1), self.assertRaisesRegex(ValueError, 'count limit'):
            executor.collect(self.spool, self.op, self.ending)
        (Path(p['packet']) / 'notes' / 'link').symlink_to(self.executable)
        with self.assertRaisesRegex(ValueError, 'Symlink'):
            executor.collect(self.spool, self.op, self.ending)
        self.assertFalse((capture.operation_dir(self.spool, self.op) / 'collection').exists())

    def test_relocated_source_displaced_revision_and_policy_mismatch(self):
        self.b['policy'] = 'read-only'
        self.prepare()
        executor.launch(self.spool, self.op, one_turn=True)
        original = self.home / 'sessions/rollout-parent.jsonl'
        archive = self.home / 'archived_sessions/relocated-parent-session.jsonl'
        archive.parent.mkdir()
        original.rename(archive)
        # Relocation uses exact native metadata. The new observation keeps a partial line
        # and displaces the earlier, differently ordered bytes.
        archive.write_bytes(archive.read_bytes().replace(b'workspace-write', b'read-only') + b'partial')
        frozen = executor.collect(self.spool, self.op, self.ending)
        names = protocol.inventory(frozen['members'])
        self.assertTrue(any('/displaced/' in n for n in names))
        tail = next(n for n in names if '/partial-tail-' in n)
        self.assertEqual(executor.payload_path(self.spool, self.op, self.ending, tail).read_bytes(), b'partial')
        q = executor.qualify(self.spool, self.op, self.ending)
        self.assertFalse(q['checks']['capture_intact'])
        self.assertFalse(q['passed'])

    def test_policy_qualification_requires_native_confirmation(self):
        self.a['harness'] = 'claude'
        self.b['policy'] = 'plan'
        self.prepare()
        executor.launch(self.spool, self.op, one_turn=True)
        executor.collect(self.spool, self.op, self.ending)
        q = executor.qualify(self.spool, self.op, self.ending)
        self.assertFalse(q['passed'])
        self.assertEqual(['default'], q['observed_policy'])

    def test_late_hook_and_replaced_transcript_are_not_new_parent_evidence(self):
        self.a['harness'] = 'claude'
        p = self.prepare()
        executor.launch(self.spool, self.op)
        folder = capture.operation_dir(self.spool, self.op)
        sid = p['reserved_session_id']
        path = next((self.home / 'projects').glob('*/*.jsonl'))
        # A callback for a resumed harness after the observed exit is outside this launch.
        self.assertEqual(
            {}, capture.route(self.spool, self.home, dict(session_id=sid, hook_event_name='Stop'), self.op)
        )
        self.assertEqual(3, len(list((folder / 'events').glob('*.json'))))
        source = path.read_bytes()
        path.write_bytes(source.replace(sid.encode(), str(uuid4()).encode()))
        manifest = executor.collect(self.spool, self.op, self.ending)
        self.assertEqual(files.digest(source), manifest['parent_sha256'])
        self.assertTrue(any('identity differs' in msg for msg in manifest['limitations']))
        self.assertFalse(executor.qualify(self.spool, self.op, self.ending)['checks']['closeout_parent_observed'])
