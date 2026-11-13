"""Native support trials: reserved before anything exists, never research, collected, cleaned up and linked.

Sessions arrive through the installed router's own entry (route()) for both adapters. Codex is a
fake harness that answers --version and never runs; os.execv is mocked; every root is disposable.
No test reads or writes the real Codex home, the real packet root or runs/.
"""
from tests.support.paths import REPO
from contextlib import contextmanager, redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from berman.capture.router import route
from berman.files import Decision, json_bytes, read, write
from berman.harnesses.codex import Codex
from berman.records import Store
from berman.installation import PACKAGE as ROOT
from berman.capture import adapters
from berman.capture import runtime
from berman import cli
from berman.capture import collector
from berman import files
from berman import lifecycle
from berman import recipes
from berman import support
from berman import verify
from berman import recovery as fieldwork
from tests.integration.test_connections import Base
from tests.integration.test_verify import Fixture

MARKER_KEYS = {'schema', 'run', 'phase', 'purpose', 'support_trial', 'reserved_at_utc', 'workspace', 'packet_root',
               'packet', 'project', 'capture', 'harness'}


class FakeCodex(Codex):
    """The real profile installation; a Codex invocation is answered without running Codex."""

    def invoke(self, argv, cwd, **kwargs):
        if argv[1:] != ['--version']:
            raise AssertionError(f'unexpected Codex invocation: {argv}')
        return subprocess.CompletedProcess(argv, 0, 'codex-cli 0.0.0-fixture\n', '')


class Terminal(io.StringIO):
    def isatty(self):
        return True


class SupportBase(Fixture, Base):
    """Stewart and orchard on berman-codex, with the router installed and reviewed."""

    def setUp(self):
        super().setUp()
        self.trust(captures=())
        (self.base / 'tmp').mkdir()
        patcher = mock.patch.object(tempfile, 'tempdir', str(self.base / 'tmp'))  # Disposable projects stay in the fixture.
        patcher.start()
        self.addCleanup(patcher.stop)

    def folder(self, name='trial'):
        return self.root / 'experiments' / name

    def prepare(self, name='trial', adapter='berman-codex'):
        return support.prepare(self.store, f'experiments/{name}', adapter, FakeCodex(self.store))

    def launch(self, name='trial'):
        with mock.patch.dict(os.environ), mock.patch.object(sys, 'stdin', Terminal()), \
             mock.patch.object(sys, 'stdout', Terminal()), mock.patch.object(support.os, 'execv') as execv:
            support.launch(self.store, f'experiments/{name}')
        return execv

    def session(self, trial, reply=None, events=('Stop', 'SessionEnd'), tool_output=None):
        """One native session through the router: the pasted assignment, a reply, then the given callbacks."""
        sid = 'support-' + trial['run']
        reply = trial['expected_reply'] if reply is None else reply
        source = self.home / 'sessions' / (sid + '.jsonl')
        source.parent.mkdir(exist_ok=True)
        rows = [{'type': 'session_meta', 'payload': {'id': sid}}]
        if tool_output:
            rows.append({'type': 'response_item', 'payload': {'type': 'function_call_output', 'call_id': 'read',
                                                              'output': tool_output}})
        rows.append({'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant', 'content': [{'text': reply}]}})
        source.write_text(''.join(json.dumps(r) + '\n' for r in rows))
        event = {'session_id': sid, 'transcript_path': str(source), 'turn_id': 'one'}
        prompt = (Path(trial['packet']) / 'ASSIGNMENT.md').read_text()
        results = [route(dict(event, hook_event_name='UserPromptSubmit', prompt=prompt), self.home, self.root)]
        for name in events:
            extra = {'last_assistant_message': reply} if name == 'Stop' else {}
            results.append(route(dict(event, hook_event_name=name, **extra), self.home, self.root))
        return event, results

    def collected(self, name='trial', adapter='berman-codex'):
        trial = self.prepare(name, adapter)
        self.launch(name)
        self.session(trial)
        return support.collect(self.store, f'experiments/{name}')


