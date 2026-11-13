"""Schema validation where records are read and written: what is refused, how, and what is never touched.

Invalid data, a missing record, an incomplete operation and an unknown fact are four different
reports. Reading validates and changes nothing; an invalid record is never written.
"""

import contextlib
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

from berman import cli, files, preserve, schemas, verify
from berman.files import Decision, json_bytes, read, write
from berman.records import Store
from berman.workflow import Workflow
from tests.support.sessions import start
from tests.support.workspace import FixtureHarness, git_repository, workspace


def state(path):
    return path.read_bytes(), path.stat().st_mtime_ns


class RecordTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.root, self.project = self.base / 'berman', self.base / 'stewart'
        (self.base / 'codex').mkdir()
        workspace(self.root, self.base, self.project, documents=['README.md'])
        git_repository(self.project, {'README.md': 'Project context\n'})
        self.load()

    def load(self):
        self.store = Store(self.root)
        self.workflow = Workflow(self.store, FixtureHarness(self.store))

    def edit(self, relative, **changes):
        """Rewrite one JSON input with fields replaced, or removed when given as ...; returns its path."""
        path = self.root / relative
        value = read(path)
        for key, replacement in changes.items():
            if replacement is ...:
                value.pop(key)
            else:
                value[key] = replacement
        write(path, value)
        return path

    def command(self, *words):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(['--root', str(self.root), *words])
        return code, out.getvalue(), err.getvalue()


