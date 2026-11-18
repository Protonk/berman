"""Drive real subprocess and hook entry points with disposable native homes and synthetic transcripts."""

import concurrent.futures
import os
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
claude = '--session-id' in argv or '--resume' in argv
resumed = '--resume' in argv or 'resume' in argv
home = Path(os.environ['CLAUDE_CONFIG_DIR' if claude else 'CODEX_HOME'])
sid = argv[argv.index('--resume' if '--resume' in argv else '--session-id') + 1] if claude else 'parent-session'
if resumed and not claude:
    assert argv[argv.index('resume') + 1] == sid or sid in argv, argv
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
if resumed:
    assert src.exists(), 'a resumed sitting continues an existing native session'
    with src.open('a') as out:
        out.write(json.dumps({'sessionId': sid, 'type': 'assistant', 'message': {'role': 'assistant', 'content': 'again'}}
                             if claude else {'type': 'response_item', 'payload': {'text': 'again'}}) + '\n')
else:
    src.write_text(json.dumps(first) + '\n')
if not claude and not resumed:
    with src.open('a') as out:
        out.write(json.dumps({'type': 'turn_context', 'payload': {'sandbox_policy': {'type': 'workspace-write'}}}) + '\n')
child.parent.mkdir(parents=True, exist_ok=True)
child.write_text(json.dumps(child_data) + '\n')
timing = os.environ.get('BERMAN_TEST_TRANSCRIPT_TIMING') if claude else None
events = ['UserPromptSubmit', 'Stop', 'SessionEnd']
if timing:
    events.insert(0, 'SessionStart')
    src.unlink()