class ReservationTests(SupportBase):
    def test_open_trial_is_not_research_and_leaves_numbering_and_plans_alone(self):
        before = self.workflow.status()
        trial = self.prepare()
        self.assertEqual((trial['run'], trial['phase'], trial['expected_reply']),
                         ('run-900101', 'prepared', 'BERMAN_SUPPORT_TRIAL_OK_900101'))
        marker = read(self.store.folder('run-900101') / 'operation.json')
        self.assertEqual(set(marker), MARKER_KEYS)
        self.assertEqual((marker['purpose'], marker['phase'], marker['support_trial'], marker['capture']['adapter']),
                         ('support-trial', 'support-trial', 'experiments/trial/trial.json', 'berman-codex'))
        self.assertEqual(marker['harness']['profile'], trial['profile'])
        self.assertEqual(trial['codex_version'], 'codex-cli 0.0.0-fixture')
        self.assertTrue(trial['readiness_at_preparation']['ready'])
        packet = Path(trial['packet'])
        self.assertEqual(packet, self.store.field / 'run-900101')
        self.assertEqual(read(packet / 'run.json')['schema'], 'berman.run-preparation/1')
        self.assertTrue((packet / 'ASSIGNMENT.md').read_text().startswith('Berman-Fieldwork-Run: run-900101\n'))
        self.assertIn('reply exactly BERMAN_SUPPORT_TRIAL_OK_900101', (packet / 'INSTRUCTIONS.md').read_text())
        self.assertEqual((self.home / (trial['profile'] + '.config.toml')).read_bytes(),
                         (self.folder() / 'profile.config.toml').read_bytes())
        self.assertTrue((Path(trial['project']) / '.git').is_dir())
        self.assertEqual(Path(trial['project']).parent, self.base / 'tmp')
        rows = self.store.discover()
        self.assertEqual(rows, [])
        self.assertEqual(list(self.store.support_trials), ['run-900101'])
        self.assertEqual(self.store.allocate(rows), 'run-001')
        status = self.workflow.status()
        self.assertEqual((status['runs'], status['next_recipe']), ([], before['next_recipe']))
        self.assertEqual([(t['run'], t['phase'], t['capture_state']) for t in status['support_trials']],
                         [('run-900101', 'prepared', 'prepared')])
        self.assertIn('berman support-trial launch --evidence experiments/trial', status['support_trials'][0]['next'])
        # A registered native session neither starts research nor blocks it.
        self.session(trial)
        status = self.workflow.status()
        self.assertEqual((status['runs'], status['next_recipe']), ([], before['next_recipe']))
        self.assertEqual(status['connections']['stewart']['blocked_by'], [])
        self.assertEqual(self.workflow.new()['run'], 'run-001')
        self.assertEqual(self.prepare('second')['run'], 'run-900102')
        self.assertEqual(self.workflow.status()['next_recipe'], before['next_recipe'])

    def test_marker_is_published_under_the_global_lock_before_packet_profile_or_project(self):
        seen, prepared = {}, {}
        keep, prepare = support.keep, fieldwork.prepare

        def observing(path, data):
            if path.name == 'operation.json':
                record = json.loads(data)
                with self.assertRaisesRegex(Decision, 'allocation and index'):
                    with Store(self.root).lock('global'):
                        pass
                seen.update(packet=Path(record['packet']).exists(), project=Path(record['project']).exists(),
                            profile=(self.home / (record['harness']['profile'] + '.config.toml')).exists())
            return keep(path, data)

        def preparing(collector, run, *args, **kwargs):
            store = Store(self.root)  # By the time a packet can appear, discovery already diverts the identity.
            prepared.update(rows=store.discover(), diverted=run in store.support_trials)
            return prepare(collector, run, *args, **kwargs)
        with mock.patch.object(support, 'keep', observing), mock.patch.object(fieldwork, 'prepare', preparing):
            self.prepare()
        self.assertEqual(seen, {'packet': False, 'project': False, 'profile': False})
        self.assertEqual(prepared, {'rows': [], 'diverted': True})

    def test_interrupted_preparation_resumes_its_own_reservation_and_is_never_repeated(self):
        with mock.patch.object(fieldwork, 'prepare', side_effect=OSError('interrupted before the packet')):
            with self.assertRaisesRegex(OSError, 'interrupted'):
                self.prepare()
        self.assertEqual(read(self.folder() / 'trial.json')['phase'], 'reserved')
        self.assertEqual(self.workflow.status()['support_trials'][0]['next'],
                         'berman support-trial prepare --evidence experiments/trial --adapter berman-codex')
        self.assertEqual(self.prepare()['run'], 'run-900101')
        with self.assertRaisesRegex(Decision, 'prepared once'):
            self.prepare()
        # A marker published without its trial.json leaves an empty folder; the same command resumes that marker.
        atomic = files.atomic

        def failing(path, data):
            if path.name == 'trial.json':
                raise OSError('interrupted after the marker')
            return atomic(path, data)
        with mock.patch.object(files, 'atomic', failing), self.assertRaisesRegex(OSError, 'after the marker'):
            self.prepare('orphan')
        self.assertTrue((self.store.folder('run-900102') / 'operation.json').exists())
        self.assertEqual(list(self.folder('orphan').iterdir()), [])
        orphan = next(t for t in self.workflow.status()['support_trials'] if t['run'] == 'run-900102')
        self.assertIn('names experiments/orphan/trial.json, which was never written', orphan['problems'][0])
        self.assertEqual(orphan['next'], 'berman support-trial prepare --evidence experiments/orphan --adapter berman-codex, '
                                         'which resumes run-900102')
        with self.assertRaisesRegex(Decision, 'reserved for berman-codex in this folder, not berman-claude'):
            support.prepare(self.store, 'experiments/orphan', 'berman-claude', FakeCodex(self.store))
        self.assertEqual(self.prepare('orphan')['run'], 'run-900102')
        self.assertEqual(self.prepare('another')['run'], 'run-900103')
        # A folder holding anything is never removed to resume a marker.
        with mock.patch.object(files, 'atomic', failing), self.assertRaises(OSError):
            self.prepare('kept')
        (self.folder('kept') / 'note.txt').write_text('operator note\n')
        with self.assertRaisesRegex(Decision, 'already exists'):
            self.prepare('kept')
        self.assertTrue((self.folder('kept') / 'note.txt').is_file())
        kept = next(t for t in self.workflow.status()['support_trials'] if t['run'] == 'run-900104')
        self.assertIsNone(kept['next'])

    def test_a_concurrent_cleanup_stops_a_resumed_preparation(self):
        with mock.patch.object(fieldwork, 'prepare', side_effect=OSError('interrupted before the packet')):
            with self.assertRaises(OSError):
                self.prepare()
        lock, done = self.store.lock, []

        def cleaning_first(kind, name=None, wait=0):
            if kind == 'run' and not done:  # Between prepare's first read and its run lock.
                done.append(support.cleanup(Store(self.root), 'experiments/trial', 'Withdrawn')['phase'])
            return lock(kind, name, wait)
        with mock.patch.object(self.store, 'lock', cleaning_first), self.assertRaisesRegex(Decision, 'phase abandoned'):
            self.prepare()
        trial = read(self.folder() / 'trial.json')
        self.assertEqual((done, trial['phase']), (['abandoned'], 'abandoned'))
        self.assertFalse(Path(trial['packet']).exists() or Path(trial['project']).exists())
        self.assertFalse((self.home / (trial['profile'] + '.config.toml')).exists())
        self.assertIsNone(read(self.folder() / 'cleanup.json')['unused_packet'])

    def test_a_folder_holding_another_trial_json_is_never_treated_as_a_trial(self):
        # Unrelated historical receipt schemas must never be treated as managed trials.
        folder = self.folder('unrelated-receipt')
        folder.mkdir(parents=True)
        write(folder / 'trial.json', {'schema': 'unrelated.trial/1', 'result': 'synthetic'})
        write(folder / 'cleanup.json', {'schema': 'unrelated.cleanup/1'})
        self.prepare()
        copied = self.folder('copied')
        copied.mkdir()
        trial = read(self.folder() / 'trial.json')
        cases = {'not a berman.support-trial/1 receipt': folder,
                 'not a berman.support-trial/1 receipt for experiments/copied': copied}
        for message, place in cases.items():
            if place == copied:
                write(copied / 'trial.json', trial)
            before = {p: p.read_bytes() for p in place.iterdir()}
            rel = place.relative_to(self.root).as_posix()
            with mock.patch.object(sys, 'stdin', Terminal()), mock.patch.object(sys, 'stdout', Terminal()), \
                 mock.patch.object(support.os, 'execv') as execv:
                for command in (lambda: support.cleanup(self.store, rel, 'oops'),
                                lambda: support.collect(self.store, rel, 'reason'),
                                lambda: support.launch(self.store, rel),
                                lambda: support.record_capture(self.store, rel),
                                lambda: support.prepare(self.store, rel, 'berman-codex', FakeCodex(self.store))):
                    with self.subTest(message=message), self.assertRaisesRegex(Decision, re.escape(message)):
                        command()
            execv.assert_not_called()
            self.assertEqual({p: p.read_bytes() for p in place.iterdir()}, before)
        # A copy that claims the copied folder still needs the immutable marker to name it.
        write(copied / 'trial.json', dict(trial, evidence='experiments/copied'))
        with self.assertRaisesRegex(Decision, 'no support reservation marker agrees with experiments/copied/trial.json'):
            support.cleanup(self.store, 'experiments/copied', 'oops')
        self.assertEqual(read(self.folder() / 'trial.json')['phase'], 'prepared')
        self.assertTrue(Path(trial['packet']).exists() and Path(trial['project']).exists())

    def test_evidence_folders_follow_the_shared_path_policy(self):
        self.folder('older/control-scripts').mkdir(parents=True)
        for path in ('runs/x', 'experiments', 'experiments/x/collection/y', '../outside', 'experiments/collection',
                     'experiments/older/again', 'experiments/older/control-scripts/x', 'experiments/T/unused-packet/x'):
            with self.subTest(path=path), self.assertRaises(Decision):
                support.prepare(self.store, path, 'berman-codex', FakeCodex(self.store))
        self.assertEqual([p.name for p in self.folder('older').iterdir()], ['control-scripts'])
        self.folder('taken').mkdir(parents=True)
        with self.assertRaisesRegex(Decision, 'already exists'):
            self.prepare('taken')
        with self.assertRaisesRegex(Decision, 'No support trial is recorded'):
            support.collect(self.store, 'experiments/nothing')
        self.assertFalse(self.store.operations.exists())

    def test_support_allocation_skips_research_and_support_identities(self):
        self.store.discover()
        self.store.support_identities = {'run-900102'}
        self.assertEqual(self.store.allocate_support([{'run': 'run-900101'}]), 'run-900103')
        self.store.support_identities = {f'run-{n}' for n in range(900101, 900200)}
        with self.assertRaisesRegex(Decision, 'run-900101 to run-900199 is exhausted'):
            self.store.allocate_support([])

    def test_marker_validation_and_damaged_markers(self):
        trial = self.prepare('notes', 'berman-codex')
        path = self.store.folder(trial['run']) / 'operation.json'
        marker = read(path)
        self.assertIs(lifecycle.validate_record(marker, trial['run']), marker)
        without = lambda key: {k: v for k, v in marker.items() if k != key}
        refused = {'unknown support phase': dict(marker, phase='reserved'),
                   'packet is missing': without('packet'),
                   'workspace.root is missing': dict(marker, workspace=['not', 'a', 'mapping']),
                   'capture.adapter is missing': dict(marker, capture={}),
                   'harness.home is missing': dict(marker, harness={'home': ''}),
                   'support_trial is missing': without('support_trial'),
                   'packet must be the run directly within packet_root': dict(marker, packet='/elsewhere/run-900101')}
        for bad in ('runs/run-900101/trial.json', '/abs/experiments/x/trial.json', 'experiments/../x/trial.json',
                    'experiments/trial.json', 'experiments/x/receipt.json', 'experiments/x/y/trial.json',
                    'experiments/x/unused-packet/trial.json', '.', './', 'trial.json'):
            refused[f'support_trial must name experiments/NAME/trial.json ({bad})'] = dict(marker, support_trial=bad)
        for message, value in refused.items():
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, re.escape(message.split(' (')[0])):
                lifecycle.validate_record(value, trial['run'])
        # A damaged marker stays a conservative problem row: visible and blocking, never dropped. No connection is
        # known for it, so it blocks every connection, and it never sets research numbering.
        for damaged in (without('packet'), dict(marker, support_trial='.'), dict(marker, purpose=None), [marker]):
            write(path, damaged)
            rows = self.store.discover()
            with self.subTest(damaged=str(damaged)[:60]):
                self.assertEqual([(r['run'], r['connection']) for r in rows], [(trial['run'], None)])
                self.assertTrue(rows[0]['problems'])
                self.assertNotIn(trial['run'], self.store.support_trials)
                self.assertEqual(self.store.allocate(rows), 'run-001')
                status = self.workflow.status()
                self.assertEqual(status['support_trials'], [])
                self.assertEqual([r['run'] for r in status['runs']], [trial['run']])
                for connection in ('stewart', 'orchard'):
                    with self.assertRaisesRegex(Decision, re.escape(rows[0]['problems'][0])):
                        self.workflow.new(connection=connection)
                check = verify.workspace(self.store, None)
                self.assertEqual((check['status'], check['details']['problems'][0]),
                                 ('failed', 'every connection: ' + rows[0]['problems'][0]))
                self.assertFalse((self.store.field / 'run-001').exists() or (self.store.field / 'run-900102').exists())
        # Outside the support range, a damaged record that states the support purpose is treated the same way.
        write(self.store.folder('run-000040') / 'operation.json', without('packet') | {'run': 'run-000040'})
        write(path, marker)
        rows = self.store.discover()
        self.assertEqual([(r['run'], r['connection']) for r in rows], [('run-000040', None)])
        with self.assertRaisesRegex(Decision, 'packet is missing'):
            self.workflow.new(connection='orchard')

    def test_research_numbering_never_enters_or_follows_the_support_range(self):
        self.store.discover()
        self.store.support_identities = set()
        self.assertEqual(self.store.allocate([{'run': 'run-900150'}, {'run': 'run-007'}]), 'run-008')
        self.assertEqual(self.store.allocate([{'run': 'run-900100'}]), 'run-900200')
        self.assertEqual(self.store.allocate([{'run': 'run-900100'}, {'run': 'run-900250'}]), 'run-900251')

    def test_support_runs_are_refused_by_research_commands_and_resolved_by_fieldwork(self):
        trial = self.prepare('notes', 'berman-codex')
        for command in (lambda: self.workflow.launch(trial['run']), lambda: self.workflow.finish('ended', trial['run']),
                        lambda: self.workflow.cancel('unwanted', trial['run'])):
            with self.assertRaisesRegex(Decision, 'native support trial.*--evidence experiments/notes'):
                command()
        # The low-level command reaches the berman-codex collector through the marker.
        out = io.StringIO()
        argv = ['fieldwork.py', 'status', trial['run'], '--analysis-root', str(self.root), '--fieldwork-root',
                str(self.store.field), '--codex-home', str(self.home)]
        with mock.patch.object(sys, 'argv', argv), redirect_stdout(out):
            fieldwork.main()
        self.assertEqual(json.loads(out.getvalue())['state'], 'prepared')

    def test_support_protocol_is_only_for_support_packets(self):
        self.assertNotIn('support-trial/1', recipes.PROTOCOLS)
        trial = self.prepare('notes', 'berman-codex')
        packet = Path(trial['packet'])
        meta = read(packet / 'run.json')
        self.assertEqual((meta['schema'], meta['recording_protocol'], meta['connection'], meta['capture_marker']),
                         ('berman.run-preparation/1', 'support-trial/1', 'support-trial', 'Berman-Fieldwork-Run:'))
        self.assertTrue((packet / 'notes').is_dir())
        self.assertFalse((packet / 'narrative.md').exists() or (packet / 'scratch').exists())
        with self.assertRaisesRegex(ValueError, 'reserved for native support-trial packets'):
            fieldwork.prepare(self.berman, 'run-000050', 'x', self.base / 'stewart', 'rev', ['notes'],
                              self.folder('notes') / 'SUPPORT-INSTRUCTIONS.txt', recording_protocol='support-trial/1',
                              marker=collector.MARKER, layout='notes', connection='orchard')
        self.assertFalse((self.store.field / 'run-000050').exists())