class OperationRecordTests(RecordTests):
    def test_missing_incomplete_invalid_and_unknown_are_four_reports(self):
        run = self.workflow.new()['run']
        path = self.store.folder(run) / 'operation.json'
        # Incomplete: a prepared, unstarted run has a valid record, and the facts nobody recorded are False.
        row = self.store.discover()[0]
        self.assertEqual((self.store.record(run)['phase'], row['problems']), ('ready', []))
        self.assertEqual(
            [row['lifecycle'][fact]['value'] for fact in ('registered', 'ending', 'packaged')], [False] * 3
        )
        # Missing: no record is None, not an error and not an empty record.
        self.assertIsNone(self.store.record('run-002'))
        # Invalid: valid JSON that lifecycle rules accept, with a structure no workflow wrote.
        write(path, dict(self.store.record(run), documents='README.md'))
        damaged = state(path)
        with self.assertRaises(ValueError) as refused:
            self.store.record(run)
        self.assertEqual(
            str(refused.exception),
            f'Invalid operation record: {path}: does not satisfy berman.operation/1: '
            'documents must be an array, not a string ("README.md")',
        )
        # Unknown: discovery reports the problem, and every fact that record held is None with a reason.
        row = self.store.discover()[0]
        self.assertEqual(row['problems'], [str(refused.exception)])
        self.assertIsNone(row['started'])
        for fact in ('registered', 'ending', 'packaged'):
            with self.subTest(fact=fact):
                self.assertIsNone(row['lifecycle'][fact]['value'])
                self.assertIn('documents must be an array', ' '.join(row['lifecycle'][fact]['unknown']))
        status = self.workflow.status()['runs'][0]
        self.assertEqual((status['ending_declared'], status['prepared_unstarted']), (False, False))
        self.assertIn('unknown: registered', status['summary'])
        self.assertNotIn('not yet started', status['summary'])
        # The damaged run blocks its connection: no second identity is allocated around it.
        with self.assertRaisesRegex(Decision, 'documents must be an array'):
            self.workflow.new()
        self.assertEqual([r['run'] for r in self.store.discover()], [run])
        self.assertEqual(state(path), damaged, 'reading a damaged record changed it')

    def test_reading_validates_and_never_rewrites(self):
        run = self.workflow.new()['run']
        path = self.store.folder(run) / 'operation.json'
        start(self.store, run)
        self.workflow.synchronize(self.store.discover())
        for step in ('active', 'archived'):
            if step == 'archived':
                self.workflow.finish('The researcher said the encounter ended.', run)
            with self.subTest(phase=step):
                before = state(path)
                record = self.store.record(run)
                self.assertEqual(record['phase'], step)
                self.assertEqual(json_bytes(record), before[0], 'a record does not round-trip to its own bytes')
                self.store.discover()
                self.workflow.status()
                self.workflow.cheatsheet()
                self.assertEqual(self.command('status')[0], 0)
                self.assertEqual(state(path), before)

    def test_an_invalid_record_is_never_written(self):
        run = self.workflow.new()['run']
        path = self.store.folder(run) / 'operation.json'
        before = state(path)
        for change, message in (
            ({'packet': 12}, 'packet must be a string, not an integer \\(12\\)'),
            ({'harness': 'codex'}, 'harness must be an object'),
            ({'phase': 'paused'}, "unknown phase 'paused'"),  # Lifecycle wording, unchanged.
        ):
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, message):
                self.store.save(dict(self.store.record(run), **change))
            self.assertEqual(state(path), before)

    def test_a_reservation_whose_record_never_landed_reserves_nothing(self):
        # Found by tests/properties/test_workflow_sequences.py. The folder is made before the record's
        # atomic write; a full disk, or a stop inside the write, leaves it empty or with a temporary file.
        for debris in ((), ('.capture-interrupted',)):
            with self.subTest(debris=debris):
                with mock.patch.object(files, 'atomic', side_effect=OSError('No space left on device')):
                    with self.assertRaisesRegex(OSError, 'No space left'):
                        self.workflow.new()
                folder = self.store.operations / 'run-001'
                self.assertEqual(list(folder.iterdir()), [])
                for name in debris:
                    (folder / name).write_bytes(b'{"schema": "berman.oper')
                self.assertEqual(self.store.discover(), [])
                status = self.workflow.status()
                self.assertEqual((status['runs'], status['workspace_problems']), ([], []))
                self.assertEqual(self.workflow.new()['run'], 'run-001')  # The retry reserves the same identity.
                self.workflow.cancel('Fixture only.', 'run-001')
                shutil.rmtree(folder)
        # Anything else in such a folder is still a run that needs attention, not debris to pass over.
        (self.store.operations / 'run-007').mkdir()
        (self.store.operations / 'run-007/notes.txt').write_text('Kept by an operator.\n')
        row = self.store.discover()[0]
        self.assertEqual(row['run'], 'run-007')
        self.assertIn('run-007 is recorded but its packet is missing', row['problems'][0])

    def test_an_interrupted_finish_is_retried_by_name_while_another_engagement_is_open(self):
        # Found by tests/properties/test_workflow_sequences.py: with another engagement open, the
        # retry was refused with "Expected one open engagement; found none".
        first = self.workflow.new()['run']
        start(self.store, first)
        second = self.workflow.new(keep_open=True)['run']
        start(self.store, second)
        with mock.patch.object(preserve, 'package', side_effect=OSError('interrupted before packaging')):
            with self.assertRaisesRegex(OSError, 'interrupted before packaging'):
                self.workflow.finish('The second encounter ended.', second)
        declared_at = self.store.record(second)['ending']['declared_at_utc']
        self.assertTrue((self.root / 'runs' / second).is_dir())
        self.assertEqual(self.store.record(second)['phase'], 'closing')
        retried = self.workflow.finish('The second encounter ended.', second)
        self.assertEqual((retried['run'], retried['state']), (second, 'archived'))
        self.assertEqual(self.store.record(second)['ending']['declared_at_utc'], declared_at)
        # Asked again, with other words, it is answered and nothing is declared twice.
        again = self.workflow.finish('Said differently.', second)
        self.assertEqual((again['state'], again['package']), ('already-archived', retried['package']))
        self.assertEqual(self.store.record(second)['ending']['reason'], 'The second encounter ended.')
        # The other engagement is untouched, and an unnamed finish still selects it.
        self.assertNotIn('ending', self.store.record(first))
        self.assertEqual(self.workflow.finish('The first encounter ended.')['run'], first)

    def test_a_combined_request_is_validated_when_read(self):
        path = self.store.operations / 'new-requests/fixture.json'
        request = {
            'schema': 'berman.new-request/1',
            'state': 'pending',
            'previous_run': 'run-001',
            'finish_reason': 'ended',
            'step': 'repeat-after-008',
            'result_run': None,
            'previous_preserved': [],
        }
        write(path, request)
        before = state(path)
        with self.assertRaises(Decision) as refused:
            self.workflow.new()
        self.assertEqual(
            str(refused.exception),
            f'Invalid new-run request: {path}: does not satisfy berman.new-request/1: '
            'previous_preserved must be an object, not an array',
        )
        self.assertEqual(state(path), before)
        self.assertEqual(self.store.discover(), [], 'a refused request allocated a run')


