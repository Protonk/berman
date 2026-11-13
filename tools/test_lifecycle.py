"""Lifecycle facts: each stage is reported separately, with its evidence or the reason it is unknown."""
import contextlib
import io
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_connections import Base
from berman_files import Decision, json_bytes, read, write
from berman_legacy import MARKER
import berman_capture
import berman_cli
import berman_collector
import berman_legacy
import berman_lifecycle as lifecycle
import berman_preserve
from berman_records import closed_by
import fieldwork

INFERRED = {'reason': 'Existing explicit finalization or migration authorizes completing preservation',
            'basis': lifecycle.INFERRED_ENDING}
DECLARED = {'declared_at_utc': '2026-10-24T13:45:31.459668Z', 'reason': 'The researcher states the run is closed.',
            'allow_incomplete': None, 'meaning': lifecycle.ENDING_MEANING}
# The shape of the real run-008 record (adopted after preparation, declared, migrated and packaged).
RUN_008_SHAPE = {'schema': 'berman.operation/1', 'run': 'run-008', 'phase': 'archived', 'project': '/fixture/stewart',
                 'documents': ['README.md'], 'adopted_existing_run_at_utc': '2026-10-24T13:45:31.459562Z',
                 'started_session': '01a16bb4-eff6-7cb3-b3e7-cb79077117f6', 'ending': DECLARED,
                 'package': {'path': '/fixture/archive/recordings/run-008.zip', 'sha256': '0' * 64, 'bytes': 1,
                             'members': 1, 'namespace': 'stewart-analysis/'}}
# The status keys that existed before lifecycle facts; they keep their meanings.
STATUS_KEYS = {'run', 'connection', 'state', 'phase', 'session', 'ending_declared', 'started', 'prepared_unstarted',
               'record', 'capture_consistent', 'last_capture', 'warnings', 'problems', 'operation_in_progress'}


def evidence(**values):
    location = values.get('location', 'active')
    base = {'location': 'active', 'placed': None if location == 'problem' else location, 'record_error': None,
            'binding': None, 'registrations': [], 'replacements': [], 'closed_by': [], 'session_material': False,
            'capture': None, 'journal': None, 'indexed': False, 'problems': []}
    return dict(base, **values)


def record(phase='ready', **values):
    return dict({'schema': 'berman.operation/1', 'run': 'run-001', 'phase': phase}, **values)


def row(location='active', registered=False, value=None, migration_pending=False, problems=()):
    return {'run': 'run-001', 'location': location, 'record': value, 'migration_pending': migration_pending,
            'problems': list(problems), 'lifecycle': {'registered': lifecycle.fact(registered, unknown=['fixture'])}}