class LaunchTests(SupportBase):
    def test_launch_records_the_attempt_and_refuses_repeats_and_non_terminals(self):
        trial = self.prepare()
        folder = self.folder()
        with mock.patch.object(sys, 'stdin', io.StringIO()), mock.patch.object(sys, 'stdout', Terminal()), \
             mock.patch.object(support.os, 'execv') as execv:
            with self.assertRaisesRegex(Decision, 'interactive'):
                support.launch(self.store, 'experiments/trial')
        execv.assert_not_called()
        self.assertEqual(read(folder / 'trial.json')['phase'], 'prepared')
        with mock.patch.dict(os.environ), mock.patch.object(sys, 'stdin', Terminal()), \
             mock.patch.object(sys, 'stdout', Terminal()) as out, mock.patch.object(support.os, 'execv') as execv:
            support.launch(self.store, 'experiments/trial')
            self.assertEqual(os.environ['CODEX_HOME'], str(self.home))
        expected = ['/usr/bin/script', '-q', str(folder / 'native.terminal'), str(self.store.codex), '--no-daemon',
                    '--no-alt-screen', '-p', trial['profile'], '-C', trial['project']]
        execv.assert_called_once_with('/usr/bin/script', expected)
        recorded = read(folder / 'trial.json')
        self.assertEqual((recorded['phase'], recorded['launch']['argv'], recorded['launch']['terminal']),
                         ('launching', expected, 'native.terminal'))
        guide = (folder / 'LAUNCH.txt').read_text()
        self.assertIn('/hooks', guide)
        self.assertIn(trial['packet'] + '/ASSIGNMENT.md', guide)
        self.assertEqual(out.getvalue(), guide + '\n')
        with mock.patch.object(sys, 'stdin', Terminal()), mock.patch.object(sys, 'stdout', Terminal()), \
             mock.patch.object(support.os, 'execv') as execv:
            with self.assertRaisesRegex(Decision, 'never repeated'):
                support.launch(self.store, 'experiments/trial')
        execv.assert_not_called()

    def test_failed_exec_returns_to_prepared_and_an_existing_terminal_record_refuses(self):
        self.prepare()
        with mock.patch.dict(os.environ), mock.patch.object(sys, 'stdin', Terminal()), \
             mock.patch.object(sys, 'stdout', Terminal()), \
             mock.patch.object(support.os, 'execv', side_effect=OSError('script is missing')):
            with self.assertRaisesRegex(OSError, 'script is missing'):
                support.launch(self.store, 'experiments/trial')
        recorded = read(self.folder() / 'trial.json')
        self.assertEqual((recorded['phase'], recorded['failed_launches'][0]['exec_error']), ('prepared', 'script is missing'))
        self.assertNotIn('launch', recorded)
        (self.folder() / 'native.terminal').write_text('an earlier record\n')
        with self.assertRaisesRegex(Decision, 'never repeated'):
            self.launch()


