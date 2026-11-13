"""Exercise the operator workflow against isolated repositories and real capture records."""
from tests.support.paths import REPO
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile
from contextlib import redirect_stdout, redirect_stderr
from contextlib import contextmanager

from berman.records import Decision, Store
from berman.installation import PACKAGE as ROOT
from berman import files
from berman.files import read, write
from berman._legacy.api import MARKER
from berman.workflow import Workflow
from berman.harnesses.codex import COMMON_CHECKS, Codex, profile, profile_name
from berman.capture import collector
from berman import preserve
from berman import cli
from berman import recovery as fieldwork


from tests.support.workspace import FixtureHarness, workspace, git_repository, legacy_hooks, legacy_basis


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name).resolve()
        self.root = self.base / 'berman'
        self.project = self.base / 'stewart'
        self.home = self.base / 'codex'
        self.home.mkdir()
        workspace(self.root, self.base, self.project, documents=['README.md', 'COORDINATOR-HANDOFF.md', 'missing.txt'])
        git_repository(self.project, {'README.md': 'Project context\n', 'COORDINATOR-HANDOFF.md': 'Ordinary handoff\n'})
        self.store = Store(self.root)
        self.harness = FixtureHarness(self.store)
        self.workflow = Workflow(self.store, self.harness)

    def start(self, run, stop=True, end=False):
        sid = 'fixture-' + run
        source = self.home / 'sessions' / (sid + '.jsonl')
        source.parent.mkdir(exist_ok=True)
        records = [{'type': 'session_meta', 'payload': {'id': sid}},
                   {'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant', 'content': [{'text': 'done'}]}}]
        source.write_text(''.join(json.dumps(row) + '\n' for row in records))
        event = {'session_id': sid, 'transcript_path': str(source), 'turn_id': 'one'}
        self.store.collector.hook(dict(event, hook_event_name='UserPromptSubmit', prompt=MARKER + run))
        if stop:
            self.store.collector.hook(dict(event, hook_event_name='Stop'))
        if end:
            self.store.collector.hook(dict(event, hook_event_name='SessionEnd'))
        return source

    def test_fresh_preparation_and_retry_do_not_consume_sequence(self):
        one = self.workflow.new()
        self.assertEqual(one['run'], 'run-001')
        self.assertEqual(self.workflow.new()['run'], one['run'])
        self.assertEqual(self.workflow.status()['next_recipe'], 'work-accounts')
        text = (Path(one['packet']) / 'INSTRUCTIONS.md').read_text()
        original = (REPO / 'tests/fixtures/workspace/recipes/work-accounts/INSTRUCTIONS.md').read_text()
        self.assertEqual(text, original.replace('{{notes_directory}}', one['packet'] + '/notes/'))
        self.assertNotIn('inquiry-after-repeat', (Path(one['packet']) / 'ASSIGNMENT.md').read_text())
        self.assertTrue(read(self.store.folder(one['run']) / 'preparation/initial-project/snapshot.json')['files'][-1]['missing'])

    def test_failed_check_retries_same_reservation_and_recipe(self):
        self.harness.fail = True
        with self.assertRaisesRegex(Decision, 'check failed'):
            self.workflow.new()
        source = self.root / 'recipes/work-accounts/INSTRUCTIONS.md'
        source.write_text(source.read_text().replace('periodically', 'constantly'))
        self.harness.fail = False
        result = self.workflow.new()
        self.assertEqual(result['run'], 'run-001')
        self.assertIn('periodically', (Path(result['packet']) / 'INSTRUCTIONS.md').read_text())
        self.assertEqual(len(self.store.discover()), 1)

    def test_live_or_closed_session_requires_an_engagement_decision(self):
        result = self.workflow.new(); self.start(result['run'], end=True)
        with self.assertRaisesRegex(Decision, 'no declared ending'):
            self.workflow.new()
        self.assertFalse((self.store.field / result['run'] / 'session/finalized.json').exists())
        self.assertEqual(len(self.store.discover()), 1)
        next_run = self.workflow.new(keep_open=True)
        self.assertEqual(next_run['recipe'], 'participant-inquiry')
        self.assertEqual(next_run['retained_open_runs'], [result['run']])

    def test_finish_and_new_collect_documents_and_archive_all_packet_bytes(self):
        first = self.workflow.new(); self.start(first['run'])
        packet = Path(first['packet'])
        (packet / 'notes/one.txt').write_text('An actual participant account.\n')
        (self.project / 'COORDINATOR-HANDOFF.md').write_text('Closing handoff\n')
        second = self.workflow.new(finish_previous='User said the encounter is concluded')
        self.assertEqual(second['run'], 'run-002')
        self.assertEqual(second['recipe'], 'participant-inquiry')
        self.assertFalse(packet.exists())
        archived = self.root / 'runs/run-001'
        self.assertEqual((archived / 'notes/one.txt').read_text(), 'An actual participant account.\n')
        self.assertEqual((archived / 'context/berman/closing-project/COORDINATOR-HANDOFF.md').read_text(), 'Closing handoff\n')
        self.assertEqual((archived / 'context/berman/preparation/initial-project/COORDINATOR-HANDOFF.md').read_text(), 'Ordinary handoff\n')
        package = self.store.record('run-001')['package']
        self.assertEqual(files.digest(Path(package['path']).read_bytes()), package['sha256'])
        with zipfile.ZipFile(package['path']) as z:
            self.assertEqual(z.read('stewart-analysis/runs/run-001/notes/one.txt'), b'An actual participant account.\n')

    def test_failed_packaging_is_resumed_by_new(self):
        first = self.workflow.new(); self.start(first['run'])
        with mock.patch.object(preserve, 'package', side_effect=OSError('power loss')):
            with self.assertRaisesRegex(OSError, 'power loss'):
                self.workflow.finish('The engagement ended')
        self.assertEqual(self.store.record('run-001')['phase'], 'closing')
        result = self.workflow.new()
        self.assertEqual(result['preserved'][0]['run'], 'run-001')
        self.assertEqual(self.store.record('run-001')['phase'], 'archived')
        self.assertEqual(result['recipe'], 'participant-inquiry')

    def test_interrupted_migration_resumes_without_recollecting_context(self):
        first = self.workflow.new(); self.start(first['run'])
        atomic = files.atomic
        def fail(path, data):
            if path == self.root / 'source-layout.json':
                raise OSError('inventory update interrupted')
            return atomic(path, data)
        with mock.patch.object(files, 'atomic', side_effect=fail):
            with self.assertRaisesRegex(OSError, 'interrupted'):
                self.workflow.finish('finished')
        (self.project / 'COORDINATOR-HANDOFF.md').write_text('Later unrelated work\n')
        result = self.workflow.new()
        self.assertEqual(result['run'], 'run-002')
        original = self.root / 'runs/run-001/context/berman/closing-project/COORDINATOR-HANDOFF.md'
        self.assertEqual(original.read_text(), 'Ordinary handoff\n')

    def test_active_turn_retained_until_explicit_incomplete_boundary(self):
        first = self.workflow.new(); self.start(first['run'], stop=False)
        with self.assertRaisesRegex(ValueError, 'Not finalized'):
            self.workflow.finish('Session was lost')
        self.assertEqual(self.store.record('run-001')['ending']['reason'], 'Session was lost')
        final = self.workflow.finish('Session was lost', allow_incomplete='Operator intentionally freezes lost session')
        self.assertEqual(final['state'], 'archived')
        receipt = read(self.root / 'runs/run-001/session/finalized.json')
        self.assertEqual(receipt['boundary']['override_reason'], 'Operator intentionally freezes lost session')

    def test_identity_collision_and_missing_packet_never_allocate(self):
        result = self.workflow.new()
        (self.root / 'runs/run-001').mkdir()
        with self.assertRaisesRegex(Decision, 'active and archived'):
            self.workflow.new()
        (self.root / 'runs/run-001').rmdir()
        shutil.rmtree(Path(result['packet']))
        with self.assertRaisesRegex(Decision, 'packet is missing'):
            self.workflow.new()

    def plan_steps(self, steps):
        plan = self.store.connections['stewart'].plan
        plan.steps, plan.ids = steps, [s['id'] for s in steps]

    def test_parallel_operation_cannot_allocate_twice(self):
        with self.store.lock('connection', 'stewart'):
            with self.assertRaisesRegex(Decision, 'Another Berman operation is using connection stewart'):
                Workflow(Store(self.root), self.harness).new()
        self.assertEqual(self.workflow.new()['run'], 'run-001')

    def test_reserved_identity_survives_interruption_before_packet_creation(self):
        with mock.patch('berman.workflow.prepare', side_effect=OSError('stopped before packet')):
            with self.assertRaises(OSError):
                self.workflow.new()
        self.assertEqual(self.store.record('run-001')['phase'], 'reserved')
        self.assertEqual(self.workflow.new()['run'], 'run-001')

    def test_sequence_exhaustion_does_not_invent_another_commission(self):
        for number in (1, 2):
            result = self.workflow.new(); self.start(result['run']); self.workflow.finish('finished')
        with self.assertRaisesRegex(Decision, 'sequence is complete'):
            self.workflow.new()
        self.assertEqual(len(self.store.discover()), 2)

    def test_profile_only_exposes_assigned_packet(self):
        import tomllib
        policy = tomllib.loads(profile(self.store, 'run-123', self.project))
        access = policy['permissions'][f'berman_{self.store.tag}_run_123']['filesystem']
        self.assertEqual(access[str(self.store.root)], 'deny')
        self.assertEqual(access[str(self.store.field)], 'deny')
        self.assertEqual(access[str(self.store.field / 'run-123')], 'write')
        self.assertEqual(access[str(self.project / '.git')], 'read')
        self.assertNotIn('model', policy)

    def test_unrelated_existing_profile_is_not_overwritten(self):
        self.workflow.new()
        conflict = self.home / (profile_name(self.store, 'run-001') + '.config.toml')
        conflict.write_text('unrelated profile\n')
        with self.assertRaisesRegex(Decision, 'Existing artifact differs'):
            Codex(self.store).ensure('run-001', self.store.folder('run-001') / 'preparation')
        self.assertEqual(conflict.read_text(), 'unrelated profile\n')

    def test_read_only_status_leaves_files_unchanged(self):
        self.workflow.new()
        before = {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.workflow.status()
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_cancelled_preparation_is_retained_and_does_not_consume_role(self):
        first = self.workflow.new()
        original = (Path(first['packet']) / 'ASSIGNMENT.md').read_bytes()
        self.workflow.cancel('Preparation no longer wanted')
        self.assertEqual((self.store.folder(first['run']) / 'unused-packet/ASSIGNMENT.md').read_bytes(), original)
        second = self.workflow.new()
        self.assertEqual(second['run'], 'run-002')
        self.assertEqual(second['recipe'], 'work-accounts')

    def test_changed_unstarted_context_retains_identity_and_preparation_snapshot(self):
        first = self.workflow.new()
        (self.project / 'README.md').write_text('Changed before launch\n')
        second = self.workflow.new()
        self.assertEqual(second['run'], first['run'])
        self.assertEqual(second['recipe'], 'work-accounts')
        old = self.store.folder(first['run']) / 'preparation/initial-project/README.md'
        self.assertEqual(old.read_text(), 'Project context\n')
        self.assertTrue(second['checks_reused'])
        with mock.patch('os.execv') as launch:
            self.workflow.launch()
        launch.assert_called_once()
        record = self.store.record(first['run'])
        self.assertEqual((Path(record['launch_attempts'][-1]['context']) / 'README.md').read_text(), 'Changed before launch\n')

    def test_retirement_cannot_hide_a_started_session(self):
        first = self.workflow.new(); row = self.store.discover()[0]
        self.start(first['run'])
        with self.assertRaisesRegex(Decision, 'session material'):
            self.workflow.retire(row['record'], 'stale observation')
        self.assertTrue((Path(first['packet']) / 'session/binding.json').exists())

    def test_launch_records_changed_document_even_with_same_dirty_status(self):
        (self.project / 'README.md').write_text('Dirty version one\n')
        first = self.workflow.new()
        (self.project / 'README.md').write_text('Dirty version two\n')
        with mock.patch('os.execv') as launch:
            self.workflow.launch()
            launch.assert_called_once()
        record = self.store.record(first['run'])
        self.assertEqual((Path(record['launch_attempts'][-1]['context']) / 'README.md').read_text(), 'Dirty version two\n')

    def test_known_support_collection_does_not_set_fieldwork_number(self):
        support = self.root / 'support-trial'
        write(support / 'source-layout.json', {'runs': [{'run': 'run-900101', 'current_root': 'runs/run-900101'}]})
        write(support / 'runs/run-900101/session/binding.json', {'run': 'run-900101', 'session_id': 'support-session'})
        write(self.home / 'stewart-fieldwork/support-session.json', {'run': 'run-900101', 'session_id': 'support-session'})
        self.store.catalogue.support = ['support-trial']
        self.assertEqual(self.workflow.new()['run'], 'run-001')
        write(self.home / 'stewart-fieldwork/missing-session.json', {'run': 'run-900102', 'session_id': 'missing-session'})
        # Status reports the problem; operations on its connection refuse to proceed.
        status = self.workflow.status()
        self.assertIn('packet is missing', status['connections']['stewart']['blocked_by'][0])
        with self.assertRaisesRegex(Decision, 'packet is missing'):
            self.workflow.new()

    def test_low_level_finalization_does_not_declare_an_ending(self):
        first = self.workflow.new(); self.start(first['run'])
        self.store.collector.command('finalize', first['run'])
        with self.assertRaisesRegex(Decision, 'no declared ending'):
            self.workflow.new()
        self.assertNotIn('ending', self.store.record(first['run']))
        status = self.workflow.status()['runs'][0]
        self.assertEqual((status['lifecycle']['finalized']['value'], status['lifecycle']['ending']['value']), (True, False))
        self.assertIn('not an ending', status['summary'])
        self.assertEqual(self.workflow.new(keep_open=True)['run'], 'run-002')

    def test_launch_verifies_packet_then_invokes_exact_native_argv(self):
        first = self.workflow.new()
        with mock.patch('os.execv') as execute:
            self.workflow.launch()
        execute.assert_called_once_with('/fixture/codex', ['/fixture/codex', first['run']])
        with self.assertRaisesRegex(Decision, 'launch attempt without a session binding'):
            self.workflow.new()
        record = self.store.record(first['run']); record['phase'] = 'ready'; self.store.save(record)
        (Path(first['packet']) / 'INSTRUCTIONS.md').write_text('changed instructions')
        with mock.patch('os.execv') as execute:
            with self.assertRaisesRegex(Decision, 'INSTRUCTIONS.md changed'):
                self.workflow.launch()
            execute.assert_not_called()

    def test_failed_exec_is_recorded_and_can_retry(self):
        first = self.workflow.new()
        with mock.patch('os.execv', side_effect=OSError('executable disappeared')):
            with self.assertRaisesRegex(OSError, 'disappeared'):
                self.workflow.launch()
        record = self.store.record(first['run'])
        self.assertEqual(record['phase'], 'ready')
        self.assertIn('disappeared', record['launch_attempts'][-1]['exec_error'])
        self.assertEqual(self.workflow.new()['run'], first['run'])

    def append_source(self, source, text='later work'):
        with source.open('a') as stream:
            stream.write(json.dumps({'type': 'response_item', 'payload': {
                'type': 'message', 'role': 'user', 'content': [{'text': text}]}}) + '\n')

    def call_cli(self, *args, json_mode=True):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(cli, 'Workflow', return_value=self.workflow), redirect_stdout(out), redirect_stderr(err):
            code = cli.main(['--root', str(self.root), *(['--json'] if json_mode else []), *args])
        return code, out.getvalue(), err.getvalue()

    def test_cheatsheet_follows_the_recorded_situation_without_changing_files(self):
        before = sorted(p.relative_to(self.root) for p in self.root.rglob('*'))
        sheet = self.workflow.cheatsheet()
        conn = sheet['connections'][0]
        self.assertEqual((conn['id'], conn['default'], conn['next_recipe'], conn['checkout']),
                         ('stewart', True, 'work-accounts', str(self.project)))
        self.assertEqual((conn['development'], conn['commands']['launch'], conn['commands']['finish']), ([], [], []))
        self.assertTrue(conn['commands']['new'].endswith(f' --root {self.root} new'), conn['commands']['new'])
        self.assertEqual(sorted(p.relative_to(self.root) for p in self.root.rglob('*')), before)
        first = self.workflow.new()
        conn = self.workflow.cheatsheet()['connections'][0]
        self.assertEqual(conn['fieldwork']['ready'], [first['run']])
        self.assertEqual(conn['commands']['launch'], [self.workflow.command('launch')])
        self.start(first['run'])
        conn = self.workflow.cheatsheet()['connections'][0]
        self.assertEqual(conn['fieldwork'], {'ready': [], 'incomplete': [], 'open': [first['run']], 'ending': [], 'attention': []})
        self.assertEqual(conn['commands']['finish'], [self.workflow.command('finish', '--run', first['run'], '--reason',
                                                                            'why the engagement ended')])
        second = self.workflow.new(keep_open=True)
        conn = self.workflow.cheatsheet()['connections'][0]
        self.assertEqual((conn['fieldwork']['ready'], conn['fieldwork']['open']), ([second['run']], [first['run']]))
        self.assertEqual(conn['commands']['launch'], [self.workflow.command('launch')])

    def test_cheatsheet_command_prints_the_launch_command_for_a_ready_packet(self):
        first = self.workflow.new()
        before = sorted(p.relative_to(self.root) for p in self.root.rglob('*'))
        code, out, err = self.call_cli('cheatsheet', json_mode=False)
        self.assertEqual((code, err), (0, ''))
        self.assertIn(f'{first["run"]} is prepared and unstarted', out)
        self.assertIn('      ' + self.workflow.command('launch') + '\n', out)
        self.assertIn('no development launcher is configured', out)
        code, out, err = self.call_cli('cheatsheet')
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)['connections'][0]['fieldwork']['ready'], [first['run']])
        self.assertEqual(sorted(p.relative_to(self.root) for p in self.root.rglob('*')), before)

    def test_dev_forwards_checks_in_either_order_and_after_separator(self):
        launcher = self.root / 'fake-dev'
        launcher.write_text('#!/bin/sh\npwd\nprintf "%s\\n" "$@"\n')
        launcher.chmod(0o755)
        config = read(self.root / 'berman.json')
        config['connections']['stewart']['development'] = dict.fromkeys(('codex', 'claude'), str(launcher))
        write(self.root / 'berman.json', config)
        before = sorted(p.relative_to(self.root) for p in self.root.rglob('*'))
        for harness in ('codex', 'claude'):
            for options in (['--harness', harness, '--check'], ['--check', '--harness', harness],
                            ['--harness', harness, '--', '--check']):
                with self.subTest(options=options):
                    result = subprocess.run([sys.executable, '-I', '-B', '-m', 'berman',
                                             '--root', str(self.root), 'dev', '--connection', 'stewart', *options],
                                            cwd=self.base, capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout.splitlines()[-2:], [str(self.project), '--check'])
        result = subprocess.run([sys.executable, '-I', '-B', '-m', 'berman', '--root', str(self.root),
                                 'dev', '--harness', 'codex', '--check', '--', '--model', 'chosen',
                                 '--effort', 'high', '--', '--check is my prompt'],
                                cwd=self.base, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines()[-8:],
                         [str(self.project), '--check', '--model', 'chosen', '--effort', 'high', '--', '--check is my prompt'])
        self.assertEqual(sorted(p.relative_to(self.root) for p in self.root.rglob('*')), before)

    def test_post_finalize_supplement_allows_continuation_then_explicit_finish(self):
        first = self.workflow.new(); source = self.start(first['run'])
        self.store.collector.command('finalize', first['run'])
        frozen = (Path(first['packet']) / 'session/transcript.jsonl').read_bytes()
        self.append_source(source)
        supplied = self.workflow.supplement('Retain continuation', first['run'])
        second = self.workflow.new(keep_open=True)
        self.assertNotIn('ending', self.store.record(first['run']))
        self.workflow.finish('Now concluded', first['run'])
        archived = self.root / 'runs' / first['run']
        self.assertEqual((archived / 'session/transcript.jsonl').read_bytes(), frozen)
        self.assertEqual(len(list((archived / 'session-supplements').iterdir())), 1)
        self.assertEqual((archived / 'session-supplements' / Path(supplied['path']).name / 'session/transcript.jsonl').read_bytes(), source.read_bytes())
        self.assertEqual(self.workflow.new()['run'], second['run'])

    def test_finish_collects_uncovered_later_activity_without_changing_frozen_bytes(self):
        first = self.workflow.new(); source = self.start(first['run'])
        self.store.collector.command('finalize', first['run'])
        frozen = (Path(first['packet']) / 'session/transcript.jsonl').read_bytes()
        self.append_source(source)
        self.workflow.finish('Actual ending')
        archived = self.root / 'runs' / first['run']
        self.assertEqual((archived / 'session/transcript.jsonl').read_bytes(), frozen)
        coverage = read(archived / 'context/berman/source-coverage.json')
        self.assertEqual(coverage['source_sha256'], files.digest(source.read_bytes()))
        self.assertEqual((archived / coverage['retained_in'] / 'session/transcript.jsonl').read_bytes(), source.read_bytes())

    def test_bad_supplement_is_not_mistaken_for_retained_later_activity(self):
        first = self.workflow.new(); source = self.start(first['run'])
        self.store.collector.command('finalize', first['run']); self.append_source(source)
        supplement = self.workflow.supplement('later')
        (Path(supplement['path']) / 'session/conversation.txt').write_text('changed')
        with self.assertRaisesRegex(Decision, 'inconsistent supplement'):
            self.workflow.finish('ended')
        self.assertTrue(Path(first['packet']).exists())

    def test_second_recipe_protocol_survives_preservation_and_packaging(self):
        first = self.workflow.new(); self.start(first['run']); self.workflow.finish('done')
        second = self.workflow.new()
        self.assertEqual(read(Path(second['packet']) / 'run.json')['recording_protocol'], 'participant-inquiry/1')
        self.start(second['run']); self.workflow.finish('done')
        record = self.store.record(second['run'])
        with zipfile.ZipFile(record['package']['path']) as archive:
            meta = json.loads(archive.read('stewart-analysis/runs/run-002/run.json'))
        self.assertEqual(meta['recording_protocol'], 'participant-inquiry/1')

    def test_combined_request_retries_same_target_and_reports_archival_on_error(self):
        first = self.workflow.new(); self.start(first['run']); self.harness.fail = True
        code, out, err = self.call_cli('new', '--finish-previous', 'Concluded')
        self.assertEqual(code, 2)
        failure = json.loads(err)
        self.assertEqual(failure['progress'][0]['run'], first['run'])
        self.assertEqual(failure['progress'][0]['state'], 'archived')
        self.assertEqual(read(Path(failure['request']))['previous_run'], first['run'])
        self.harness.fail = False
        code, out, err = self.call_cli('new', '--finish-previous', 'Concluded')
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)['run'], 'run-002')
        self.assertEqual(self.workflow.new(finish_previous='Concluded')['run'], 'run-002')
        self.assertEqual(len(list((self.store.operations / 'new-requests').glob('*.json'))), 1)

    def test_plain_new_resumes_combined_request_after_native_failure(self):
        first = self.workflow.new(); self.start(first['run']); self.harness.fail = True
        with self.assertRaisesRegex(Decision, 'check failed'):
            self.workflow.new(finish_previous='ended')
        self.harness.fail = False
        self.assertEqual(self.workflow.new()['run'], 'run-002')

    def test_hook_and_exhausted_sequence_checks_precede_combined_ending(self):
        first = self.workflow.new(); self.start(first['run'])
        self.harness.preflight_fail = True
        with self.assertRaisesRegex(Decision, 'hook check failed'):
            self.workflow.new(finish_previous='ended')
        self.assertNotIn('ending', self.store.record(first['run']))
        self.harness.preflight_fail = False
        self.plan_steps(self.store.connections['stewart'].plan.steps[:1])
        with self.assertRaisesRegex(Decision, 'sequence is complete'):
            self.workflow.new(finish_previous='ended')
        self.assertNotIn('ending', self.store.record(first['run']))
        self.assertFalse((Path(first['packet']) / 'session/finalized.json').exists())

    def test_cli_error_uses_finish_command_and_resumes_after_incomplete_boundary(self):
        first = self.workflow.new(); self.start(first['run'], stop=False)
        code, out, err = self.call_cli('new', '--finish-previous', 'Lost session')
        self.assertEqual(code, 2)
        message = json.loads(err)['message']
        self.assertIn('berman finish --run run-001 --reason', message)
        self.assertIn('--allow-incomplete', message)
        code, out, err = self.call_cli('finish', '--reason', 'Lost session', '--allow-incomplete', 'Intentional boundary')
        self.assertEqual(code, 0, err)
        self.assertEqual(self.workflow.new(finish_previous='Lost session')['run'], 'run-002')

    def test_cli_text_reports_partial_success_and_reused_checks(self):
        first = self.workflow.new(); self.start(first['run']); self.harness.fail = True
        code, out, err = self.call_cli('new', '--finish-previous', 'Ended', json_mode=False)
        self.assertEqual(code, 2)
        self.assertIn('Recorded progress: run-001: archived', err)
        self.harness.fail = False
        self.workflow.new(finish_previous='Ended')
        code, out, err = self.call_cli('new', json_mode=False)
        self.assertEqual(code, 0, err)
        self.assertIn('Prior native checks reused', out)
        self.assertNotIn('Native checks passed in this preparation', out)

    def test_reused_preparation_records_current_retained_engagements(self):
        first = self.workflow.new(); self.start(first['run'])
        second = self.workflow.new(keep_open=True)
        self.workflow.finish('Ended first', first['run'])
        self.assertEqual(self.workflow.new()['run'], second['run'])
        record = self.store.record(second['run'])
        self.assertEqual(record['retained_open_runs'], [])
        self.assertEqual(record['retained_open_history'][-1]['previous'], [first['run']])

    def test_multiple_open_selection_points_to_a_real_cli_command(self):
        first = self.workflow.new(); self.start(first['run'])
        second = self.workflow.new(keep_open=True); self.start(second['run'])
        self.plan_steps([*self.store.connections['stewart'].plan.steps, {'id': 'third', 'recipe': 'work-accounts'}])
        code, out, err = self.call_cli('new', '--finish-previous', 'ended')
        self.assertEqual(code, 2)
        self.assertIn('berman finish --run RUN --reason', json.loads(err)['message'])

    def test_entrypoint_ignores_pythonpath_startup_code(self):
        hostile = self.base / 'hostile'; hostile.mkdir()
        marker = self.base / 'startup-ran'
        (hostile / 'sitecustomize.py').write_text(f'open({str(marker)!r}, "w").write("loaded")\n')
        result = subprocess.run([sys.executable, '-I', '-B', '-m', 'berman', '--help'], env=dict(os.environ, PYTHONPATH=str(hostile)), capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(marker.exists())

    @contextmanager
    def native_adapter_fixture(self, run):
        harness = Codex(self.store)
        control = {'fingerprint': {'version': 1}, 'response': 'valid'}
        def invoke(argv, cwd=None, **kwargs):
            if 'sandbox' in argv:
                spec = read(Path(argv[-1]))
                checks = {key: True for key in COMMON_CHECKS | {c['id'] for c in spec['project_checks']}}
                if control['response'] == 'missing-check':
                    checks.pop('assigned_notes_writable')
                observed = {'token': spec['token'], 'checks': checks}
                if control['response'] == 'wrong-token':
                    observed['token'] = 'another-invocation'
                if control['response'] == 'not-a-dict':
                    observed = []
                if control['response'] == 'timeout':
                    raise subprocess.TimeoutExpired(argv, 90)
                return subprocess.CompletedProcess(argv, 0, json.dumps(observed), '')
            paths = ''.join(f'<entry access="deny" escalatable="false"><path>{p}</path></entry>'
                            for p in (self.store.root, self.store.field))
            paths += f'<entry access="write"><path>{self.store.field / run}</path></entry>'
            paths += ''.join(f'<entry access="read"><path>{self.project / p}</path></entry>'
                             for p in ('.git', '.agents', '.codex', '.aws'))
            return subprocess.CompletedProcess(argv, 0, json.dumps({'context': '<environment_context>' + paths}), '')
        with mock.patch.object(harness, 'hooks', return_value={'fixture': True}), \
             mock.patch.object(harness, 'fingerprint', side_effect=lambda *_: dict(control['fingerprint'])), \
             mock.patch.object(harness, 'invoke', side_effect=invoke) as calls, \
             mock.patch('berman.harnesses.codex.shutil.which', return_value='/fixture/executable'):
            yield harness, control, calls

    def test_native_adapter_reuses_only_unchanged_passing_checks(self):
        first = self.workflow.new()
        folder = self.store.folder(first['run']) / 'preparation'
        with self.native_adapter_fixture(first['run']) as (harness, control, calls):
            self.assertFalse(harness.ensure(first['run'], folder)['reused'])
            self.assertEqual(calls.call_count, 2)
            self.assertTrue(harness.ensure(first['run'], folder)['reused'])
            self.assertEqual(calls.call_count, 2)
            control['fingerprint'] = {'version': 2}
            self.assertFalse(harness.ensure(first['run'], folder)['reused'])
            self.assertEqual(calls.call_count, 4)

    def test_native_adapter_failures_invalidate_cached_pass_and_keep_evidence(self):
        first = self.workflow.new()
        folder = self.store.folder(first['run']) / 'preparation'
        with self.native_adapter_fixture(first['run']) as (harness, control, calls):
            harness.ensure(first['run'], folder)
            for version, response in enumerate(('missing-check', 'wrong-token', 'not-a-dict', 'timeout'), 2):
                control.update(fingerprint={'version': version}, response=response)
                with self.subTest(response=response), self.assertRaisesRegex(Decision, 'Native access check did not pass'):
                    harness.ensure(first['run'], folder)
                self.assertFalse(read(folder / 'readiness.json')['passed'])
            reports = [read(p) for p in (folder / 'checks').glob('*/result.json')]
            self.assertEqual(len(reports), 5)
            self.assertEqual(sum(r['passed'] for r in reports), 1)
            control['response'] = 'valid'
            self.assertTrue(harness.ensure(first['run'], folder)['passed'])

    def test_later_partial_tail_requires_explicit_disposition_and_is_preserved(self):
        first = self.workflow.new(); source = self.start(first['run'])
        self.store.collector.command('finalize', first['run'])
        with source.open('ab') as stream:
            stream.write(b'{"unfinished":')
        with self.assertRaisesRegex(Decision, 'berman finish --run'):
            self.workflow.finish('Session lost')
        self.workflow.finish('Session lost', allow_incomplete='Keep the interrupted tail')
        archived = self.root / 'runs' / first['run']
        coverage = read(archived / 'context/berman/source-coverage.json')
        self.assertGreater(coverage['partial_tail_bytes'], 0)
        self.assertEqual(coverage['source_sha256'], files.digest(source.read_bytes()))

    def test_old_inferred_ending_does_not_reintroduce_automatic_archival(self):
        first = self.workflow.new(); source = self.start(first['run'])
        self.store.collector.command('finalize', first['run']); self.append_source(source)
        record = self.store.record(first['run'])
        record.update(phase='closing', ending={
            'reason': 'Existing explicit finalization or migration authorizes completing preservation',
            'basis': 'Previously written finalized.json or migration journal; no new completion inferred.'})
        self.store.save(record)
        self.workflow.new(keep_open=True)
        status = self.workflow.status()['runs'][0]
        self.assertFalse(status['ending_declared'])
        self.assertIs(status['lifecycle']['ending']['value'], False)
        self.assertIn('earlier inferred interpretation retained; not a declaration', status['lifecycle']['ending']['evidence'])
        self.workflow.finish('Actual ending', first['run'])
        record = self.store.record(first['run'])
        self.assertEqual(record['ending']['reason'], 'Actual ending')
        self.assertEqual(len(record['previous_ending_interpretations']), 1)

    def test_combined_request_retries_after_migration_interruption(self):
        first = self.workflow.new(); self.start(first['run'])
        atomic = files.atomic
        def fail(path, data):
            if path == self.root / 'source-layout.json':
                raise OSError('index update interrupted')
            return atomic(path, data)
        with mock.patch.object(files, 'atomic', side_effect=fail):
            code, out, err = self.call_cli('new', '--finish-previous', 'Concluded')
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(err)['progress'][0]['state'], 'migrated; packaging pending')
        self.assertEqual(self.workflow.new(finish_previous='Concluded')['run'], 'run-002')
        self.assertEqual(self.store.record(first['run'])['phase'], 'archived')

    def test_retry_uses_reserved_guidance_even_if_recipe_files_are_unavailable(self):
        first = self.workflow.new(); self.start(first['run']); self.harness.fail = True
        with self.assertRaises(Decision):
            self.workflow.new(finish_previous='Concluded')
        shutil.rmtree(self.root / 'recipes/participant-inquiry')
        self.harness.fail = False
        second = self.workflow.new(finish_previous='Concluded')
        self.assertEqual(second['run'], 'run-002')
        self.assertEqual(read(Path(second['packet']) / 'run.json')['recording_protocol'], 'participant-inquiry/1')

    def test_unrelated_changes_during_access_check_do_not_burn_another_identity(self):
        first = self.workflow.new(); self.start(first['run'])
        second = self.workflow.new(keep_open=True)
        original = self.harness.ensure
        def changed_during_check(run, folder):
            (self.project / 'new-untracked-file').write_text('Another coordinator is working.\n')
            return original(run, folder)
        with mock.patch.object(self.harness, 'ensure', side_effect=changed_during_check), mock.patch('os.execv') as execute:
            self.workflow.launch(second['run'])
        execute.assert_called_once()
        record = self.store.record(second['run'])
        context = read(Path(record['launch_attempts'][-1]['context']) / 'snapshot.json')
        self.assertIn('new-untracked-file', context['status'])
        self.assertEqual(len(self.store.discover()), 2)


if __name__ == '__main__':
    unittest.main()
