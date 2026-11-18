"""Schema validation where a co-located workspace's records are read: what is refused, how, and what is never touched.

Invalid data, a missing record, an incomplete operation and an unknown fact are four different
reports. Reading validates and changes nothing. The records are synthetic, written by
tests/support/legacy.py as release 0.26.3 wrote them.
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

from berman import cli, recipes, records, schemas, verify
from berman.files import Decision, json_bytes, read, write
from berman.records import Store
from tests.support import legacy
from tests.support.workspace import git_repository, workspace


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

    def status(self):
        return records.status(self.store)

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
        run = legacy.prepare(self.store)
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
        status = self.status()['runs'][0]
        self.assertEqual((status['ending_declared'], status['started']), (False, None))
        self.assertIn('unknown: registered', status['summary'])
        self.assertNotIn('not yet started', status['summary'])
        # What a damaged run awaits is unknown: it is a problem, and no reason is offered for it.
        self.assertIsNone(status['unsettled'])
        self.assertEqual(self.status()['unsettled'], [])
        self.assertEqual([r['run'] for r in self.store.discover()], [run])
        self.assertEqual(state(path), damaged, 'reading a damaged record changed it')

    def test_reading_validates_and_never_rewrites(self):
        run = legacy.prepare(self.store)
        path = self.store.folder(run) / 'operation.json'
        legacy.start(self.store, run)
        for step in ('ready', 'archived'):
            if step == 'archived':
                legacy.finish(self.store, run, 'The researcher said the encounter ended.')
            with self.subTest(phase=step):
                before = state(path)
                record = self.store.record(run)
                self.assertEqual(record['phase'], step)
                self.assertEqual(json_bytes(record), before[0], 'a record does not round-trip to its own bytes')
                self.store.discover()
                self.status()
                self.assertEqual(self.command('status')[0], 0)
                self.assertEqual(state(path), before)

    def test_a_reservation_whose_record_never_landed_is_not_a_run(self):
        # The writer made the folder before the record's atomic write; a full disk, or a stop inside the
        # write, left it empty or with a temporary file.
        folder = self.store.operations / 'run-001'
        folder.mkdir(parents=True)
        for debris in ((), ('.capture-interrupted',)):
            with self.subTest(debris=debris):
                for name in debris:
                    (folder / name).write_bytes(b'{"schema": "berman.oper')
                self.assertEqual(self.store.discover(), [])
                status = self.status()
                self.assertEqual((status['runs'], status['workspace_problems'], status['unsettled']), ([], [], []))
        shutil.rmtree(folder)
        # Anything else in such a folder is still a run that needs attention, not debris to pass over.
        (self.store.operations / 'run-007').mkdir()
        (self.store.operations / 'run-007/notes.txt').write_text('Kept by an operator.\n')
        row = self.store.discover()[0]
        self.assertEqual(row['run'], 'run-007')
        self.assertIn('run-007 is recorded but its packet is missing', row['problems'][0])

    def test_a_record_holding_text_no_stream_can_encode_is_still_reported(self):
        # Found in review: the refusal quoted the offending value, a lone surrogate made that message
        # unprintable, and one such record took plain status down with exit 2 and no output.
        run = legacy.prepare(self.store)
        path = self.store.folder(run) / 'operation.json'
        path.write_text(json.dumps(dict(self.store.record(run), documents='\ud83d')))
        code, out, err = self.command('status')
        self.assertEqual((code, err), (0, ''))
        out.encode('utf-8')
        self.assertIn(f'{run} (stewart): starting state unknown', out)
        self.assertIn('documents must be an array or null, not a string ("\\ud83d")', out)

    def test_a_pending_combined_request_is_reported_and_never_resumed(self):
        path = self.store.operations / 'new-requests/fixture.json'
        request = {
            'schema': 'berman.new-request/2',
            'state': 'pending',
            'connection': 'stewart',
            'previous_run': 'run-001',
            'finish_reason': 'ended',
            'result_run': 'run-002',
        }
        write(path, request)
        before = state(path)
        status = self.status()
        self.assertEqual([row['path'] for row in status['pending_requests']], [str(path)])
        self.assertEqual(len(status['unsettled']), 1)
        self.assertIn('a combined request to end run-001 and prepare run-002 never completed', status['unsettled'][0])
        self.assertIn('It has to be closed with the release that opened it', status['unsettled'][0])
        self.assertEqual(self.store.discover(), [], 'reading a request made a run')
        # A completed or cancelled request awaits nothing; one that cannot be read is a problem, not a guess.
        for settled in ('complete', 'cancelled'):
            write(path, dict(request, state=settled))
            self.assertEqual((self.status()['pending_requests'], self.status()['unsettled']), ([], []))
        write(path, dict(request, state='paused'))
        status = self.status()
        self.assertEqual(status['unsettled'], [])
        self.assertIn(f"Combined request {path} cannot be read: unknown state 'paused'", status['workspace_problems'])
        write(path, request)
        self.assertEqual(state(path)[0], before[0])


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
                        _ = loaded.connections['stewart'].plan
                    else:
                        Store(self.root)
                self.assertEqual(state(path), changed)
            path.write_bytes(original)
        Store(self.root)

    def test_inputs_that_version_0_4_worked_with_still_work(self):
        # Found in review: a first set of schemas refused these. Each is a value 0.4 converted, read as
        # absent or never read, and with each of them the workspace loaded. It still does.
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
            path.write_bytes(original)

    def test_a_connection_with_no_fieldwork_fields_still_loads(self):
        project = self.base / 'tools'
        git_repository(project, {'README.md': 'Tools\n'})
        write(self.root / 'connections/tools/connection.json', {'schema': 'berman.connection/1', 'id': 'tools'})
        value = read(self.root / 'berman.json')
        value['connections']['tools'] = {
            'definition': 'connections/tools/connection.json',
            'checkout': str(project),
            'development': {'codex': str(self.base / 'tools-dev')},
        }
        write(self.root / 'berman.json', value)
        self.load()
        self.assertEqual(self.command('status')[0], 0)
        self.assertEqual(self.status()['connections']['tools']['plan'], None)

    def test_text_the_opening_message_no_longer_carried_is_refused_in_a_recipe_and_still_read(self):
        connection, recipe = 'connections/stewart/connection.json', 'recipes/work-accounts/recipe.json'
        originals = {name: (self.root / name).read_bytes() for name in (connection, recipe)}
        for relative, changes, message in (
            # A connection is only read here; what it once said to a fieldworker commissions nothing.
            (connection, {'commission': 'COMMISSION.md'}, None),
            (connection, {'revision': 'Stewart at {head}'}, None),
            (recipe, {'research_commission': 'COMMISSION.md'}, 'Recipe work-accounts names research_commission, which'),
            (recipe, {'commission': 'COMMISSION.md'}, 'Recipe work-accounts names commission, which no longer reaches'),
        ):
            path = self.edit(relative, **changes)
            self.load()
            with self.subTest(changes=changes):
                self.assertEqual(self.command('status')[0], 0, 'reading must still work')
                if message:
                    with self.assertRaisesRegex(Decision, message) as caught:
                        recipes.recipe(self.store, 'work-accounts', self.store.connections['stewart'])
                    self.assertIn(str(path), str(caught.exception))
                self.assertEqual(self.store.discover(), [], 'reading an input made a run')
            path.write_bytes(originals[relative])
        self.load()
        self.assertEqual(
            recipes.recipe(self.store, 'work-accounts', self.store.connections['stewart'])['metadata']['id'],
            'work-accounts',
        )

    def test_a_recipe_is_refused_when_selected_and_earlier_wording_is_kept(self):
        relative = 'recipes/work-accounts/recipe.json'
        path, original = self.root / relative, (self.root / relative).read_bytes()

        def selected():
            return recipes.recipe(self.store, 'work-accounts', self.store.connections['stewart'])

        self.edit(relative, instructions=...)
        with self.assertRaisesRegex(
            schemas.InvalidRecord, f'{path} does not satisfy berman.recipe/2: instructions is required'
        ):
            selected()
        # Rules berman.recipes already stated keep their own words.
        for changes, message in (
            ({'recording_protocol': 'diary/9'}, 'Recipe must explicitly name a supported recording protocol'),
            ({'coverage': ['notes', 'scratch']}, 'This workflow requires a named notes recipe'),
            ({'connections': ['another']}, 'is worded for another, not stewart'),
        ):
            path.write_bytes(original)
            self.edit(relative, **changes)
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, message):
                selected()
        path.write_bytes(original)
        self.assertEqual(selected()['metadata']['id'], 'work-accounts')
        self.assertEqual(self.store.discover(), [], 'selecting a recipe made a run')


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


if __name__ == '__main__':
    unittest.main()