class CollectionTests(SupportBase):
    def test_the_codex_adapter_registers_through_the_router_and_collects_into_the_trial_folder(self):
        catalogue = self.root / 'connections/catalogue.json'
        for number, adapter in enumerate((n for n, a in adapters.ADAPTERS.items() if a['harness'] == 'codex'), 1):
            with self.subTest(adapter=adapter):
                name, run = f'trial-{adapter}', f'run-90010{number}'
                trial = self.prepare(name, adapter)
                self.assertEqual(trial['run'], run)
                self.launch(name)
                event, results = self.session(trial)
                self.assertEqual(results, [{}, {}, {}])
                state = read(self.home / 'berman-capture' / (event['session_id'] + '.json'))
                self.assertEqual((state['run'], state['workspace']), (run, str(self.root)))
                collected = support.collect(self.store, f'experiments/{name}')
                folder = self.folder(name)
                self.assertEqual((collected['phase'], collected['demonstrates_capture'], collected['override_reason']),
                                 ('collected', True, None))
                self.assertEqual((collected['events'], collected['session_id']), (['Stop', 'SessionEnd'], event['session_id']))
                self.assertEqual(collected['preserved_packet'], f'experiments/{name}/collection/runs/{run}')
                self.assertFalse((self.store.field / run).exists())
                moved = folder / 'collection/runs' / run
                self.assertEqual(read(moved / 'session/finalized.json')['capture_sha256'], collected['capture_sha256'])
                self.assertEqual(read(folder / 'collection/migrations' / f'{run}.json')['phase'], 'indexed')
                for receipt in ('native-status.json', 'finalization.json', 'migration.json', 'LAUNCH.txt'):
                    self.assertTrue((folder / receipt).is_file(), receipt)
                self.assertIn(f'experiments/{name}/collection', read(catalogue)['support_collections'])
                self.assertFalse((self.root / 'migrations').exists())
                self.assertEqual(read(self.root / 'source-layout.json'), {'runs': []})
                self.assertEqual(self.store.discover(), [])
                self.assertIn(run, self.store.support_trials)
                self.assertIn(run, self.store.support_identities)
                listed = next(t for t in self.workflow.status()['support_trials'] if t['run'] == run)
                self.assertEqual(listed['capture_state'], f'preserved in experiments/{name}/collection/runs/{run}')
                self.assertEqual(listed['next'], f'berman capture record-capture --trial experiments/{name}, then '
                                                 f'berman support-trial cleanup --evidence experiments/{name} --reason TEXT')
                self.assertEqual(support.collect(self.store, f'experiments/{name}')['state'], 'already-collected')

    def test_collect_requires_both_callbacks_the_exact_reply_and_unchanged_code(self):
        cases = {'no SessionEnd capture': {'events': ('Stop',)},
                 'final assistant message is exactly': {'reply': 'Something else',
                                                        'tool_output': 'Instructions say: reply exactly BERMAN_SUPPORT_TRIAL_OK_900102'}}
        for number, (message, session) in enumerate(cases.items()):
            name = f'trial-{number}'
            trial = self.prepare(name)
            self.launch(name)
            self.session(trial, **session)
            conversation = (Path(trial['packet']) / 'session/conversation.txt').read_text()
            if 'tool_output' in session:
                self.assertIn(trial['expected_reply'], conversation)  # A substring search would wrongly pass.
            with self.subTest(message=message), self.assertRaisesRegex(Decision, 'Not collected: .*' + message):
                support.collect(self.store, f'experiments/{name}')
            self.assertFalse((Path(trial['packet']) / 'session/finalized.json').exists())
            self.assertEqual(read(self.folder(name) / 'trial.json')['phase'], 'launching')
        trial = self.prepare('changed')
        self.launch('changed')
        self.session(trial)
        with mock.patch.object(adapters, 'implementation', return_value={'_legacy/capture_session.py': '0' * 64}):
            with self.assertRaisesRegex(Decision, 'implementation changed after preparation'):
                support.collect(self.store, 'experiments/changed')
        # An explicit incomplete boundary is preserved, demonstrates nothing, and cannot be linked.
        collected = support.collect(self.store, 'experiments/trial-0', 'Fixture boundary: SessionEnd withheld')
        self.assertEqual((collected['phase'], collected['demonstrates_capture'], collected['override_reason']),
                         ('collected-incomplete', False, 'Fixture boundary: SessionEnd withheld'))
        self.assertEqual(collected['observed']['missing'], ['no SessionEnd capture'])
        self.assertEqual(read(self.folder('trial-0') / 'finalization.json')['boundary']['override_reason'],
                         'Fixture boundary: SessionEnd withheld')
        with self.assertRaisesRegex(Decision, 'demonstrates no native capture'):
            support.record_capture(self.store, 'experiments/trial-0')
        self.assertIn('demonstrates no capture', next(t for t in self.workflow.status()['support_trials']
                                                      if t['run'] == collected['run'])['next'])

    def test_collect_refuses_a_trial_that_was_not_launched_or_never_registered(self):
        self.prepare()
        with self.assertRaisesRegex(Decision, 'collect follows berman support-trial launch'):
            support.collect(self.store, 'experiments/trial')
        self.launch()
        with self.assertRaisesRegex(Decision, 'No session has registered run-900101'):
            support.collect(self.store, 'experiments/trial', 'reason')

    def test_a_session_started_without_launch_is_collected_as_incomplete_and_cleaned_up(self):
        trial = self.prepare()
        event, _ = self.session(trial)  # Codex started outside launch with the installed profile.
        listed = self.workflow.status()['support_trials'][0]
        self.assertEqual(listed['next'], 'berman support-trial collect --evidence experiments/trial --allow-incomplete '
                                         'REASON; a session registered without launch, so this trial demonstrates no capture')
        with self.assertRaisesRegex(Decision, 'session already registered run-900101 outside launch'):
            self.launch()
        self.assertFalse((self.folder() / 'native.terminal').exists() or (self.folder() / 'LAUNCH.txt').exists())
        with self.assertRaisesRegex(Decision, 'Not collected: no launch was recorded'):
            support.collect(self.store, 'experiments/trial')
        collected = support.collect(self.store, 'experiments/trial', 'Started outside launch by mistake')
        self.assertEqual((collected['phase'], collected['demonstrates_capture'], collected['override_reason']),
                         ('collected-incomplete', False, 'Started outside launch by mistake'))
        self.assertEqual(collected['observed']['missing'],
                         ['no launch was recorded: the session started outside berman support-trial launch'])
        with self.assertRaisesRegex(Decision, 'demonstrates no native capture'):
            support.record_capture(self.store, 'experiments/trial')
        self.assertEqual(support.cleanup(self.store, 'experiments/trial', 'Closed')['phase'], 'cleaned')
        self.assertEqual(route(dict(event, hook_event_name='Stop'), self.home, self.root), {})

    def test_a_packet_finalized_outside_collect_still_needs_allow_incomplete_for_missing_evidence(self):
        trial = self.prepare()
        self.launch()
        self.session(trial, events=('Stop',))
        live = self.store.collector_for(self.store.record(trial['run']))
        live.command('finalize', trial['run'], reason='operator finalized by hand')
        with self.assertRaisesRegex(Decision, 'Not collected: no SessionEnd capture. run-900101 was finalized outside '
                                              'collect with this evidence missing, or keep an intentional incomplete'):
            support.collect(self.store, 'experiments/trial')
        self.assertTrue(Path(trial['packet']).exists())
        self.assertEqual(read(self.folder() / 'trial.json')['phase'], 'launching')
        self.assertFalse((self.folder() / 'finalization.json').exists())
        collected = support.collect(self.store, 'experiments/trial', 'Kept as finalized by hand')
        self.assertEqual((collected['phase'], collected['demonstrates_capture'], collected['override_reason'],
                          collected['allow_incomplete']),
                         ('collected-incomplete', False, 'operator finalized by hand', 'Kept as finalized by hand'))
        self.assertEqual(collected['observed']['missing'], ['no SessionEnd capture'])

    def test_interrupted_collection_resumes_and_catalogues_once(self):
        catalogue = self.root / 'connections/catalogue.json'
        original = read(catalogue)
        trial = self.prepare()
        self.launch()
        self.session(trial)
        with mock.patch.object(fieldwork, 'migrate', side_effect=OSError('interrupted before migration')):
            with self.assertRaisesRegex(OSError, 'before migration'):
                support.collect(self.store, 'experiments/trial')
        self.assertTrue((Path(trial['packet']) / 'session/finalized.json').exists())
        with self.assertRaisesRegex(Decision, 'has session material; collect it first'):
            support.cleanup(self.store, 'experiments/trial', 'premature')
        frozen = (self.folder() / 'finalization.json').read_bytes()
        atomic = files.atomic

        def interrupted(path, data):
            if path == catalogue:
                raise OSError('interrupted before the catalogue')
            return atomic(path, data)
        with mock.patch.object(files, 'atomic', interrupted), self.assertRaisesRegex(OSError, 'catalogue'):
            support.collect(self.store, 'experiments/trial')
        self.assertFalse(Path(trial['packet']).exists())
        self.assertEqual(read(catalogue), original)
        # The captured packet now lies only in the trial's collection: cleanup never records it as unused.
        with self.assertRaisesRegex(Decision, 'interrupted collect already moved run-900101 toward '
                                              'experiments/trial/collection; finish it with berman support-trial '
                                              'collect --evidence experiments/trial'):
            support.cleanup(self.store, 'experiments/trial', 'premature')
        self.assertEqual(read(self.folder() / 'trial.json')['phase'], 'launching')
        self.assertFalse((self.folder() / 'cleanup.json').exists() or (self.folder() / 'unused-packet').exists())
        expected = dict(original, support_collections=original['support_collections'] + ['experiments/trial/collection'])

        def unrecorded(path, data):
            if path.name == 'trial.json' and b'"collected_at_utc"' in data:
                raise OSError('interrupted after the catalogue')
            return atomic(path, data)
        with mock.patch.object(files, 'atomic', unrecorded), self.assertRaisesRegex(OSError, 'after the catalogue'):
            support.collect(self.store, 'experiments/trial')
        self.assertEqual(catalogue.read_bytes(), json_bytes(expected))
        self.assertEqual(read(self.folder() / 'trial.json')['phase'], 'launching')
        self.assertEqual(support.collect(self.store, 'experiments/trial')['phase'], 'collected')
        self.assertEqual((self.folder() / 'finalization.json').read_bytes(), frozen)
        self.assertEqual(catalogue.read_bytes(), json_bytes(expected))
        support.collect(self.store, 'experiments/trial')
        self.assertEqual(catalogue.read_bytes(), json_bytes(expected))

    def test_only_the_rename_holds_the_live_packet_lock(self):
        for adapter in (n for n, a in adapters.ADAPTERS.items() if a['harness'] == 'codex'):
            name = f'trial-{adapter}'
            trial = self.prepare(name, adapter)
            self.launch(name)
            self.session(trial)
            live = self.store.collector_for(self.store.record(trial['run']))
            entry, rename, observed = fieldwork.migration_entry, os.rename, {}

            def during_inventory(root, run, destination):
                with live.locked():  # Free while the packet is inventoried and hashed.
                    observed['inventory'] = 'free'
                return entry(root, run, destination)

            def during_rename(source, destination):
                if Path(source).name == trial['run']:
                    with self.assertRaisesRegex(ValueError, 'Another capture is still running'):
                        with live.locked():
                            pass
                    observed['rename'] = 'held'
                return rename(source, destination)
            with self.subTest(adapter=adapter), \
                 mock.patch.object(fieldwork, 'migration_entry', side_effect=during_inventory), \
                 mock.patch.object(fieldwork.os, 'rename', side_effect=during_rename):
                support.collect(self.store, f'experiments/{name}')
                self.assertEqual(observed, {'inventory': 'free', 'rename': 'held'})
                self.assertFalse((self.folder(name) / 'collection/.berman-locks').exists())