class FactTests(unittest.TestCase):
    def test_inferred_ending_basis_is_an_interpretation_not_a_declaration(self):
        for ending in (INFERRED, dict(INFERRED, declared_at_utc='2026-10-23T00:00:00Z')):
            with self.subTest(ending=ending):
                value = record('closing', ending=ending, migration_authority={'journal': 'fixture'})
                self.assertFalse(lifecycle.declared(value))
                fact = lifecycle.assess(value, evidence())['ending']
                self.assertIs(fact['value'], False)
                self.assertIn('earlier inferred interpretation retained; not a declaration', fact['evidence'])
        retained = record('closing', ending=DECLARED, previous_ending_interpretations=[INFERRED])
        self.assertTrue(lifecycle.declared(retained))
        self.assertIs(lifecycle.assess(retained, evidence())['ending']['value'], True)
        self.assertIs(lifecycle.assess(record(), evidence())['ending']['value'], False)

    def test_declaration_matches_previous_bytes(self):
        inline = {'declared_at_utc': '2026-10-24T00:00:00Z', 'reason': 'Ended', 'allow_incomplete': None,
                  'meaning': 'Operator declares this engagement ended; not a claim that its development or inquiry is complete.'}
        self.assertEqual(json_bytes(lifecycle.declaration('Ended', None, '2026-10-24T00:00:00Z')), json_bytes(inline))
        self.assertEqual(list(lifecycle.declaration('Ended', 'boundary', 'now')),
                         ['declared_at_utc', 'reason', 'allow_incomplete', 'meaning'])

    def test_record_validation_accepts_written_shapes_and_rejects_others(self):
        accepted = [record(phase) for phase in ('reserved', 'prepared', 'ready')]
        accepted += [record('launching', launch_attempts=[{'requested_at_utc': 'now'}]),
                     record('active', started_session='fixture-session'),
                     record('closing', ending=DECLARED), record('closing', migration_authority={'journal': 'fixture'}),
                     record('closing', ending=INFERRED, migration_authority={'journal': 'fixture'}),
                     record('archived', package={'path': '/fixture.zip'}, ending=dict(DECLARED, allow_incomplete='boundary')),
                     # d1f71cd stored --allow-incomplete verbatim, including a blank value.
                     record('closing', ending=dict(DECLARED, allow_incomplete='')),
                     record('closing', ending=dict(DECLARED, allow_incomplete='  ')),
                     record('cancelling', cancellation={'reason': 'unwanted'}),
                     record('cancelled', cancellation={'reason': 'unwanted'}),
                     record('ready', retained_open_runs=['run-002'], started_session=None)]
        for value in accepted:
            with self.subTest(phase=value['phase']):
                self.assertIs(lifecycle.validate_record(value, 'run-001'), value)
        self.assertIs(lifecycle.validate_record(RUN_008_SHAPE, 'run-008'), RUN_008_SHAPE)
        refused = {'unknown phase': record('bogus'),
                   'phase closing needs ending or migration_authority': record('closing'),
                   'phase archived needs package': record('archived'),
                   'phase cancelled needs cancellation': record('cancelled'),
                   'phase launching needs launch_attempts': record('launching', launch_attempts=[]),
                   'phase active needs started_session': record('active'),
                   'ending needs a reason': record('closing', ending=dict(DECLARED, reason=' ')),
                   'a declared ending needs declared_at_utc': record('closing', ending={'reason': 'Ended'}),
                   'allow_incomplete must be absent, null or text': record('closing', ending=dict(DECLARED, allow_incomplete=True)),
                   'started_session is not a session identity': record('active', started_session='../escape'),
                   'retained_open_runs must list run identities': record(retained_open_runs=['run-1']),
                   'package needs a path': record('archived', package=True),
                   'unknown record purpose': record(purpose='unrecognised')}
        for message, value in refused.items():
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                lifecycle.validate_record(value, 'run-001')
        for value, run in ((record(), 'run-002'), (dict(record(), schema='other'), 'run-001'), ([], 'run-001')):
            with self.assertRaisesRegex(ValueError, 'not a berman.operation/1 record'):
                lifecycle.validate_record(value, run)

    def test_request_and_journal_validation(self):
        request = {'schema': 'berman.new-request/1', 'state': 'pending', 'connection': 'stewart', 'created_at_utc': 'now',
                   'previous_run': 'run-001', 'finish_reason': 'Concluded', 'step': 'repeat', 'result_run': None}
        self.assertIs(lifecycle.validate_request(request), request)
        lifecycle.validate_request(dict(request, state='complete', result_run='run-002'))
        lifecycle.validate_request({k: v for k, v in request.items() if k not in ('connection', 'result_run')})
        for change, message in (({'state': 'done'}, 'unknown state'), ({'previous_run': 'run-1'}, 'previous_run'),
                                ({'result_run': 'elsewhere'}, 'result_run'), ({'finish_reason': ''}, 'finish_reason'),
                                ({'step': None}, 'step'), ({'schema': 'other'}, 'not a berman.new-request/1')):
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, message):
                lifecycle.validate_request(dict(request, **change))
        # run-006's journal names the pre-rename root; destinations are not compared.
        journal = {'schema': 'stewart.migration/1', 'source': '/fieldwork/run-006',
                   'destination': '/historical/study/runs/run-006',
                   'phase': 'indexed', 'entry': {'run': 'run-006'}}
        self.assertEqual(lifecycle.journal_phase(journal, 'run-006'), 'indexed')
        self.assertEqual(lifecycle.journal_phase(dict(journal, run='run-006', phase='inventoried'), 'run-006'), 'inventoried')
        for value, message in ((dict(journal, entry={'run': 'run-007'}), 'another run'), (dict(journal, run='run-007'), 'another run'),
                               (dict(journal, entry=None), 'another run'), (dict(journal, phase='moved'), 'unknown migration phase'),
                               (dict(journal, schema='other'), 'not a stewart.migration/1'), ([], 'not a stewart.migration/1')):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, message):
                lifecycle.journal_phase(value, 'run-006')

    def test_archived_run_without_record_leaves_ending_and_package_unknown(self):
        facts = lifecycle.assess(None, evidence(location='archived', indexed=True, closed_by=['finalized.json'],
                                                binding={'run': 'run-004', 'session_id': 'old'}))
        self.assertIs(facts['registered']['value'], True)
        self.assertEqual((facts['capture']['value'], facts['finalized']['value']), ('closed', True))
        self.assertEqual(facts['migration']['value'], 'indexed')
        self.assertIsNone(facts['ending']['value'])
        self.assertIn('preserved before Berman recorded ending declarations', facts['ending']['unknown'][0])
        self.assertIsNone(facts['packaged']['value'])
        self.assertEqual(facts['unknown'], ['ending', 'packaged'])
        self.assertEqual(facts['disagreements'], [])
        # Runs preserved before capture registration read as never registered, not unknown.
        early = lifecycle.assess(None, evidence(location='archived', indexed=True))
        self.assertEqual((early['registered']['value'], early['capture']['value']), (False, 'none'))

    def test_problem_rows_state_only_what_their_evidence_supports(self):
        problem = ['run-001: cannot inspect it']
        for placed, migration, recorded in (('active', 'none', False), ('archived', None, None), (None, None, None)):
            with self.subTest(placed=placed):
                facts = lifecycle.assess(None, evidence(location='problem', placed=placed, problems=problem))
                self.assertEqual(facts['migration']['value'], migration)
                self.assertIs(facts['ending']['value'], recorded)
                self.assertIs(facts['packaged']['value'], recorded)
                self.assertEqual(facts['unknown'], ['registered', 'capture', 'finalized']
                                 + ([] if placed == 'active' else ['ending', 'migration', 'packaged']))
        self.assertEqual(lifecycle.assess(None, evidence(location='problem', problems=problem))['migration']['unknown'], problem)
        # A readable record still says whether an ending was declared or a package made.
        moved = lifecycle.assess(record('active', started_session='s'), evidence(location='problem', placed='archived', problems=problem))
        self.assertEqual((moved['ending']['value'], moved['packaged']['value'], moved['migration']['value']), (False, False, None))
        self.assertEqual(moved['migration']['unknown'], ['archived without inventory or migration journal'])
        self.assertEqual(moved['unknown'], ['capture', 'finalized', 'migration'])
        # An indexed journal without its inventory entry is neither unmigrated nor indexed.
        journal = lifecycle.assess(None, evidence(location='problem', placed='archived', journal='indexed', problems=problem))
        self.assertIsNone(journal['migration']['value'])
        self.assertIn('source-layout.json has no entry', journal['migration']['unknown'][0])
        indexed = lifecycle.assess(RUN_008_SHAPE | {'run': 'run-001'}, evidence(location='archived', journal='indexed', indexed=True))
        self.assertEqual((indexed['migration']['value'], indexed['disagreements']), ('indexed', []))

    def test_unreadable_record_keeps_registration_evidence(self):
        error = 'Invalid operation record: /fixture/operation.json: unknown phase'
        facts = lifecycle.assess(None, evidence(record_error=error, problems=[error],
                                                binding={'run': 'run-001', 'session_id': 'fixture'}))
        self.assertIs(facts['registered']['value'], True)
        for name in ('ending', 'packaged', 'capture', 'finalized'):
            self.assertIsNone(facts[name]['value'], name)
        self.assertIn(error, facts['ending']['unknown'][0])
        unreadable = lifecycle.assess(None, evidence(record_error=error))
        self.assertIsNone(unreadable['registered']['value'])
        self.assertIn('operation record unreadable: ' + error, unreadable['registered']['unknown'])
        self.assertEqual(lifecycle.fact(None)['unknown'], ['no readable evidence'])
        self.assertNotIn('unknown', lifecycle.fact(False))

    def test_describe_never_calls_closure_an_ending(self):
        bound = {'run': 'run-001', 'session_id': 'fixture'}
        cases = {'open': evidence(binding=bound, capture={'capture': {'event': 'SessionEnd'}}),
                 'attempted': evidence(binding=bound, capture={'capture': {'event': 'manual-finalize'}}),
                 'finalized': evidence(binding=bound, closed_by=['finalized.json']),
                 'switched': evidence(binding=bound, closed_by=['ended.json'])}
        lines = {name: lifecycle.describe(lifecycle.assess(record('active', started_session='fixture'), ev), 'active')
                 for name, ev in cases.items()}
        self.assertEqual(lines['open'], 'session registered; capture open after SessionEnd; no engagement ending declared')
        self.assertEqual(lines['attempted'], 'session registered; capture open after a finalization attempt that did not '
                         'freeze it; no engagement ending declared')
        self.assertIn('finalized.json (frozen; not an ending)', lines['finalized'])
        self.assertIn('ended.json (registration moved to another run; not an ending)', lines['switched'])
        for line in lines.values():
            self.assertIn('no engagement ending declared', line)
            self.assertNotIn('ending declared;', line.replace('no engagement ending declared', ''))
        self.assertEqual(lifecycle.describe(lifecycle.assess(record(), evidence()), 'ready'), 'ready; not yet started')
        self.assertEqual(lifecycle.describe(lifecycle.assess(record('launching', launch_attempts=[{}]), evidence()), 'launching'),
                         'launch attempted; no session registered yet')
        unknown = lifecycle.assess(record(), evidence(problems=['run-001: damaged']))
        self.assertEqual(lifecycle.describe(unknown, 'ready'),
                         'starting state unknown; no engagement ending declared; unknown: registered, capture, finalized')
        displaced = lifecycle.assess(record(), evidence(replacements=['session-history/x/replacement.json']))
        self.assertEqual(displaced['capture']['value'], 'displaced')
        self.assertIn('session displaced', lifecycle.describe(displaced, 'ready'))

    def test_disagreements_name_records_that_conflict_with_evidence(self):
        bound = {'run': 'run-001', 'session_id': 'fixture'}
        migrated = evidence(location='archived', indexed=True, binding=bound, closed_by=['finalized.json'])
        started = record('active', started_session='fixture')
        self.assertEqual(lifecycle.assess(started, migrated)['disagreements'], [lifecycle.LOW_LEVEL])
        # A migration still under way is resumed by settlement; declared or authorized ones are ordinary.
        for value, ev in ((started, dict(migrated, journal='moved-and-verified')),
                          (record('closing', ending=DECLARED), migrated),
                          (record('closing', migration_authority={'journal': 'j'}), migrated),
                          (None, migrated), (RUN_008_SHAPE | {'run': 'run-001'}, migrated),
                          (record('archived', started_session='fixture', package={'path': '/p.zip'}, ending=INFERRED), migrated)):
            with self.subTest(value=value, journal=ev['journal']):
                facts = lifecycle.assess(value, ev)
                self.assertEqual(facts['disagreements'], [])
                if (value or {}).get('package'):
                    self.assertTrue(lifecycle.describe(facts, 'archived').endswith('recording package made'))
        moved_back = lifecycle.assess(RUN_008_SHAPE | {'run': 'run-001'}, evidence(binding=bound))
        self.assertEqual(moved_back['disagreements'], ['record says archived but the packet is not under runs/'])
        self.assertEqual(lifecycle.assess(record(), evidence())['disagreements'], [])

    def test_unknown_never_counts_as_unused_or_open(self):
        for location in ('active', 'reserved'):
            self.assertTrue(lifecycle.unused(row(location, False)))
            self.assertFalse(lifecycle.unused(row(location, None)))
            self.assertFalse(lifecycle.unused(row(location, True)))
        self.assertTrue(lifecycle.open_engagement(row('active', True)))
        self.assertFalse(lifecycle.open_engagement(row('active', None)))
        self.assertFalse(lifecycle.open_engagement(row('archived', True)))
        self.assertFalse(lifecycle.unused(row('archived', False)))

    def test_authorized_matches_the_previous_settle_condition(self):
        def previous(r):
            value = r['record']
            return bool((value and value.get('phase') == 'cancelling') or (
                value and lifecycle.declared(value) and value.get('phase') != 'archived') or (
                r['migration_pending'] or (value and value.get('migration_authority') and value.get('phase') != 'archived')))
        values = [None, record(), record('cancelling', cancellation={'reason': 'x'}), record('closing', ending=DECLARED),
                  record('archived', ending=DECLARED, package={'path': 'p'}), record('closing', ending=INFERRED),
                  record('closing', migration_authority={'journal': 'j'}),
                  record('archived', migration_authority={'journal': 'j'}, package={'path': 'p'})]
        for value in values:
            for pending in (False, True):
                with self.subTest(value=value, pending=pending):
                    candidate = row('archived', True, value, pending)
                    self.assertEqual(lifecycle.authorized(candidate) is not None, previous(candidate))
        self.assertEqual(lifecycle.authorized(row(value=record('cancelling', cancellation={'reason': 'x'}))), 'retire')
        self.assertEqual(lifecycle.authorized(row(value=record('closing', ending=DECLARED))), 'preserve')
        self.assertEqual(lifecycle.authorized(row(value=record(), migration_pending=True)), 'resume-migration')

    def test_outstanding_and_progress_states(self):
        self.assertFalse(lifecycle.outstanding(row('unused', value=record('cancelled', cancellation={'reason': 'x'}))))
        self.assertTrue(lifecycle.outstanding(row('unused', value=record('cancelled', cancellation={}), problems=['x'])))
        self.assertFalse(lifecycle.outstanding(row('archived', True)))
        self.assertFalse(lifecycle.outstanding(row('archived', True, RUN_008_SHAPE)))
        self.assertTrue(lifecycle.outstanding(row('archived', True, record('active', started_session='s'))))
        self.assertTrue(lifecycle.outstanding(row('archived', True, None, migration_pending=True)))
        self.assertTrue(lifecycle.outstanding(row('reserved')))
        self.assertEqual(lifecycle.progress_state({'package': {}}, True), 'migrated; packaging pending')
        self.assertEqual(lifecycle.progress_state({'package': {'path': 'p'}}, True), 'archived')
        self.assertEqual(lifecycle.progress_state(record('closing', ending=DECLARED), False), 'ending recorded; preservation pending')
        self.assertEqual(lifecycle.progress_state(record('closing', ending=INFERRED), False), 'closing')
        self.assertEqual(lifecycle.progress_state({}, False), 'unknown')


