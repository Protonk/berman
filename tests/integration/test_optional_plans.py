"""Direct assignments and durable recovery across changing study instructions."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from berman import workflow as workflow_module
from berman.files import Decision, read, write
from berman.records import Store, checked_request
from berman.workflow import Workflow
from tests.support.sessions import start
from tests.support.workspace import FixtureHarness, git_repository, workspace


class OptionalPlanTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.root = self.base / 'study'
        self.project = self.base / 'project'
        (self.base / 'codex').mkdir()
        workspace(self.root, self.base, self.project, documents=['README.md'])
        git_repository(self.project, {'README.md': 'Project context\n'})
        self.load()

    def load(self):
        self.store = Store(self.root)
        self.harness = FixtureHarness(self.store)
        self.workflow = Workflow(self.store, self.harness)

    def select_plan(self, path):
        settings = read(self.root / 'berman.json')
        settings['connections']['stewart']['plan'] = path
        write(self.root / 'berman.json', settings)
        self.load()

    def request(self):
        (path,) = (self.root / 'operations/new-requests').glob('*.json')
        return path, read(path)

    def first_session(self):
        first = self.workflow.new(recipe_name='work-accounts')
        start(self.store, first['run'], end=True)
        return first['run']

    def test_direct_assignment_without_plan_preserves_origin_and_detaches(self):
        self.select_plan(None)
        with self.assertRaisesRegex(Decision, 'choose a recipe'):
            self.workflow.new()
        result = self.workflow.new(recipe_name='work-accounts')
        self.assertEqual(result['assignment_origin'], 'direct')
        record = self.store.record(result['run'])
        self.assertNotIn('plan', record)
        self.assertNotIn('step', record)
        self.assertEqual(self.workflow.status()['runs'][0]['assignment_origin'], 'direct')
        start(self.store, result['run'], end=True)
        finished = self.workflow.finish('Researcher ending')
        with zipfile.ZipFile(finished['package']['path']) as package:
            operation = package.read('evidence/operation.json')
        self.assertIn(b'"assignment_origin": "direct"', operation)

    def test_broken_plan_does_not_veto_direct_assignment_or_launch(self):
        self.select_plan('plans/missing/plan.json')
        status = self.workflow.status()['connections']['stewart']
        self.assertIsNone(status['next_recipe'])
        self.assertIn('missing', status['plan_error'])
        one = self.workflow.new(recipe_name='work-accounts')
        with patch('os.execv') as execute:
            self.workflow.launch(one['run'])
        execute.assert_called_once()
        self.assertTrue(self.workflow.cheatsheet()['connections'][0]['plan_error'])

    def test_planned_direct_planned_sequence_counts_only_its_own_assignments(self):
        one = self.workflow.new()
        self.assertEqual(one['assignment_origin'], 'plan')
        start(self.store, one['run'], end=True)
        self.workflow.finish('First ended')
        direct = self.workflow.new(recipe_name='participant-inquiry')
        start(self.store, direct['run'], end=True)
        self.workflow.finish('Independent encounter ended')
        self.assertEqual(self.workflow.status()['next_recipe'], 'participant-inquiry')
        planned = self.workflow.new()
        self.assertEqual(planned['assignment_origin'], 'plan')
        self.assertEqual(planned['run'], 'run-003')
        self.assertEqual(self.store.record(planned['run'])['step'], 'inquiry-after-repeat')

    def test_retry_uses_reserved_bytes_when_plan_and_recipe_disappear(self):
        original = self.workflow.new()
        record = self.store.record(original['run'])
        instructions = (Path(original['packet']) / 'INSTRUCTIONS.md').read_bytes()
        self.store.connection().plan.path.unlink()
        (self.root / 'recipes/work-accounts/INSTRUCTIONS.md').unlink()
        self.load()
        repeated = self.workflow.new(recipe_name='work-accounts')
        self.assertEqual(repeated['run'], original['run'])
        self.assertEqual(repeated['assignment_origin'], 'plan')
        self.assertEqual(self.store.record(original['run'])['recipe'], record['recipe'])
        self.assertEqual((Path(original['packet']) / 'INSTRUCTIONS.md').read_bytes(), instructions)
        with self.assertRaisesRegex(Decision, 'retains recipe'):
            self.workflow.new(recipe_name='participant-inquiry')

    def test_unknown_historical_assignment_origin_is_not_relabelled(self):
        result = self.workflow.new()
        record = self.store.record(result['run'])
        del record['assignment_origin']
        self.store.save(record)
        self.select_plan(None)
        self.assertIsNone(self.workflow.new()['assignment_origin'])
        self.assertNotIn('assignment_origin', self.store.record(result['run']))

    def test_status_can_describe_nullable_historical_recipe_metadata(self):
        result = self.workflow.new()
        record = self.store.record(result['run'])
        record['recipe']['metadata'] = None
        self.store.save(record)
        self.assertIsNone(self.workflow.status()['runs'][0]['recipe'])

    def test_reserved_request_validation_retains_authority_without_defaults(self):
        value = {
            'schema': 'berman.new-request/2',
            'state': 'pending',
            'connection': 'stewart',
            'previous_run': 'run-001',
            'result_run': 'run-002',
            'finish_reason': 'Declared ending',
        }
        self.assertIs(checked_request(value), value)
        for change in (
            {'result_run': None},
            {'result_run': 'run-001'},
            {'state': 'cancelled'},
            {'connection': None},
            {'finish_reason': ' '},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                checked_request(dict(value, **change))
        cancelled = dict(value, state='cancelled', cancellation={'reason': 'Target withdrawn', 'at_utc': 'now'})
        self.assertIs(checked_request(cancelled), cancelled)

    def test_combined_request_recovery_ignores_changed_live_instructions(self):
        for boundary in (
            'reserved',
            'request-before',
            'request-after',
            'preserved',
            'prepared',
            'complete-before',
            'complete-after',
        ):
            with self.subTest(boundary=boundary):
                self.setUp()
                previous = self.first_session()
                original = {
                    'reserved': self.workflow.reserve,
                    'request-before': self.workflow.save_request,
                    'request-after': self.workflow.save_request,
                    'preserved': workflow_module.finish,
                    'prepared': workflow_module.prepare,
                    'complete-before': self.workflow.save_request,
                    'complete-after': self.workflow.save_request,
                }[boundary]

                def interrupt(*args, boundary=boundary, original=original, **kwargs):
                    if boundary.startswith('complete') and args[0]['state'] != 'complete':
                        return original(*args, **kwargs)
                    if not boundary.endswith('before'):
                        original(*args, **kwargs)
                    raise RuntimeError('Synthetic process stop')

                owner, name = {
                    'reserved': (self.workflow, 'reserve'),
                    'request-before': (self.workflow, 'save_request'),
                    'request-after': (self.workflow, 'save_request'),
                    'preserved': (workflow_module, 'finish'),
                    'prepared': (workflow_module, 'prepare'),
                    'complete-before': (self.workflow, 'save_request'),
                    'complete-after': (self.workflow, 'save_request'),
                }[boundary]
                with (
                    patch.object(owner, name, side_effect=interrupt),
                    self.assertRaisesRegex(RuntimeError, 'Synthetic'),
                ):
                    self.workflow.new(finish_previous='Recorded ending', recipe_name='participant-inquiry')
                target = self.store.record('run-002')
                self.assertEqual(target['recipe']['metadata']['id'], 'participant-inquiry')
                ending = self.store.record(previous).get('ending')
                if boundary in ('reserved', 'request-before', 'request-after'):
                    self.assertIsNone(ending)
                self.store.connection().plan.path.unlink()
                (self.root / 'recipes/participant-inquiry/INSTRUCTIONS.md').unlink()
                self.load()
                if boundary in ('reserved', 'request-before'):
                    result = self.workflow.new(finish_previous='Recorded ending')
                else:
                    result = self.workflow.new()
                self.assertEqual(result['run'], 'run-002')
                self.assertEqual(result['recipe'], 'participant-inquiry')
                self.assertEqual(self.store.record('run-002')['recipe'], target['recipe'])
                if ending is not None:
                    self.assertEqual(self.store.record(previous)['ending'], ending)
                self.assertEqual([r['run'] for r in self.store.discover()], ['run-001', 'run-002'])

    def pending(self, after_ending=False):
        previous = self.first_session()
        name = 'prepare' if after_ending else 'finish'
        with patch.object(workflow_module, name, side_effect=RuntimeError('stop')), self.assertRaises(RuntimeError):
            self.workflow.new(finish_previous='Recorded ending', recipe_name='participant-inquiry')
        return previous

    def test_pending_request_conflicts_refuse_without_changing_either_operation(self):
        previous = self.pending()
        path, request = self.request()
        self.assertEqual(request['schema'], 'berman.new-request/2')
        for kwargs in ({'keep_open': True}, {'recipe_name': 'work-accounts'}, {'finish_previous': 'Different reason'}):
            with self.subTest(kwargs=kwargs):
                sources = [
                    path,
                    self.store.folder(previous) / 'operation.json',
                    self.store.folder('run-002') / 'operation.json',
                ]
                before = [p.read_bytes() for p in sources]
                with self.assertRaises(Decision):
                    self.workflow.new(**kwargs)
                self.assertEqual([p.read_bytes() for p in sources], before)

    def test_cancelled_request_retirement_survives_a_stop_before_target_retirement(self):
        for after_ending in (False, True):
            with self.subTest(after_ending=after_ending):
                self.setUp()
                previous = self.pending(after_ending)
                ending = self.store.record(previous).get('ending')
                with (
                    patch.object(self.workflow, 'retire', side_effect=RuntimeError('stop')),
                    self.assertRaises(RuntimeError),
                ):
                    self.workflow.cancel('Target withdrawn', run='run-002')
                self.assertEqual(self.request()[1]['state'], 'cancelled')
                self.load()
                with patch('os.execv') as execute, self.assertRaises(Decision):
                    self.workflow.launch('run-002')
                execute.assert_not_called()
                self.assertEqual(self.store.record('run-002')['phase'], 'cancelled')
                self.assertEqual(self.store.record(previous).get('ending'), ending)
                result = self.workflow.new(recipe_name='work-accounts', keep_open=not after_ending)
                self.assertEqual(result['run'], 'run-003')

    def test_retiring_target_does_not_rewrite_completed_request(self):
        self.first_session()
        result = self.workflow.new(finish_previous='Ended', recipe_name='participant-inquiry')
        path, _ = self.request()
        before = path.read_bytes()
        self.workflow.cancel('Target withdrawn', run=result['run'])
        self.assertEqual(path.read_bytes(), before)

    def test_ready_target_waits_for_its_request_before_launching(self):
        self.first_session()
        save = self.workflow.save_request

        def before_complete(request):
            if request['state'] == 'complete':
                raise RuntimeError('stop')
            save(request)

        with patch.object(self.workflow, 'save_request', side_effect=before_complete), self.assertRaises(RuntimeError):
            self.workflow.new(finish_previous='Ended', recipe_name='participant-inquiry')
        self.assertEqual(self.store.record('run-002')['phase'], 'ready')
        fieldwork = self.workflow.cheatsheet()['connections'][0]['fieldwork']
        self.assertEqual(fieldwork['ready'], [])
        self.assertEqual(fieldwork['incomplete'], ['run-002'])
        with patch('os.execv') as execute, self.assertRaisesRegex(Decision, 'pending combined request'):
            self.workflow.launch('run-002')
        execute.assert_not_called()
        self.workflow.new()
        with patch('os.execv') as execute:
            self.workflow.launch('run-002')
        execute.assert_called_once()

    def test_legacy_pending_request_requires_original_installation(self):
        previous = self.first_session()
        path = self.store.operations / 'new-requests/legacy.json'
        write(
            path,
            {
                'schema': 'berman.new-request/1',
                'state': 'pending',
                'connection': 'stewart',
                'previous_run': previous,
                'finish_reason': 'Ended',
                'step': 'inquiry-after-repeat',
                'result_run': None,
            },
        )
        before = path.read_bytes()
        with self.assertRaisesRegex(Decision, 'original installation'):
            self.workflow.new(recipe_name='participant-inquiry')
        self.assertEqual(path.read_bytes(), before)
        self.assertNotIn('ending', self.store.record(previous))

    def test_completed_legacy_request_is_read_without_rewriting(self):
        self.first_session()
        self.workflow.new(finish_previous='Ended', recipe_name='participant-inquiry')
        path, value = self.request()
        value.update(schema='berman.new-request/1', step='inquiry-after-repeat')
        write(path, checked_request(value))
        before = path.read_bytes()
        result = self.workflow.new(finish_previous='Ended')
        self.assertEqual(result['run'], 'run-002')
        self.assertEqual(path.read_bytes(), before)