for event in events:
    if event == 'Stop' and timing == 'at-stop':
        src.write_text(json.dumps(first) + '\n')
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
            checks=[
                {
                    'id': 'git_read',
                    'argv': ['{executable:git}', 'rev-parse', '--is-inside-work-tree'],
                    'stdout_contains': 'true',
                }
            ],
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
        self.assertNotIn('--model', launch['intent']['argv'])
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
        # Berman adds no turn cap of its own to a native run.
        self.assertNotIn('--max-turns', launch['intent']['argv'])
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

    def claude_with_delayed_transcript(self, timing):
        self.a['harness'] = 'claude'
        self.prepare()
        with patch.dict(os.environ, BERMAN_TEST_TRANSCRIPT_TIMING=timing):
            executor.launch(self.spool, self.op)
        return executor.collect(self.spool, self.op, self.ending)

    def test_claude_opening_callbacks_before_transcript_creation(self):
        frozen = self.claude_with_delayed_transcript('at-stop')
        self.assertEqual(
            frozen['limitations'],
            [
                'Transcript file absent at SessionStart, UserPromptSubmit. Verified transcript snapshot at Stop, SessionEnd.'
            ],
        )
        self.assertTrue(executor.qualify(self.spool, self.op, self.ending)['passed'])
        payload = executor.frozen_dir(capture.operation_dir(self.spool, self.op)) / 'payload'
        receipts = [files.read(p) for p in (payload / 'events').glob('*.json')]
        self.assertEqual(
            {r['event']['hook_event_name'] for r in receipts if r.get('transcript_missing') and 'limitation' in r},
            {'SessionStart', 'UserPromptSubmit'},
        )

    def test_claude_parent_transcript_never_appears(self):
        frozen = self.claude_with_delayed_transcript('absent')
        self.assertIn('No verified parent transcript observed.', frozen['limitations'])
        self.assertIn(
            'Transcript file absent at SessionStart, UserPromptSubmit, Stop, SessionEnd.', frozen['limitations']
        )
        self.assertFalse(executor.qualify(self.spool, self.op, self.ending)['passed'])

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
        with self.assertRaisesRegex(ValueError, 'differs'):
            executor.prepare(self.spool, self.a, dict(self.b, model='another-model'))
        hooks = files.read(self.home / 'hooks.json')
        hooks['hooks']['Stop'].append(hooks['hooks']['Stop'][0])
        files.write(self.home / 'hooks.json', hooks)
        with self.assertRaisesRegex(ValueError, 'exclusive'):
            executor.launch(self.spool, self.op)
        self.assertTrue(p['packet'])

    def test_unreadable_members_are_omitted_and_named_not_fatal(self):
        p = self.prepare()
        executor.launch(self.spool, self.op, one_turn=True)
        notes = Path(p['packet']) / 'notes'
        (notes / 'link').symlink_to(self.executable)
        os.mkfifo(notes / 'pipe')
        (notes / 'large.bin').write_bytes(b'x' * 300_000)
        with patch.object(protocol, 'FILE_LIMIT', 200_000):
            frozen = executor.collect(self.spool, self.op, self.ending)
        kinds = {row['path']: row['kind'] for row in frozen['omitted']}
        self.assertEqual(
            {'packet/notes/link': 'symlink', 'packet/notes/pipe': 'special', 'packet/notes/large.bin': 'bound'},
            {k: v for k, v in kinds.items() if k.startswith('packet/notes/')},
        )
        large = next(row for row in frozen['omitted'] if row['path'] == 'packet/notes/large.bin')
        self.assertIn(files.digest(b'x' * 300_000), large['detail'])
        inventory = protocol.inventory(frozen['members'])
        self.assertIn('packet/notes/account.md', inventory)
        self.assertIn('omitted.json', inventory)
        self.assertNotIn('packet/notes/link', inventory)
        self.assertTrue(any('packet/notes/link (symbolic link to' in text for text in frozen['limitations']))
        self.assertEqual(frozen, executor.collect(self.spool, self.op, self.ending))

    def test_member_bound_keeps_receipts_and_names_what_it_left(self):
        self.prepare()
        executor.launch(self.spool, self.op, one_turn=True)
        with patch.object(protocol, 'MEMBER_LIMIT', 6):
            frozen = executor.collect(self.spool, self.op, self.ending)
        names = [row['path'] for row in frozen['members']]
        self.assertEqual(6, len(names))
        self.assertTrue(
            {'launch.json', 'assignment.json', 'preparation.json', 'hooks.json', 'omitted.json'} <= set(names)
        )
        self.assertTrue(frozen['omitted'])
        self.assertTrue(
            any('further members omitted' in t or 'Omitted from this collection' in t for t in frozen['limitations'])
        )
        # The callback names stay in the manifest even when their files did not fit.
        self.assertEqual(frozen['events'], ['UserPromptSubmit', 'Stop', 'SessionEnd'])

    def test_total_bound_includes_the_record_of_omissions(self):
        p = self.prepare()
        executor.launch(self.spool, self.op, one_turn=True)
        notes = Path(p['packet']) / 'notes'
        for index in range(40):
            (notes / f'filler-{index:02d}.txt').write_bytes(bytes([index]) * 2000)
        with patch.object(protocol, 'TOTAL_LIMIT', 60_000):
            frozen = executor.collect(self.spool, self.op, self.ending)
            # The guest applies the same bounds to the inventory it is sent.
            inventory = protocol.inventory(frozen['members'])
        self.assertLessEqual(sum(size for size, _ in inventory.values()), 60_000)
        self.assertIn('omitted.json', inventory)
        self.assertTrue(frozen['omitted'])

    def test_unportable_names_and_unreadable_directories_are_named(self):
        p = self.prepare()
        executor.launch(self.spool, self.op, one_turn=True)
        notes = Path(p['packet']) / 'notes'
        (notes / 'a\\b.md').write_text('a name no portable recording carries')
        closed = notes / 'closed'
        closed.mkdir()
        (closed / 'inside.md').write_text('unseen')
        closed.chmod(0)
        self.addCleanup(closed.chmod, 0o700)
        frozen = executor.collect(self.spool, self.op, self.ending)
        kinds = {row['path']: row['kind'] for row in frozen['omitted']}
        self.assertEqual('name', kinds['packet/notes/a\\b.md'])
        self.assertEqual('unreadable', kinds['packet/notes/closed'])
        self.assertIn('packet/notes/account.md', protocol.inventory(frozen['members']))

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

    def test_callback_after_exit_is_staged_and_replaced_transcript_is_not_parent_evidence(self):
        self.a['harness'] = 'claude'
        p = self.prepare()
        executor.launch(self.spool, self.op)
        folder = capture.operation_dir(self.spool, self.op)
        sid = p['reserved_session_id']
        path = next((self.home / 'projects').glob('*/*.jsonl'))
        # The engagement is still open after the launched process exits. A later callback from
        # the bound parent is kept, with its own verified generation.
        self.assertEqual(
            {}, capture.route(self.spool, self.home, dict(session_id=sid, hook_event_name='Stop'), self.op)
        )
        self.assertEqual(4, len(list((folder / 'events').glob('*.json'))))
        self.assertEqual(
            {}, capture.route(self.spool, self.home, dict(session_id=str(uuid4()), hook_event_name='Stop'), self.op)
        )
        self.assertEqual(4, len(list((folder / 'events').glob('*.json'))))
        source = path.read_bytes()
        path.write_bytes(source.replace(sid.encode(), str(uuid4()).encode()))
        manifest = executor.collect(self.spool, self.op, self.ending)
        self.assertEqual(files.digest(source), manifest['parent_sha256'])
        self.assertEqual(manifest['events'], ['UserPromptSubmit', 'Stop', 'SessionEnd', 'Stop'])
        self.assertTrue(any('identity differs' in msg for msg in manifest['limitations']))
        self.assertFalse(executor.qualify(self.spool, self.op, self.ending)['checks']['closeout_parent_observed'])

    def resume_case(self, harness):
        self.a['harness'] = harness
        self.prepare()
        with self.assertRaisesRegex(ValueError, 'Nothing to resume'):
            executor.launch(self.spool, self.op, resume=True, request='again')
        with self.assertRaisesRegex(ValueError, 'request label'):
            executor.launch(self.spool, self.op, resume=True)
        first = executor.launch(self.spool, self.op, one_turn=True)
        with self.assertRaisesRegex(ValueError, 'needs --prompt'):
            executor.launch(self.spool, self.op, one_turn=True, resume=True, request='again')
        second = executor.launch(
            self.spool, self.op, one_turn=True, resume=True, prompt='continue the work', request='again'
        )
        self.assertEqual(2, second['sitting'])
        # Repeating the same request, as after a lost reply, returns that sitting and starts nothing.
        self.assertEqual(
            second,
            executor.launch(
                self.spool, self.op, one_turn=True, resume=True, prompt='continue the work', request='again'
            ),
        )
        self.assertEqual([1, 2], capture.sittings(capture.operation_dir(self.spool, self.op)))
        self.assertTrue(second['intent']['resumed_native_session'])
        self.assertEqual(second['intent']['argv'][-1], 'continue the work')
        self.assertEqual(first['session_id'], second['session_id'])
        # A plain launch stays a replay of the first sitting and starts nothing.
        self.assertEqual(first, executor.launch(self.spool, self.op, one_turn=True))
        self.assertEqual([first, second], executor.sitting_receipts(self.spool, self.op))
        frozen = executor.collect(self.spool, self.op, self.ending)
        self.assertEqual(frozen['events'], ['UserPromptSubmit', 'Stop', 'SessionEnd'] * 2)
        self.assertIn('sittings.json', protocol.inventory(frozen['members']))
        q = executor.qualify(self.spool, self.op, self.ending)
        self.assertEqual(2, q['sittings'])
        self.assertTrue(q['checks']['later_sittings_observed'])
        return second

    def test_claude_resumed_sitting_keeps_staging(self):
        second = self.resume_case('claude')
        self.assertIn('--resume', second['intent']['argv'])
        self.assertNotIn('--session-id', second['intent']['argv'])

    def test_codex_resumed_sitting_keeps_staging(self):
        second = self.resume_case('codex')
        argv = second['intent']['argv']
        self.assertEqual(argv[argv.index('resume') + 1], 'parent-session')
        self.assertIn('exec', argv)

    def test_interactive_resume_arguments_and_a_first_sitting_that_bound_nothing(self):
        self.b['model'] = 'fixture-model'
        for harness in ('claude', 'codex'):
            with self.subTest(harness=harness):
                self.op = str(uuid4())
                self.a.update(operation=self.op, harness=harness)
                p = self.prepare()
                folder = capture.operation_dir(self.spool, self.op)
                with patch('berman.executor.subprocess.Popen', side_effect=OSError('spawn failed')):
                    with patch('berman.executor.fingerprint', return_value={}):
                        with self.assertRaisesRegex(OSError, 'spawn failed'):
                            executor.launch(self.spool, self.op)
                first = executor.launch(self.spool, self.op)
                self.assertIsNone(first['session_id'])
                # No native session exists, so the next sitting opens with the prepared prompt.
                with self.assertRaisesRegex(ValueError, 'omit --prompt'):
                    executor.launch(self.spool, self.op, resume=True, prompt='too early', request='two')
                second = executor.launch(self.spool, self.op, resume=True, request='two')
                self.assertFalse(second['intent']['resumed_native_session'])
                self.assertEqual(p['prompt'], second['intent']['argv'][-1])
                self.assertTrue(second['session_id'])
                # The first sitting's receipt is the bytes it was when it ended, now that a session is bound.
                self.assertEqual(first, executor.launch(self.spool, self.op))
                self.assertEqual([first, second], executor.sitting_receipts(self.spool, self.op))
                third = executor.launch(self.spool, self.op, resume=True, request='three')
                for sitting in (first, second, third):
                    args = sitting['intent']['argv']
                    self.assertEqual(args[args.index('--model') + 1], self.b['model'])
                argv = third['intent']['argv']
                self.assertTrue(third['intent']['resumed_native_session'])
                self.assertNotIn('--print', argv)
                self.assertNotIn('exec', argv)
                if harness == 'claude':
                    self.assertEqual(p['reserved_session_id'], argv[argv.index('--resume') + 1])
                    self.assertEqual(str(Path(p['packet'])), argv[-1])
                else:
                    self.assertEqual([third['session_id']], argv[-1:])
                    self.assertEqual('resume', argv[1])
                self.assertEqual([1, 2, 3], capture.sittings(folder))

    def test_callbacks_after_collection_wait_for_a_supplement(self):
        self.a['harness'] = 'claude'
        p = self.prepare()
        executor.launch(self.spool, self.op, one_turn=True)
        folder = capture.operation_dir(self.spool, self.op)
        first = executor.collect(self.spool, self.op, self.ending)
        sid = p['reserved_session_id']
        source = next((self.home / 'projects').glob('*/*.jsonl'))
        with source.open('a') as out:
            out.write(
                '{"sessionId": "%s", "type": "assistant", "message": {"role": "assistant", "content": "later"}}\n' % sid
            )
        capture.route(self.spool, self.home, dict(session_id=sid, hook_event_name='Stop'), self.op)
        self.assertEqual(3, len(list((folder / 'events').glob('*.json'))))
        self.assertEqual(1, len(list((folder / 'late' / 'events').glob('*.json'))))
        declaration = str(uuid4())
        with self.assertRaisesRegex(ValueError, 'earlier supplement'):
            executor.collect(self.spool, self.op, declaration, supplement=2)
        later = executor.collect(self.spool, self.op, declaration, supplement=1)
        self.assertEqual((1, declaration, self.ending), (later['sequence'], later['supplement_id'], later['ending_id']))
        self.assertEqual(['Stop'], later['events'])
        self.assertNotEqual(first['parent_sha256'], later['parent_sha256'])
        self.assertEqual(later, executor.collect(self.spool, self.op, declaration, supplement=1))
        self.assertEqual(first, executor.collect(self.spool, self.op, self.ending))
        self.assertFalse((folder / 'late').exists())
        path = executor.payload_path(self.spool, self.op, declaration, 'packet/notes/account.md', supplement=1)
        self.assertTrue(path.is_file())
        # Nothing staged since: the next supplement says so instead of looking like a session.
        again = executor.collect(self.spool, self.op, str(uuid4()), supplement=2)
        self.assertTrue(any('closeout observation only' in text for text in again['limitations']))

    def test_dead_launcher_is_settled_as_an_unobserved_exit(self):
        self.prepare()
        folder = capture.operation_dir(self.spool, self.op)
        gone = subprocess.Popen([sys.executable, '-c', 'pass'])
        gone.wait()
        paths = capture.sitting_files(folder, 1)
        files.write(paths['intent'], {'one_turn': False})
        files.write(paths['started'], {'pid': gone.pid})
        launch = executor.launch(self.spool, self.op)
        self.assertIsNone(launch['exit']['returncode'])
        self.assertIn('before it observed the native process exit', launch['exit']['unobserved'])
        self.assertEqual(launch, executor.launch(self.spool, self.op))
        frozen = executor.collect(self.spool, self.op, self.ending)
        self.assertTrue(any('without its launcher observing' in text for text in frozen['limitations']))

    def test_a_running_process_blocks_only_when_it_is_the_recorded_one(self):
        self.prepare()
        folder = capture.operation_dir(self.spool, self.op)
        paths = capture.sitting_files(folder, 1)
        files.write(paths['intent'], {'one_turn': False})
        other = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
        self.addCleanup(other.wait)
        self.addCleanup(other.kill)
        # The same pid with the recorded start time is the native process, still running.
        files.write(paths['started'], {'pid': other.pid, 'process_started': executor.process_identity(other.pid)})
        with self.assertRaisesRegex(ValueError, 'is still running without its launcher'):
            executor.launch(self.spool, self.op)
        with (
            patch.object(executor, 'process_identity', return_value=None),
            self.assertRaisesRegex(ValueError, 'may still be running'),
        ):
            executor.launch(self.spool, self.op)
        self.assertFalse(paths['exit'].exists())
        # The same pid with another start time is some other process that reused the number.
        files.write(paths['started'], {'pid': other.pid, 'process_started': 'Thu Jan  1 00:00:00 1970'})
        launch = executor.launch(self.spool, self.op)
        self.assertIsNone(launch['exit']['returncode'])

    def test_a_launcher_that_died_before_starting_a_process_says_so(self):
        self.prepare()
        folder = capture.operation_dir(self.spool, self.op)
        files.write(capture.sitting_files(folder, 1)['intent'], {'one_turn': False})
        launch = executor.launch(self.spool, self.op)
        self.assertIn('before a native process was observed to start', launch['exit']['unobserved'])
        self.assertIsNone(launch['started'])

    def test_damaged_staging_is_reported_per_member(self):
        self.prepare()
        executor.launch(self.spool, self.op, one_turn=True)
        frozen = executor.collect(self.spool, self.op, self.ending)
        self.assertEqual([], executor.damaged(self.spool, self.op, self.ending))
        collection = capture.operation_dir(self.spool, self.op) / 'collection' / 'payload'
        (collection / 'packet/notes/account.md').write_bytes(b'altered')
        (collection / 'hooks.json').unlink()
        self.assertEqual(
            ['hooks.json', 'packet/notes/account.md'],
            [row['path'] for row in executor.damaged(self.spool, self.op, self.ending)],
        )
        # A frozen manifest is still returned: the loss is per member, found at transfer.
        self.assertEqual(frozen, executor.collect(self.spool, self.op, self.ending))