class ResearcherInputTests(RecordTests):
    def test_a_malformed_workspace_file_is_reported_with_its_place(self):
        path = self.edit('berman.json', packet_root=..., connections={'stewart': {'checkout': 12}})
        before = state(path)
        expected = (
            f'{path} does not satisfy berman.workspace/1: connections.stewart.checkout must be a string, '
            'not an integer (12); connections.stewart.definition is required; packet_root is required'
        )
        with self.assertRaises(schemas.InvalidRecord) as refused:
            Store(self.root)
        self.assertEqual(str(refused.exception), expected)
        # The command line reports it; nothing reaches the operator as a traceback.
        code, out, err = self.command('status')
        self.assertEqual((code, out), (2, ''))
        self.assertEqual(err, f'Berman needs attention: {expected}\n')
        code, out, err = self.command('--json', 'status')
        self.assertEqual((code, out, json.loads(err)), (2, '', {'state': 'needs-attention', 'message': expected}))
        # Verification reports it as a failed check and still runs the others.
        result = verify.verify(self.root, skip=('archive',))
        self.assertEqual(result['checks']['workspace']['status'], 'failed')
        self.assertIn(expected, result['checks']['workspace']['summary'])
        self.assertEqual(result['checks']['frozen-collector']['status'], 'passed')
        self.assertEqual(state(path), before)

    def test_a_workspace_file_that_is_not_an_object_is_refused_not_crashed_on(self):
        for value in ([], 'berman.workspace/1', None, 7):
            (self.root / 'berman.json').write_text(json.dumps(value))
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'Unsupported berman.json schema'):
                Store(self.root)
            self.assertEqual(self.command('status')[0], 2)

    def test_each_researcher_input_is_refused_at_its_own_file(self):
        cases = (
            (
                'connections/stewart/connection.json',
                {'documents': 'README.md'},
                'berman.connection/1',
                'documents must be an array',
            ),
            (
                'connections/stewart/connection.json',
                {'checks': [{'id': 'x'}]},
                'berman.connection/1',
                r'checks\[0\].argv is required',
            ),
            (
                'connections/catalogue.json',
                {'runs': [{'run': 'run-001'}]},
                'berman.run-catalogue/1',
                r'runs\[0\].connection is required',
            ),
            (
                'plans/stewart-fieldwork-roles/plan.json',
                {'steps': [{'id': 'a'}]},
                'berman.research-plan/1',
                r'steps\[0\].recipe is required',
            ),
            ('plans/stewart-fieldwork-roles/plan.json', {'steps': ...}, 'berman.research-plan/1', 'steps is required'),
        )
        for relative, changes, name, message in cases:
            path = self.root / relative
            original = path.read_bytes()
            self.edit(relative, **changes)
            with self.subTest(file=relative, changes=changes):
                with self.assertRaisesRegex(schemas.InvalidRecord, f'{path} does not satisfy {name}: {message}'):
                    Store(self.root)
            path.write_bytes(original)
        Store(self.root)

    def test_a_recipe_is_refused_when_selected_and_earlier_wording_is_kept(self):
        relative = 'recipes/work-accounts/recipe.json'
        path, original = self.root / relative, (self.root / relative).read_bytes()
        self.edit(relative, research_commission=...)
        with self.assertRaisesRegex(
            schemas.InvalidRecord, f'{path} does not satisfy berman.recipe/2: research_commission is required'
        ):
            self.workflow.new()
        self.assertEqual(self.store.discover(), [], 'a refused recipe allocated a run')
        # Rules berman.recipes already stated keep their own words.
        for changes, message in (
            ({'recording_protocol': 'diary/9'}, 'Recipe must explicitly name a supported recording protocol'),
            ({'coverage': ['notes', 'scratch']}, 'This workflow requires a named notes recipe'),
            ({'connections': []}, 'is worded for no connection, not stewart'),
        ):
            path.write_bytes(original)
            self.edit(relative, **changes)
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, message):
                self.workflow.new()
        path.write_bytes(original)
        self.assertEqual(self.workflow.new()['run'], 'run-001')


class OutputTests(RecordTests):
    def test_verification_results_satisfy_their_schema(self):
        software = verify.software()
        self.assertEqual(schemas.problems('berman.verification/1', software), [])
        self.assertEqual(set(software['checks']), {'installation', 'dependencies', 'frozen-collector', 'schemas'})
        self.assertEqual(software['verdict'], 'passed')
        workspace_result = verify.verify(self.root, skip=('archive',), record='experiments/check')
        self.assertEqual(schemas.problems('berman.verification/1', workspace_result), [])
        kept = read(self.root / 'experiments/check/verification.json')
        self.assertEqual(schemas.problems('berman.verification/1', kept), [])

    def test_initialization_writes_records_that_satisfy_their_schemas(self):
        code, out, _ = self.command(
            'init', str(self.base / 'study'), '--project', str(self.project), '--connection', 'orchard'
        )
        self.assertEqual(code, 0)
        study = self.base / 'study'
        for relative, name in (
            ('berman.json', 'berman.workspace/1'),
            ('connections/orchard/connection.json', 'berman.connection/1'),
            ('connections/catalogue.json', 'berman.run-catalogue/1'),
            ('software.json', 'berman.software/1'),
        ):
            with self.subTest(file=relative):
                self.assertEqual(schemas.problems(name, read(study / relative)), [])
        self.assertEqual(json.loads(out)['software'], read(study / 'software.json'))


if __name__ == '__main__':
    unittest.main()