class StatusLifecycleTests(Base):
    def status_row(self, run):
        return next(r for r in self.workflow.status()['runs'] if r['run'] == run)

    def discovered(self, run):
        return next(r for r in self.store.discover() if r['run'] == run)

    def status_text(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(berman_cli.main(['--root', str(self.root), 'status']), 0)
        return output.getvalue()

    def test_terminal_stop_and_session_end_without_declaration_is_not_ended(self):
        one = self.workflow.new()
        _, event = self.start(one['run'], self.legacy)
        self.legacy.hook(dict(event, hook_event_name='SessionEnd'))
        status = self.status_row(one['run'])
        capture = status['lifecycle']['capture']
        self.assertEqual((capture['value'], capture['last_event'], capture['closed_by']), ('open', 'SessionEnd', []))
        self.assertIs(status['lifecycle']['ending']['value'], False)
        self.assertIs(status['lifecycle']['finalized']['value'], False)
        self.assertIn('no engagement ending declared', status['summary'])
        self.assertNotIn(status['phase'], status['summary'])
        with self.assertRaisesRegex(Decision, 'no declared ending'):
            self.workflow.new()

    def test_finalized_but_undeclared_engagement_stays_open(self):
        one = self.workflow.new()
        self.start(one['run'], self.legacy)
        self.legacy.command('finalize', one['run'])
        status = self.status_row(one['run'])
        facts = status['lifecycle']
        self.assertIs(facts['finalized']['value'], True)
        self.assertEqual((facts['capture']['value'], facts['capture']['closed_by']), ('closed', ['finalized.json']))
        self.assertIs(facts['ending']['value'], False)
        self.assertTrue(lifecycle.open_engagement(self.discovered(one['run'])))
        text = self.status_text()
        self.assertIn(f'{one["run"]} (stewart): capture closed by finalized.json (frozen; not an ending); '
                      'no engagement ending declared', text)
        self.assertEqual(self.workflow.new(keep_open=True)['run'], 'run-002')

    def test_run_switch_closes_capture_without_ending(self):
        one = self.workflow.new()
        source, event = self.start(one['run'], self.legacy)
        two = self.workflow.new(keep_open=True)
        self.legacy.hook(dict(event, hook_event_name='UserPromptSubmit', prompt=MARKER + two['run'], turn_id='two'))
        status = self.status_row(one['run'])
        self.assertEqual(status['state'], 'ended')  # The collector's word, kept for compatibility.
        facts = status['lifecycle']
        self.assertEqual((facts['capture']['value'], facts['capture']['closed_by']), ('closed', ['ended.json']))
        self.assertIs(facts['ending']['value'], False)
        self.assertIs(facts['finalized']['value'], False)
        self.assertIn('ended.json (registration moved to another run; not an ending)', status['summary'])
        self.assertFalse(status['ending_declared'])

    def test_damaged_binding_and_registry_yield_unknown(self):
        one = self.workflow.new()
        _, event = self.start(one['run'], self.legacy)
        (Path(one['packet']) / 'session/binding.json').write_text('{broken binding')
        facts = self.discovered(one['run'])['lifecycle']
        self.assertIs(facts['registered']['value'], True)
        self.assertIn(f'registration stewart-fieldwork/{event["session_id"]}.json', facts['registered']['evidence'])
        self.assertEqual(facts['unknown'], ['capture', 'finalized'])
        self.legacy.registry(event['session_id']).write_text('{broken registry')
        status = self.workflow.status()
        row = next(r for r in status['runs'] if r['run'] == one['run'])
        self.assertIsNone(row['started'])
        self.assertEqual(row['unknown'], ['registered', 'capture', 'finalized'])
        self.assertIn('damaged', row['lifecycle']['registered']['unknown'][0])
        self.assertIn('starting state is unknown (', status['next_recipe'])
        self.assertIn('damaged', status['next_recipe'])
        text = self.status_text()
        self.assertIn('starting state unknown; no engagement ending declared; unknown: registered, capture, finalized', text)
        self.assertIn('  ' + row['problems'][0], text)

    def test_declared_but_not_migrated_reports_preservation_pending(self):
        one = self.workflow.new()
        self.start(one['run'], self.legacy, stop=False)
        with self.assertRaisesRegex(ValueError, 'Not finalized'):
            self.workflow.finish('Session was lost', allow_incomplete='  ')
        ending = self.store.record(one['run'])['ending']
        self.assertEqual(list(ending), ['declared_at_utc', 'reason', 'allow_incomplete', 'meaning'])
        self.assertIsNone(ending['allow_incomplete'])
        current = self.discovered(one['run'])
        facts = current['lifecycle']
        self.assertEqual((facts['ending']['value'], facts['migration']['value'], facts['packaged']['value']), (True, 'none', False))
        self.assertEqual(lifecycle.authorized(current), 'preserve')
        summary = self.status_row(one['run'])['summary']
        self.assertEqual(summary, 'session registered; capture open after a finalization attempt that did not freeze it; '
                         'ending declared; preservation pending')

    def test_migrated_but_not_packaged_reports_packaging_pending(self):
        one = self.workflow.new()
        self.start(one['run'], self.legacy)
        with mock.patch.object(berman_preserve, 'package', side_effect=OSError('power loss')):
            with self.assertRaisesRegex(OSError, 'power loss'):
                self.workflow.finish('The engagement ended')
        self.assertEqual(self.workflow.progress()[0]['state'], 'migrated; packaging pending')
        status = self.status_row(one['run'])
        facts = status['lifecycle']
        self.assertEqual((facts['migration']['value'], facts['packaged']['value'], facts['ending']['value']),
                         ('indexed', False, True))
        self.assertTrue(status['summary'].endswith('ending declared; migration indexed; recording package pending'))
        self.assertEqual(facts['disagreements'], [])

    def test_problem_row_leaves_unsupported_facts_unknown(self):
        one = self.workflow.new()
        self.start(one['run'], self.legacy)
        self.workflow.finish('The engagement ended')
        layout_path = self.root / 'source-layout.json'
        layout = read(layout_path)
        write(layout_path, dict(layout, runs=[r for r in layout['runs'] if r['run'] != one['run']]))
        self.assertEqual(self.discovered(one['run'])['location'], 'problem')
        status = self.status_row(one['run'])
        facts = status['lifecycle']
        self.assertEqual((facts['ending']['value'], facts['packaged']['value']), (True, True))
        self.assertIsNone(facts['migration']['value'])
        self.assertIn('migration journal phase indexed', facts['migration']['unknown'][0])
        self.assertIn('migration', status['unknown'])
        self.assertNotIn('preservation pending', status['summary'])
        self.assertIn('ending declared; migration unknown; recording package made', status['summary'])
        # Without an operation record, as for runs preserved before Berman kept them, and with a damaged binding.
        write(layout_path, layout)
        (self.store.folder(one['run']) / 'operation.json').unlink()
        (self.root / 'runs' / one['run'] / 'session/binding.json').write_text('{broken binding')
        current = self.discovered(one['run'])
        self.assertEqual(current['location'], 'problem')
        facts = current['lifecycle']
        self.assertEqual([facts[name]['value'] for name in ('ending', 'migration', 'packaged')], [None, 'indexed', None])
        self.assertIn('preserved before Berman', facts['ending']['unknown'][0])
        summary = self.status_row(one['run'])['summary']
        self.assertIn('ending unknown; migration indexed; recording package unknown', summary)
        self.assertNotIn('no engagement ending declared', summary)

    def test_low_level_migration_without_declaration_stays_visible(self):
        one = self.workflow.new()
        self.start(one['run'], self.legacy)
        self.legacy.command('finalize', one['run'])
        fieldwork.migrate(self.store.collector_for(self.store.record(one['run'])), one['run'])
        status = self.status_row(one['run'])
        self.assertEqual(status['state'], 'archived')
        self.assertTrue(status['lifecycle']['disagreements'][0].startswith('migrated without a declared ending'))
        self.assertIn('no recording package', status['summary'])
        self.assertIn('  migrated without a declared ending', self.status_text())
        with self.assertRaisesRegex(Decision, 'found none'):
            self.workflow.finish('Adopt it', one['run'])
        self.assertEqual(self.workflow.new()['run'], 'run-002')
        value = self.store.record(one['run'])
        self.assertNotIn('ending', value)
        self.assertNotIn('package', value)
        self.assertFalse((self.root / 'archive/recordings' / (one['run'] + '.zip')).exists())
        self.assertIn(one['run'], [r['run'] for r in self.workflow.status()['runs']])

    def test_replaced_session_is_neither_unused_nor_retirable(self):
        one = self.workflow.new()
        self.start(one['run'], self.legacy)
        self.store.collector_for(self.store.record(one['run'])).command(
            'replace', one['run'], reason='Fixture displacement', relationship='continuation')
        status = self.status_row(one['run'])
        self.assertTrue(status['started'])
        self.assertEqual(status['lifecycle']['capture']['value'], 'displaced')
        self.assertIn('session displaced', status['summary'])
        with self.assertRaisesRegex(Decision, 'Expected one prepared packet; found none'):
            self.workflow.cancel('Stale observation')
        with self.assertRaisesRegex(Decision, 'session material'):
            self.workflow.retire(self.store.record(one['run']), 'Stale observation')
        with mock.patch('os.execv') as execute:
            with self.assertRaisesRegex(Decision, 'Expected one prepared packet'):
                self.workflow.launch()
            execute.assert_not_called()
        with self.assertRaisesRegex(Decision, 'replaced and no replacement session has registered'):
            self.workflow.finish('Ended', one['run'])
        with self.assertRaisesRegex(Decision, 'has no declared ending'):
            self.workflow.new()
        self.assertTrue((self.store.field / one['run'] / 'ASSIGNMENT.md').exists())
        self.assertNotIn('cancellation', self.store.record(one['run']))
        self.assertNotIn('ending', self.store.record(one['run']))

    def test_unfinishable_previous_engagement_leaves_no_request(self):
        one = self.workflow.new()
        self.start(one['run'], self.legacy)
        self.store.collector_for(self.store.record(one['run'])).command(
            'replace', one['run'], reason='Fixture displacement', relationship='continuation')
        with self.assertRaisesRegex(Decision, 'has no declared ending') as refused:
            self.workflow.new()
        self.assertNotIn('--finish-previous', str(refused.exception))
        self.assertIn('--keep-open', str(refused.exception))
        for reason, message in (('  ', 'Describe the engagement ending'), ('It ended', 'replaced and no replacement session')):
            with self.subTest(reason=reason), self.assertRaisesRegex(Decision, message):
                self.workflow.new(finish_previous=reason)
            self.assertEqual(list((self.store.operations / 'new-requests').glob('*.json')), [])
        self.assertEqual(self.workflow.status()['connections']['stewart']['pending_requests'], [])
        two = self.workflow.new(keep_open=True)
        self.assertEqual((two['run'], two['preserved'], two['retained_open_runs']), ('run-002', [], [one['run']]))
        # Once the replacement session registers, the engagement continues and nothing stale ends it.
        self.start(one['run'], self.legacy, sid='replacement-session')
        self.assertNotIn('ending', self.store.record(one['run']))
        self.assertEqual(self.discovered(one['run'])['location'], 'active')

    def test_session_material_without_registration_is_reported_and_kept(self):
        one = self.workflow.new()
        (Path(one['packet']) / 'session').mkdir()  # As left by a registration interrupted before its binding.
        status = self.status_row(one['run'])
        self.assertEqual(status['summary'], 'ready; not yet started')
        self.assertIn('session material is present although no session registered', status['lifecycle']['disagreements'][0])
        with self.assertRaisesRegex(Decision, 'session material'):
            self.workflow.cancel('Unwanted')
        self.assertTrue((Path(one['packet']) / 'session').is_dir())

    def test_invalid_operation_record_is_scoped_and_save_refuses_inconsistency(self):
        orchard = self.workflow.new(connection='orchard')
        path = self.store.folder(orchard['run']) / 'operation.json'
        write(path, dict(read(path), phase='bogus'))
        status = self.workflow.status()
        self.assertIn('Invalid operation record', status['connections']['orchard']['blocked_by'][0])
        self.assertIn("unknown phase 'bogus'", status['connections']['orchard']['blocked_by'][0])
        self.assertIsNone(next(r for r in status['runs'] if r['run'] == orchard['run'])['lifecycle']['ending']['value'])
        stewart = self.workflow.new()
        self.assertEqual(stewart['run'], 'run-002')
        saved = self.store.folder(stewart['run']) / 'operation.json'
        before = saved.read_bytes()
        value = self.store.record(stewart['run'])
        value['phase'] = 'archived'
        with self.assertRaisesRegex(ValueError, 'Invalid operation record: .*phase archived needs package'):
            self.store.save(value)
        self.assertEqual(saved.read_bytes(), before)

    def test_invalid_request_names_its_problem(self):
        one = self.workflow.new()
        self.start(one['run'], self.legacy)
        path = self.store.operations / 'new-requests/fixture.json'
        write(path, {'schema': 'berman.new-request/1', 'state': 'pending', 'previous_run': 'run-001',
                     'finish_reason': 'ended', 'step': 'repeat', 'result_run': 'nowhere'})
        with self.assertRaisesRegex(Decision, f'Invalid new-run request: {path}: result_run is not a run identity'):
            self.workflow.new()

    def test_status_keeps_existing_keys(self):
        self.workflow.new()
        two = self.workflow.new(connection='orchard')
        self.start(two['run'], self.berman)
        rows = self.workflow.status()['runs']
        self.assertEqual(len(rows), 2)
        for current in rows:
            self.assertLessEqual(STATUS_KEYS | {'summary', 'unknown', 'lifecycle'}, set(current))
            facts = current['lifecycle']
            self.assertIs(current['started'], facts['registered']['value'])
            self.assertEqual(current['prepared_unstarted'], facts['registered']['value'] is False)
            self.assertEqual(current['ending_declared'], facts['ending']['value'] is True)
            self.assertEqual(current['unknown'], [])
        self.assertEqual([r['started'] for r in rows], [False, True])
        self.assertEqual(rows[0]['summary'], 'ready; not yet started')

    def test_damaged_registry_makes_cutover_state_unknown(self):
        (self.root / 'hooks').mkdir()
        write(self.root / 'hooks/berman-capture.hooks.json', berman_capture.definition(self.store, 'berman-codex'))
        path = self.home / 'stewart-fieldwork/damaged-session.json'
        path.parent.mkdir()
        path.write_text('{broken registry')
        sessions = berman_capture.registrations(self.store)
        self.assertEqual([(s['session_id'], s['open'], s['pending_callback']) for s in sessions],
                         [('damaged-session', None, None)])
        self.assertIn(str(path), sessions[0]['unknown'][0])
        with self.assertRaisesRegex(Decision, r'requires closed captures.*damaged-session \(legacy-codex: state unknown'):
            berman_capture.install(self.store, 'berman-codex')
        self.assertFalse((self.home / 'hooks.json').exists())
        self.assertFalse((self.root / 'hooks/installations').exists())

    def test_closure_matches_frozen_collectors(self):
        self.assertIs(lifecycle.RUN, berman_legacy.RUN)
        self.assertIs(lifecycle.SESSION, berman_legacy.SESSION)
        frozen = berman_collector.Collector(self.store.field, self.home, self.root)
        packet = self.store.field / 'run-042'
        (packet / 'session').mkdir(parents=True)
        write(self.home / 'stewart-fieldwork/closure.json', {'run': 'run-042', 'session_id': 'closure'})
        for closing in (None, 'finalized.json', 'ended.json'):
            with self.subTest(closing=closing):
                if closing:
                    for name in ('finalized.json', 'ended.json'):
                        (packet / 'session' / name).unlink(missing_ok=True)
                    write(packet / 'session' / closing, {'fixture': True})
                self.assertEqual(closed_by(packet), [closing] if closing else [])
                self.assertEqual(frozen.legacy_claim('closure'), None if closing else 'run-042')
                opened = [s['open'] for s in berman_capture.registrations(self.store) if s['session_id'] == 'closure']
                self.assertEqual(opened, [closing is None])


if __name__ == '__main__':
    unittest.main()
