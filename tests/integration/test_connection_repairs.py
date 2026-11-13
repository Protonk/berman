"""Regression cases from the independent repository-connections completion review."""
from tests.support.paths import REPO
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import threading
from unittest import mock

from tests.integration.test_connections import Base
from berman.files import Decision, read, write
from berman.installation import PACKAGE as ROOT
from berman.harnesses.codex import Codex
from berman.capture.router import route
from berman.capture import adapters
from berman import cli


class ReviewRegressionTests(Base):
    def test_simultaneous_openings_in_one_session_are_serialized(self):
        """Two openings race in one session: both bind in turn, and the first is closed by the switch."""
        packets = [self.workflow.new(), self.workflow.new(connection='orchard')]
        source = self.home / 'sessions/racing.jsonl'
        source.parent.mkdir(exist_ok=True)
        source.write_text(json.dumps({'type': 'session_meta', 'payload': {'id': 'racing'}}) + '\n')
        barrier = threading.Barrier(2)
        outcomes = []
        def register(packet):
            barrier.wait(timeout=3)
            try:
                route({'session_id': 'racing', 'hook_event_name': 'UserPromptSubmit', 'transcript_path': str(source),
                       'prompt': self.berman.marker + packet['run'], 'turn_id': 'one'}, self.home, self.root)
                outcomes.append('registered')
            except ValueError as error:
                outcomes.append(str(error))
        threads = [threading.Thread(target=register, args=(packet,)) for packet in packets]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
        self.assertEqual(outcomes, ['registered', 'registered'])
        self.assertEqual(sum((Path(p['packet']) / 'session/binding.json').exists() for p in packets), 2)
        # Exactly one packet was closed by the other's registration; the registry names the one left open.
        switched = [p for p in packets if (Path(p['packet']) / 'session/ended.json').exists()]
        self.assertEqual(len(switched), 1)
        current = read(self.berman.registry('racing'))['run']
        self.assertEqual({current, switched[0]['run']}, {p['run'] for p in packets})

    def test_foreign_workspace_prompt_does_not_mutate_registration(self):
        for connection, collector in [('stewart', self.berman), ('orchard', self.berman)]:
            one = self.workflow.new(connection=connection)
            _, event = self.start(one['run'], collector)
            registry = collector.registry(event['session_id'])
            before = registry.read_bytes()
            self.assertEqual(route(dict(event, hook_event_name='UserPromptSubmit', prompt='Continue'),
                                   self.home, self.base / 'another-workspace'), {})
            self.assertEqual(registry.read_bytes(), before)

    def check_finalize_isolation(self, connection, collector):
        one = self.workflow.new(connection=connection)
        self.start(one['run'], collector)
        two = self.workflow.new(connection=connection, keep_open=True)
        _, event = self.start(two['run'], collector, stop=False)
        entered, release = threading.Event(), threading.Event()
        original = type(collector).capture
        outcomes = []
        def slow_capture(instance, root, incoming, *args, **kwargs):
            if root.name == one['run'] and incoming.get('hook_event_name') == 'manual-finalize':
                entered.set()
                if not release.wait(timeout=5):
                    raise AssertionError('Test did not release finalization')
            return original(instance, root, incoming, *args, **kwargs)
        def finish():
            try:
                outcomes.append(self.workflow.finish('First engagement ended', run=one['run']))
            except Exception as error:
                outcomes.append(error)
        with mock.patch.object(type(collector), 'capture', slow_capture):
            thread = threading.Thread(target=finish)
            thread.start()
            try:
                self.assertTrue(entered.wait(timeout=3))
                self.assertEqual(route(dict(event, hook_event_name='Stop'), self.home, self.root), {})
                self.assertFalse(read(collector.registry(event['session_id']))['active_turn'])
            finally:
                release.set()
                thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(outcomes), 1)
        self.assertIsInstance(outcomes[0], dict, outcomes)
        self.assertEqual(outcomes[0]['state'], 'archived')
        self.assertEqual(self.workflow.finish('Second engagement ended', run=two['run'])['state'], 'archived')

    def test_finalize_does_not_skip_another_last_stop(self):
        self.check_finalize_isolation('orchard', self.berman)

    def test_damaged_foreign_journal_is_scoped_and_status_still_works(self):
        one = self.workflow.new(connection='orchard')
        self.start(one['run'], self.berman)
        journal = self.root / 'migrations' / (one['run'] + '.json')
        journal.parent.mkdir(exist_ok=True)
        journal.write_text('{interrupted journal')
        two = self.workflow.new(connection='stewart')
        self.assertEqual(two['run'], 'run-002')
        status = self.workflow.status()
        self.assertTrue(status['connections']['orchard']['blocked_by'])
        self.assertFalse(status['connections']['stewart']['blocked_by'])
        with self.assertRaisesRegex(Decision, 'cannot inspect.*migrations'):
            self.workflow.new(connection='orchard')
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(cli.main(['--root', str(self.root), 'status']), 0)
        self.assertIn(str(journal), output.getvalue())
        self.assertEqual(journal.read_text(), '{interrupted journal')

    def test_changed_harness_settings_refuse_launch_retry_and_finish(self):
        one = self.workflow.new(connection='orchard')
        config = read(self.root / 'berman.json')
        original = dict(config)
        for key, replacement in [('codex', '/different/codex'), ('python', '/different/python'),
                                 ('codex_home', str(self.base / 'different-home'))]:
            with self.subTest(key=key):
                write(self.root / 'berman.json', dict(original, **{key: replacement}))
                self.reload()
                with self.assertRaisesRegex(Decision, 'harness installation differs'):
                    Codex(self.store).argv(one['run'])
                with self.assertRaisesRegex(Decision, 'harness installation differs'):
                    self.workflow.new(connection='orchard')
                with mock.patch('berman.workflow.os.execv') as execute:
                    with self.assertRaisesRegex(Decision, 'harness installation differs'):
                        self.workflow.launch(run=one['run'])
                    execute.assert_not_called()
        write(self.root / 'berman.json', original)
        self.reload()
        self.assertEqual(Codex(self.store).argv(one['run'])[0], str(Path(original['codex']).expanduser()))
        self.start(one['run'], self.berman)
        write(self.root / 'berman.json', dict(original, codex_home=str(self.base / 'different-home')))
        self.reload()
        with self.assertRaisesRegex(Decision, 'harness installation differs'):
            self.workflow.finish('Ended', run=one['run'])
        self.assertNotIn('ending', self.store.record(one['run']))

    def test_damaged_foreign_registry_is_attributed_from_its_binding(self):
        one = self.workflow.new(connection='orchard')
        _, event = self.start(one['run'], self.berman)
        registry = self.berman.registry(event['session_id'])
        registry.write_text('{broken registry')
        self.assertEqual(self.workflow.new(connection='stewart')['run'], 'run-002')
        status = self.workflow.status()
        self.assertTrue(status['connections']['orchard']['blocked_by'])
        self.assertEqual(status['workspace_problems'], [])

    def test_damaged_unattributed_registry_is_visible_as_shared_dependency(self):
        path = self.home / 'berman-capture/unknown-session.json'
        path.parent.mkdir(parents=True)
        path.write_text('{broken registry')
        status = self.workflow.status()
        self.assertIn(str(path), status['workspace_problems'][0])
        with self.assertRaisesRegex(Decision, 'Unattributed damaged session registry'):
            self.workflow.new()

    def test_router_entrypoint_captures_each_connection_without_model_calls(self):
        for connection, cls in [('stewart', self.berman), ('orchard', self.berman)]:
            one = self.workflow.new(connection=connection)
            source = self.home / 'sessions' / (connection + '.jsonl')
            source.parent.mkdir(exist_ok=True)
            source.write_text(json.dumps({'type': 'session_meta', 'payload': {'id': connection}}) + '\n' +
                              json.dumps({'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant',
                                         'content': [{'text': 'done'}]}}) + '\n')
            event = {'session_id': connection, 'transcript_path': str(source), 'turn_id': 'one'}
            for name in ('UserPromptSubmit', 'Stop', 'SessionEnd'):
                result = subprocess.run([sys.executable, '-I', '-B', '-m', 'berman.capture.router', 'hook',
                                         '--codex-home', str(self.home), '--analysis-root', str(self.root)],
                                        input=json.dumps(dict(event, hook_event_name=name, prompt=cls.marker + one['run'])),
                                        capture_output=True, text=True, check=True)
                self.assertEqual(json.loads(result.stdout), {}, result.stderr)
            self.assertEqual(read(Path(one['packet']) / 'session/capture.json')['event'], 'SessionEnd')

    def test_readiness_rejects_duplicate_direct_hooks(self):
        template = adapters.definition(self.store, 'berman-codex')
        (self.root / 'hooks').mkdir()
        write(self.root / 'hooks/berman-capture.hooks.json', template)
        installed = json.loads(json.dumps(template))
        old = __import__('tests.support.workspace', fromlist=['workspace']).direct_hooks()
        installed['hooks']['Stop'] += old['hooks']['Stop']
        write(self.home / 'hooks.json', installed)
        with self.assertRaisesRegex(Decision, 'not exclusive'):
            adapters.readiness(self.store, 'berman-codex')

    def test_installed_router_warns_on_near_markers_without_registration(self):
        # The removed adapter's opening line, exact or near, registers nothing and says what replaced it.
        cases = [('Stewart-Fieldwork-Run: run-001', 'belonged to a removed adapter'),
                 ('**Stewart-Fieldwork-Run: run-001**', 'belonged to a removed adapter'),
                 ('Stewart-Fieldwork-Run : run-001', 'Berman-Fieldwork-Run: run-NNN'),
                 ('`Berman-Fieldwork-Run: run-002`', 'Berman-Fieldwork-Run:'),
                 ('Do this first.\nBerman-Fieldwork-Run: run-002', 'Berman-Fieldwork-Run:')]
        for index, (opening, expected) in enumerate(cases):
            with self.subTest(opening=opening):
                event = {'session_id': f'near-{index}', 'hook_event_name': 'UserPromptSubmit', 'prompt': opening}
                result = subprocess.run([sys.executable, '-I', '-B', '-m', 'berman.capture.router', 'hook',
                                         '--codex-home', str(self.home), '--analysis-root', str(self.root)],
                                        input=json.dumps(event), capture_output=True, text=True, check=True)
                self.assertIn(expected, json.loads(result.stdout)['systemMessage'])
                self.assertFalse(self.berman.registry(event['session_id']).exists())
                self.assertFalse((self.home / 'stewart-fieldwork').exists())
        self.assertEqual(route({'session_id': 'ordinary', 'hook_event_name': 'UserPromptSubmit', 'prompt': 'Hello'},
                               self.home, self.root), {})
        self.assertIn('systemMessage', self.berman.hook({'session_id': 'direct-near', 'hook_event_name': 'UserPromptSubmit',
                                                         'prompt': '**' + self.berman.marker + 'run-001**'}))

    def test_moved_workspace_and_changed_profile_are_refused(self):
        import shutil
        from berman.records import Store
        from berman.workflow import Workflow
        from tests.support.workspace import FixtureHarness
        one = self.workflow.new(connection='orchard')
        moved = self.base / 'moved-berman'
        shutil.copytree(self.root, moved)
        store = Store(moved)
        with self.assertRaisesRegex(Decision, 'workspace: recorded'):
            Codex(store).argv(one['run'])
        with mock.patch('berman.workflow.os.execv') as execute:
            with self.assertRaisesRegex(Decision, 'workspace: recorded'):
                Workflow(store, FixtureHarness(store)).launch(run=one['run'])
            execute.assert_not_called()
        with self.assertRaisesRegex(ValueError, 'workspace differs'):
            route({'session_id': 'moved', 'hook_event_name': 'UserPromptSubmit',
                   'prompt': self.berman.marker + one['run']}, self.home, moved)
        record = self.store.record(one['run'])
        record['harness']['profile'] = 'a-different-profile'
        self.store.save(record)
        with self.assertRaisesRegex(Decision, 'profile: recorded'):
            Codex(self.store).argv(one['run'])

    def test_damaged_started_run_keeps_next_role_and_started_state(self):
        one = self.workflow.new()
        _, event = self.start(one['run'], self.berman)
        self.assertNotIn('started_session', self.store.record(one['run']))
        journal = self.root / 'migrations' / (one['run'] + '.json')
        journal.parent.mkdir(exist_ok=True)
        journal.write_text('{broken journal')
        status = self.workflow.status()
        self.assertEqual(status['next_recipe'], 'participant-inquiry')
        row = next(r for r in status['runs'] if r['run'] == one['run'])
        self.assertTrue(row['started'])
        self.assertFalse(row['prepared_unstarted'])
        self.assertEqual(row['session'], event['session_id'])
        self.assertIn('session/binding.json names ' + event['session_id'], row['lifecycle']['registered']['evidence'])
        self.assertIsNone(row['lifecycle']['migration']['value'])
        # A damaged binding still has independent registration evidence of a start.
        (Path(one['packet']) / 'session/binding.json').write_text('{broken binding')
        status = self.workflow.status()
        self.assertEqual(status['next_recipe'], 'participant-inquiry')
        row = next(r for r in status['runs'] if r['run'] == one['run'])
        self.assertEqual(row['lifecycle']['registered']['evidence'],
                         [f'registration berman-capture/{event["session_id"]}.json'])
        # With every start indicator unreadable, status reports uncertainty, not the repeat.
        self.berman.registry(event['session_id']).write_text('{broken registry')
        status = self.workflow.status()
        self.assertIn('starting state is unknown', status['next_recipe'])
        row = next(r for r in status['runs'] if r['run'] == one['run'])
        self.assertIn('registered', row['unknown'])
        self.assertEqual(row['lifecycle']['registered']['unknown'], row['problems'])

    def test_trust_renewal_does_not_carry_capture_evidence_across_code_changes(self):
        template = adapters.definition(self.store, 'berman-codex')
        (self.root / 'hooks').mkdir()
        write(self.root / 'hooks/berman-capture.hooks.json', template)
        write(self.home / 'hooks.json', template)
        evidence = self.base / 'review.terminal'
        evidence.write_text('Fixture review, not a real trust observation.\n')
        adapters.record_trust(self.store, evidence, 'fixture')
        basis_path = self.root / adapters.ADAPTERS['berman-codex']['basis']
        basis = read(basis_path)
        successful = {'path': 'fixture-captures.jsonl', 'required_events': ['Stop', 'SessionEnd']}
        basis.update(successful_capture=successful, meaning='Fixture successful capture')
        write(basis_path, basis)
        adapters.record_trust(self.store, evidence, 'fixture same implementation')
        self.assertEqual(read(basis_path)['successful_capture'], successful)
        with mock.patch.object(adapters, 'implementation', return_value={'fixture.py': 'different-code'}):
            adapters.record_trust(self.store, evidence, 'fixture changed implementation')
        self.assertIsNone(read(basis_path)['successful_capture'])
        self.assertIn('No successful capture', read(basis_path)['meaning'])