class CleanupTests(SupportBase):
    STATE = {'berman-codex': 'berman-capture'}

    def test_cleanup_retires_the_registration_keeps_the_profile_and_removes_an_empty_project(self):
        for adapter in (n for n, a in adapters.ADAPTERS.items() if a['harness'] == 'codex'):
            with self.subTest(adapter=adapter):
                self.retires(adapter)

    def retires(self, adapter):
        name = f'trial-{adapter}'
        trial = self.prepare(name, adapter)
        self.launch(name)
        event, _ = self.session(trial)
        support.collect(self.store, f'experiments/{name}')
        # Until retirement, a late callback for the closed session reports its moved packet as missing.
        self.assertIn('missing', route(dict(event, hook_event_name='Stop'), self.home, self.root)['systemMessage'])
        registry = self.home / self.STATE[adapter] / (event['session_id'] + '.json')
        before = registry.read_bytes()
        file_lock, order = runtime.file_lock, []

        def observed(path):
            order.append(Path(path).relative_to(Path(path).parent.parent).as_posix())
            return file_lock(path)
        # The runtime's order: the session lock, then the packet lock.
        with mock.patch.object(runtime, 'file_lock', observed):
            result = support.cleanup(self.store, f'experiments/{name}', 'Trial collected and reviewed')
        self.assertEqual(order, [f'berman-session-locks/{event["session_id"]}.lock',
                                 f'.berman-locks/capture-{trial["run"]}.lock'])
        folder = self.folder(name)
        self.assertEqual((folder / 'registry-before-retirement.json').read_bytes(), before)
        after = read(registry)
        self.assertEqual(read(folder / 'registry-after-retirement.json'), after)
        self.assertEqual(set(after) - set(json.loads(before)), {'retired', 'retired_at_utc', 'retirement_reason', 'preserved_packet'})
        self.assertEqual((after['retired'], after['preserved_packet'], after['run'], after['workspace']),
                         (True, f'experiments/{name}/collection/runs/{trial["run"]}', trial['run'], str(self.root)))
        self.assertEqual(route(dict(event, hook_event_name='Stop'), self.home, self.root), {})
        self.assertEqual(read(registry), after)
        self.assertEqual((result['phase'], result['support_registry_retired'], result['empty_disposable_project_removed']),
                         ('cleaned', str(registry), trial['project']))
        self.assertFalse(Path(trial['project']).exists())
        self.assertTrue((self.home / (trial['profile'] + '.config.toml')).is_file())
        self.assertEqual((result['opt_in_profile_retained'], result['unused_packet'], result['project_retained'],
                          result['open_turn_at_retirement']), (trial['profile'], None, None, None))
        self.assertEqual(result['codex_config_sha256'], {'at_preparation': None, 'at_cleanup': None})
        self.assertEqual(read(folder / 'cleanup.json'), {k: v for k, v in result.items() if k != 'phase'})
        files = {p: p.read_bytes() for p in folder.rglob('*') if p.is_file()}
        again = support.cleanup(self.store, f'experiments/{name}', 'Retried')
        self.assertEqual((again['state'], again['at_utc']), ('already-cleaned', result['at_utc']))
        self.assertEqual({p: p.read_bytes() for p in folder.rglob('*') if p.is_file()}, files)
        self.assertNotIn(trial['run'], [t['run'] for t in self.workflow.status()['support_trials']])
        self.assertEqual(support.record_capture(self.store, f'experiments/{name}')['state'], 'recorded')

    def test_cleanup_refuses_a_packet_still_in_the_field_and_records_a_later_open_turn(self):
        for adapter in (n for n, a in adapters.ADAPTERS.items() if a['harness'] == 'codex'):
            name = f'trial-{adapter}'
            trial = self.prepare(name, adapter)
            self.launch(name)
            event, _ = self.session(trial)
            registry = self.home / self.STATE[adapter] / (event['session_id'] + '.json')
            with self.subTest(adapter=adapter):
                with self.assertRaisesRegex(Decision, 'has session material; collect it first'):
                    support.cleanup(self.store, f'experiments/{name}', 'premature')
                support.collect(self.store, f'experiments/{name}')
                # A prompt after collect opens a turn that no callback can close: the packet has left the field.
                route(dict(event, hook_event_name='UserPromptSubmit', prompt='one more thing'), self.home, self.root)
                for terminal in ('Stop', 'SessionEnd'):
                    self.assertIn('missing', route(dict(event, hook_event_name=terminal), self.home, self.root)['systemMessage'])
                self.assertTrue(read(registry)['active_turn'])
                result = support.cleanup(self.store, f'experiments/{name}', 'Session ended after a stray prompt')
                self.assertEqual((result['phase'], result['open_turn_at_retirement']), ('cleaned', support.OPEN_TURN))
                self.assertTrue(read(self.folder(name) / 'registry-before-retirement.json')['active_turn'])
                self.assertTrue(read(registry)['retired'])
                self.assertEqual(route(dict(event, hook_event_name='Stop'), self.home, self.root), {})

    def test_a_session_killed_mid_turn_is_collected_incomplete_and_cleaned_up(self):
        trial = self.prepare()
        self.launch()
        event, _ = self.session(trial, events=())  # Codex was killed after the prompt: no Stop or SessionEnd.
        with self.assertRaisesRegex(Decision, 'a submitted prompt has no later terminal callback'):
            support.collect(self.store, 'experiments/trial')
        collected = support.collect(self.store, 'experiments/trial', 'Codex was killed mid-turn')
        self.assertEqual((collected['phase'], collected['demonstrates_capture']), ('collected-incomplete', False))
        self.assertIn('A prompt was submitted without a later terminal callback',
                      json.dumps(read(self.folder() / 'finalization.json')))
        result = support.cleanup(self.store, 'experiments/trial', 'The session was killed and has ended')
        self.assertEqual((result['phase'], result['open_turn_at_retirement'], result['empty_disposable_project_removed']),
                         ('cleaned', support.OPEN_TURN, trial['project']))
        self.assertEqual(read(self.folder() / 'cleanup.json')['open_turn_at_retirement'], support.OPEN_TURN)
        self.assertTrue(read(self.home / self.STATE['berman-codex'] / (event['session_id'] + '.json'))['retired'])
        self.assertFalse(Path(trial['project']).exists())
        self.assertEqual(self.workflow.status()['support_trials'], [])
        self.assertEqual(route(dict(event, hook_event_name='SessionEnd'), self.home, self.root), {})

    def test_cleanup_of_a_never_registered_trial_retains_its_unused_packet_and_a_used_project(self):
        trial = self.prepare()
        (Path(trial['project']) / 'notes.txt').write_text('left behind\n')
        inventory = files.inventory(Path(trial['packet']))
        result = support.cleanup(self.store, 'experiments/trial', 'The session never started')
        folder = self.folder()
        self.assertEqual((result['phase'], result['unused_packet'], result['support_registry_retired']),
                         ('abandoned', 'experiments/trial/unused-packet', None))
        self.assertFalse(Path(trial['packet']).exists())
        self.assertEqual(files.inventory(folder / 'unused-packet'), inventory)
        self.assertEqual(result['project_retained'], {'path': trial['project'],
                                                      'reason': 'Retained: it holds files outside .git: notes.txt'})
        self.assertTrue(Path(trial['project']).is_dir())
        self.assertEqual(read(folder / 'trial.json')['phase'], 'abandoned')
        # The marker keeps the identity reserved: never research, never reused.
        self.assertEqual(self.store.discover(), [])
        self.assertEqual(self.store.allocate_support([]), 'run-900102')
        self.assertEqual(self.workflow.status()['support_trials'], [])
        self.assertEqual(support.cleanup(self.store, 'experiments/trial', 'again')['state'], 'already-cleaned')


