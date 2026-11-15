"""Schema validation where records are read and written: what is refused, how, and what is never touched.

Invalid data, a missing record, an incomplete operation and an unknown fact are four different
reports. Reading validates and changes nothing; an invalid record is never written.
"""

import contextlib
import builtins
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

from berman import cli, files, preserve, schemas, verify
from berman.files import Decision, json_bytes, read, write
from berman.recipes import prepare
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
            'documents must be an array or null, not a string ("README.md")',
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
            ({'packet': 12}, 'packet must be a string or null, not an integer \\(12\\)'),
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

    def test_a_record_holding_text_no_stream_can_encode_is_still_reported(self):
        # Found in review: the refusal quoted the offending value, a lone surrogate made that message
        # unprintable, and one such record took plain status down with exit 2 and no output.
        run = self.workflow.new()['run']
        path = self.store.folder(run) / 'operation.json'
        path.write_text(json.dumps(dict(self.store.record(run), documents='\ud83d')))
        code, out, err = self.command('status')
        self.assertEqual((code, err), (0, ''))
        out.encode('utf-8')
        self.assertIn(f'{run} (stewart): starting state unknown', out)
        self.assertIn('documents must be an array or null, not a string ("\\ud83d")', out)

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
            'previous_preserved must be an object or null, not an array',
        )
        self.assertEqual(state(path), before)
        self.assertEqual(self.store.discover(), [], 'a refused request allocated a run')