class RecordCaptureTests(SupportBase):
    def basis_path(self, adapter='berman-codex'):
        return self.root / adapters.ADAPTERS[adapter]['basis']

    def test_record_capture_links_a_verified_trial_for_the_same_bytes(self):
        collected = self.collected()
        path = self.basis_path()
        before, other = path.read_bytes(), self.basis_path('berman-claude').read_bytes()
        self.assertIsNone(adapters.native_capture(self.store, 'berman-codex'))
        ready = adapters.readiness(self.store, 'berman-codex')
        result = support.record_capture(self.store, 'experiments/trial')
        self.assertEqual(result['state'], 'recorded')
        expected = {'path': 'experiments/trial/collection/runs/run-900101/session/captures.jsonl',
                    'required_events': ['Stop', 'SessionEnd'], 'run': 'run-900101', 'session_id': collected['session_id'],
                    'trial_receipt': 'experiments/trial/trial.json',
                    'implementation_sha256': adapters.implementation('berman-codex'),
                    'basis': support.CAPTURE_BASIS}
        basis = read(path)
        self.assertEqual(basis['successful_capture'], expected)
        self.assertIn('support run-900101', basis['meaning'])
        self.assertEqual(list(basis), list(json.loads(before)))
        history = sorted((path.parent / 'history').glob('berman-codex-before-capture-*.json'))
        self.assertEqual([p.read_bytes() for p in history], [before])
        self.assertEqual(adapters.native_capture(self.store, 'berman-codex')['run'], 'run-900101')
        self.assertEqual(result['native_capture']['trial_receipt'], 'experiments/trial/trial.json')
        self.assertEqual(adapters.readiness(self.store, 'berman-codex'), dict(ready, meaning=basis['meaning']))
        self.assertEqual(self.basis_path('berman-claude').read_bytes(), other)
        self.assertEqual(support.record_capture(self.store, 'experiments/trial')['state'], 'already-recorded')
        self.assertEqual(len(list((path.parent / 'history').glob('*-before-capture-*.json'))), 1)
        self.assertEqual(self.workflow.status()['support_trials'][0]['next'],
                         'berman support-trial cleanup --evidence experiments/trial --reason TEXT')
        # A later trust renewal for changed bytes drops the link, as before.
        evidence = self.base / 'review.terminal'
        evidence.write_text('Fixture review\n')
        version = subprocess.CompletedProcess([], 0, 'codex-cli 0.0.0-fixture\n', '')
        with mock.patch.object(adapters.subprocess, 'run', return_value=version), \
             mock.patch.object(adapters, 'implementation', return_value={'_legacy/capture_session.py': '0' * 64}):
            adapters.record_trust(self.store, evidence, 'fixture review after a code change')
        self.assertIsNone(read(path)['successful_capture'])

    def test_record_capture_refuses_unreviewed_or_changed_bytes_other_hooks_and_other_links(self):
        self.collected()
        path, folder = self.basis_path(), self.folder()
        original = read(path)
        self.basis('berman-codex', implementation_sha256=dict(original['implementation_sha256'],
                                                              **{'_legacy/capture_session.py': '0' * 64}))
        with self.assertRaisesRegex(Decision, 'with berman capture record-trust first'):
            support.record_capture(self.store, 'experiments/trial')
        # record-trust reads --evidence from the working directory, so the step names the absolute path.
        self.assertIn(f'berman capture record-trust --evidence {folder}/native.terminal --attest',
                      self.workflow.status()['support_trials'][0]['next'])
        write(path, original)
        with mock.patch.object(adapters, 'implementation', return_value={'_legacy/capture_session.py': '0' * 64}):
            with self.assertRaisesRegex(Decision, 'run a new support trial for these bytes'):
                support.record_capture(self.store, 'experiments/trial')
        trial = read(folder / 'trial.json')
        write(folder / 'trial.json', dict(trial, installed_hooks_sha256='0' * 64))
        with self.assertRaisesRegex(Decision, 'review of other hooks'):
            support.record_capture(self.store, 'experiments/trial')
        write(folder / 'trial.json', trial)
        self.trust()  # Now another run's capture is linked for these same bytes.
        with self.assertRaisesRegex(Decision, 'already records a native capture.*run-900101.*not replaced'):
            support.record_capture(self.store, 'experiments/trial')
        self.assertEqual(read(path)['successful_capture']['path'], 'fixture-captures/berman-codex.jsonl')
        # So status names no record-capture that would refuse; the trial goes straight to cleanup.
        self.assertEqual(self.workflow.status()['support_trials'][0]['next'],
                         'berman support-trial cleanup --evidence experiments/trial --reason TEXT; run-900101 already '
                         'records the native capture for these implementation bytes, so this trial is not linked')
        with mock.patch.object(adapters, 'implementation', return_value={'_legacy/capture_session.py': '0' * 64}):
            self.assertIn('capture code changed after this trial, so it cannot be linked',
                          self.workflow.status()['support_trials'][0]['next'])
        self.trust(captures=())
        (folder / 'collection/runs/run-900101/session/conversation.txt').write_text('altered after preservation\n')
        with self.assertRaisesRegex(ValueError, 'inventory differs'):
            support.record_capture(self.store, 'experiments/trial')
        self.assertIsNone(read(path)['successful_capture'])


    def test_the_basis_is_judged_under_the_installation_lock(self):
        self.collected()
        path, lock = self.basis_path(), self.store.lock
        renewed = dict(read(self.home / 'hooks.json'), note='renewed review of changed hooks')

        @contextmanager
        def renewing(kind, name=None, wait=0):
            with lock(kind, name, wait):
                if kind == 'installation':
                    # As record_trust might have just done: other installed hooks, the same implementation bytes.
                    write(self.home / 'hooks.json', renewed)
                    write(self.root / 'hooks/verification/observations/fixture/installed-hooks.json', renewed)
                yield
        self.assertEqual(adapters.readiness(self.store, 'berman-codex')['adapter'], 'berman-codex')
        with mock.patch.object(self.store, 'lock', renewing), self.assertRaisesRegex(Decision, 'review of other hooks'):
            support.record_capture(self.store, 'experiments/trial')
        self.assertIsNone(read(path)['successful_capture'])
        self.assertFalse((path.parent / 'history').exists())