class ResearcherInputTests(RecordTests):
    def test_broken_jsonschema_initialization_refuses_records_without_a_traceback(self):
        original = builtins.__import__

        def broken(name, *args, **kwargs):
            if name == 'jsonschema':
                raise RuntimeError('synthetic damaged dependency')
            return original(name, *args, **kwargs)

        path = self.root / 'berman.json'
        before = state(path)
        schemas.validator.cache_clear()
        self.addCleanup(schemas.validator.cache_clear)
        with mock.patch('builtins.__import__', side_effect=broken):
            code, out, err = self.command('status')
            self.assertEqual((code, out), (2, ''))
            self.assertIn('Records cannot be checked: jsonschema does not import', err)
            self.assertIn('synthetic damaged dependency', err)
            self.assertNotIn('Traceback', err)
            code, out, err = self.command('--json', 'verify', '--skip', 'archive')
            self.assertEqual((code, err), (1, ''))
            self.assertEqual(json.loads(out)['checks']['workspace']['status'], 'failed')
        self.assertEqual(state(path), before)

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
        connection, plan = 'connections/stewart/connection.json', 'plans/stewart-fieldwork-roles/plan.json'
        cases = (
            # A list given as text would be read character by character without complaint.
            (connection, {'documents': 'README.md'}, 'berman.connection/1', 'documents must be an array'),
            (connection, {'readers': 'stewart-historical'}, 'berman.connection/1', 'readers must be an array'),
            (
                connection,
                {'checks': [{'id': 'x', 'argv': 'true'}]},
                'berman.connection/1',
                r'checks\[0\].argv must be an array',
            ),
            (connection, {'checks': {'x': ['true']}}, 'berman.connection/1', 'checks must be an array'),
            # What loading reads and used to fail on with a bare key name or a traceback.
            (
                'connections/catalogue.json',
                {'runs': [{'connection': 'stewart'}]},
                'berman.run-catalogue/1',
                r'runs\[0\].run is required',
            ),
            (plan, {'steps': [{'recipe': 'work-accounts'}]}, 'berman.research-plan/1', r'steps\[0\].id is required'),
            (plan, {'steps': ...}, 'berman.research-plan/1', 'steps is required'),
        )
        for relative, changes, name, message in cases:
            path = self.root / relative
            original = path.read_bytes()
            self.edit(relative, **changes)
            with self.subTest(file=relative, changes=changes):
                # A selected plan is read only when used; other inputs remain eager.
                loaded = Store(self.root) if relative == plan else None
                changed = state(path)
                with self.assertRaisesRegex(schemas.InvalidRecord, f'{path} does not satisfy {name}: {message}'):
                    if loaded is not None:
                        _ = loaded.connection('stewart').plan
                    else:
                        Store(self.root)
                self.assertEqual(state(path), changed)
            path.write_bytes(original)
        Store(self.root)

    def test_inputs_that_version_0_4_worked_with_still_work(self):
        # Found in review: a first set of schemas refused these. Each is a value 0.4 converted, read as
        # absent or never read, and with each of them status and preparation ran. They still do.
        connection, plan = 'connections/stewart/connection.json', 'plans/stewart-fieldwork-roles/plan.json'
        checks = read(self.root / connection)['checks']
        tolerated = {
            'a timeout given as text': (connection, {'checks': [dict(checks[0], timeout='45'), checks[1]]}),
            'a fractional timeout': (connection, {'checks': [dict(checks[0], timeout=45.5), checks[1]]}),
            'expected output given as a number': (
                connection,
                {'checks': [checks[0], dict(checks[1], stdout_contains=3)]},
            ),
            'an argument given as true': (
                connection,
                {'checks': [checks[0], dict(checks[1], argv=[*checks[1]['argv'], True])]},
            ),
            'a fractional connection version': (connection, {'version': 1.5}),
            'a description kept as a list': (connection, {'description': ['two', 'lines']}),
            'a connection name of null': (connection, {'name': None}),
            'no development launchers, as null': ('berman.json', 'development', None),
            'a launcher left as null': ('berman.json', 'development', {'codex': None}),
            'a fieldwork model that Codex ignores': ('berman.json', 'fieldwork_model', 5),
            'numbered plan steps': (
                plan,
                {'steps': [{'id': 1, 'recipe': 'work-accounts'}, {'id': 2, 'recipe': 'participant-inquiry'}]},
            ),
            'boolean plan steps': (
                plan,
                {'steps': [{'id': True, 'recipe': 'work-accounts'}, {'id': False, 'recipe': 'participant-inquiry'}]},
            ),
            'a later step with no recipe yet': (
                plan,
                {'steps': [{'id': 'first', 'recipe': 'work-accounts'}, {'id': 'later'}]},
            ),
            'a commitment kept as an object': (plan, {'commitment': {'text': 'Disposable.'}}),
            'relationships kept as study annotations': (plan, {'relationships': [{'relationship': 'Study context.'}]}),
            'a catalogue row with no connection yet': (
                'connections/catalogue.json',
                {'runs': [{'run': 'run-001', 'connection': None}]},
            ),
            'a catalogue basis kept as an object': (
                'connections/catalogue.json',
                {'runs': [{'run': 'run-001', 'connection': 'stewart', 'basis': {}}]},
            ),
            'a recipe source kept as an object': ('recipes/work-accounts/recipe.json', {'source': {'from': 'fixture'}}),
            'an empty executable path': ('berman.json', {'codex': ''}),
            'a numeric collection identity': ('berman.json', {'collection_id': 7}),
            'no catalogue, as false': ('berman.json', {'catalogue': False}),
            'no catalogue, as zero': ('berman.json', {'catalogue': 0}),
            'no catalogue, as fractional zero': ('berman.json', {'catalogue': 0.0}),
            'no catalogue, as an empty list': ('berman.json', {'catalogue': []}),
            'no catalogue, as an empty object': ('berman.json', {'catalogue': {}}),
        }
        for name, (relative, *change) in tolerated.items():
            path = self.root / relative
            original = path.read_bytes()
            if len(change) == 2:  # A field of this workspace's entry for the connection.
                value = read(path)
                value['connections']['stewart'][change[0]] = change[1]
                write(path, value)
            else:
                self.edit(relative, **change[0])
            with self.subTest(tolerated=name):
                self.load()
                code, _, err = self.command('status')
                self.assertEqual((code, err), (0, ''))
                run = self.workflow.new()['run']
                self.assertEqual(self.store.record(run)['phase'], 'ready')
                self.workflow.cancel('Fixture only.', run)
            shutil.rmtree(self.store.folder(run))
            path.write_bytes(original)

    def test_a_connection_with_no_fieldwork_fields_serves_development(self):
        project = self.base / 'tools'
        git_repository(project, {'README.md': 'Tools\n'})
        write(self.root / 'connections/tools/connection.json', {'schema': 'berman.connection/1', 'id': 'tools'})
        launcher = self.base / 'tools-dev'
        launcher.write_text('#!/bin/sh\n')
        launcher.chmod(0o755)
        value = read(self.root / 'berman.json')
        value['connections']['tools'] = {
            'definition': 'connections/tools/connection.json',
            'checkout': str(project),
            'development': {'codex': str(launcher)},
        }
        write(self.root / 'berman.json', value)
        self.load()
        self.assertEqual(self.command('status')[0], 0)
        self.assertEqual(self.workflow.dev('codex', (), 'tools')['argv'], [str(launcher)])
        self.assertEqual(self.workflow.new()['connection'], 'stewart')  # Fieldwork elsewhere is unaffected.
        with self.assertRaisesRegex(Decision, 'No assignment selected for connection tools; choose a recipe'):
            self.workflow.new(connection='tools')

    def test_text_the_opening_message_no_longer_carries_is_refused_not_ignored(self):
        connection, recipe = 'connections/stewart/connection.json', 'recipes/work-accounts/recipe.json'
        originals = {name: (self.root / name).read_bytes() for name in (connection, recipe)}
        for relative, changes, message in (
            (
                connection,
                {'commission': 'COMMISSION.md'},
                'Connection stewart names commission, which no longer reaches',
            ),
            (
                connection,
                {'revision': 'Stewart at {head}'},
                'Connection stewart names revision, which no longer reaches',
            ),
            (connection, {'commission': 'COMMISSION.md', 'revision': 'x {head}'}, 'names commission and revision,'),
            (recipe, {'research_commission': 'COMMISSION.md'}, 'Recipe work-accounts names research_commission, which'),
            (recipe, {'commission': 'COMMISSION.md'}, 'Recipe work-accounts names commission, which no longer reaches'),
        ):
            path = self.edit(relative, **changes)
            self.load()
            with self.subTest(changes=changes):
                self.assertEqual(self.command('status')[0], 0, 'reading must still work')
                with self.assertRaisesRegex(Decision, message) as caught:
                    self.workflow.new()
                self.assertIn(str(path), str(caught.exception))
                self.assertEqual(self.store.discover(), [], 'a refused input allocated a run')
            path.write_bytes(originals[relative])
        self.load()
        self.assertEqual(self.workflow.new()['run'], 'run-001')

    def test_an_unprepared_reservation_that_carried_a_commission_is_refused(self):
        run = self.workflow.new()['run']
        record = self.store.record(run)
        shutil.rmtree(self.store.field / run)
        record.update(phase='reserved', commission='Discuss the work with me.')
        self.store.save(record)
        with self.assertRaisesRegex(
            Decision, f'{run} was reserved by an earlier version .* cancel this unused preparation'
        ):
            prepare(self.store, self.store.record(run))
        self.assertFalse((self.store.field / run).exists())

    def test_a_recipe_is_refused_when_selected_and_earlier_wording_is_kept(self):
        relative = 'recipes/work-accounts/recipe.json'
        path, original = self.root / relative, (self.root / relative).read_bytes()
        self.edit(relative, instructions=...)
        with self.assertRaisesRegex(
            schemas.InvalidRecord, f'{path} does not satisfy berman.recipe/2: instructions is required'
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