class StatusTests(SupportBase):
    def test_an_unreadable_or_foreign_receipt_is_a_problem_line_not_a_crash(self):
        trial = self.prepare()
        for content in ('[]', '{"schema": "berman.support-trial/1"}', 'not json'):
            (self.folder() / 'trial.json').write_text(content)
            with self.subTest(content=content):
                listed = self.workflow.status()['support_trials']
                self.assertEqual([(t['run'], t['phase'], t['next']) for t in listed], [(trial['run'], None, None)])
                self.assertIn('experiments/trial/trial.json', listed[0]['problems'][0])
                out = io.StringIO()
                with redirect_stdout(out):
                    self.assertEqual(cli.main(['--root', str(self.root), 'status']), 0)
                self.assertIn(f'{trial["run"]} (support trial, berman-codex; not research): None', out.getvalue())
                check = verify.workspace(self.store, None)
                self.assertEqual(check['status'], 'failed')
                self.assertEqual(check['details']['problems'], ['support trial: ' + listed[0]['problems'][0]])


class ReadinessMessageTests(Fixture, Base):
    """installed() was factored out of readiness(); both keep readiness's exact refusals."""

    def test_installed_definition_checks_keep_their_messages(self):
        self.trust(captures=())
        hooks, definition = self.home / 'hooks.json', self.root / 'hooks/berman-capture.hooks.json'
        template = read(definition)
        python = str(self.store.python)

        def refused(message):
            for check in (adapters.installed, adapters.readiness):
                with self.subTest(check=check.__name__, message=message), \
                     self.assertRaisesRegex(Decision, '^' + re.escape(message) + '$'):
                    check(self.store, 'berman-codex')
        hooks.unlink()
        refused(f'No Codex hooks are installed at {hooks}; berman-codex capture is unavailable')
        write(hooks, template)
        definition.unlink()
        refused(f'The berman-codex hook definition is missing from this workspace: {definition}')
        write(definition, template)
        write(hooks, {'hooks': {}})
        refused('Installed UserPromptSubmit berman-codex capture hook differs; inspect it before launching. '
                'To install the router, use berman capture install --adapter berman-codex')
        broken = json.loads(json.dumps(template))
        broken['hooks']['UserPromptSubmit'][0]['hooks'][0]['command'] = f'"{python}" -I -B "/nowhere/router.py" hook'
        write(definition, broken)
        write(hooks, broken)
        refused('UserPromptSubmit collector or interpreter is unavailable')
        write(definition, template)
        direct = json.loads(json.dumps(template))
        direct['hooks']['Stop'].append({'hooks': [{'type': 'command', 'timeout': 10,
                                                   'command': f'"{python}" -I -B "{ROOT}/tools/capture_session.py" hook'}]})
        write(hooks, direct)
        refused('Stop: capture routing is not exclusive; install the reviewed single-router definition')
        write(hooks, template)
        found = adapters.installed(self.store, 'berman-codex')
        self.assertEqual(found, {'adapter': 'berman-codex', 'config_path': str(hooks), 'installed': template,
                                 'definition': template})
        self.assertEqual(set(adapters.readiness(self.store, 'berman-codex')), {'adapter', 'events', 'basis', 'meaning'})


class CommandLineTests(SupportBase):
    def main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(['--root', str(self.root), *argv])
        return code, out.getvalue(), err.getvalue()

    def test_cli_round_trip(self):
        with mock.patch.object(support, 'Codex', FakeCodex):
            code, out, _ = self.main('--json', 'support-trial', 'prepare', '--evidence', 'experiments/cli',
                                     '--adapter', 'berman-codex')
        trial = json.loads(out)
        self.assertEqual((code, trial['run'], trial['phase']), (0, 'run-900101', 'prepared'))
        code, out, _ = self.main('status')
        self.assertIn('run-900101 (support trial, berman-codex; not research): prepared; capture: prepared; next: '
                      'berman support-trial launch --evidence experiments/cli', out)
        self.assertIn('No outstanding engagements or preparations.', out)
        self.launch('cli')
        self.session(trial)
        code, out, _ = self.main('--json', 'support-trial', 'collect', '--evidence', 'experiments/cli')
        self.assertEqual((code, json.loads(out)['phase']), (0, 'collected'))
        code, out, _ = self.main('--json', 'capture', 'record-capture', '--trial', 'experiments/cli')
        self.assertEqual((code, json.loads(out)['state']), (0, 'recorded'))
        self.assertEqual(adapters.native_capture(self.store, 'berman-codex')['run'], 'run-900101')
        code, out, _ = self.main('--json', 'support-trial', 'cleanup', '--evidence', 'experiments/cli', '--reason', 'Done')
        self.assertEqual((code, json.loads(out)['phase']), (0, 'cleaned'))
        code, out, err = self.main('--json', 'support-trial', 'launch', '--evidence', 'experiments/cli')
        self.assertEqual((code, out, json.loads(err)['state']), (2, '', 'needs-attention'))
        code, out, err = self.main('--json', 'support-trial', 'prepare', '--evidence', 'runs/x', '--adapter', 'berman-codex')
        self.assertEqual((code, json.loads(err)['state']), (2, 'needs-attention'))
        self.assertFalse((self.root / 'runs/x').exists())


if __name__ == '__main__':
    unittest.main()
