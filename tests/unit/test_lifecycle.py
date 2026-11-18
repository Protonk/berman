"""Lifecycle facts: each stage is reported separately, with its evidence or the reason it is unknown.

The runs are synthetic records of a co-located workspace, written by tests/support/legacy.py as
release 0.26.3 wrote them. Reading states what they establish and what a run still awaits from
that release; it never ends, freezes, migrates or packages one.
"""

import contextlib
import io
import shutil
import unittest

from tests.integration.test_connections import Base
from tests.support import legacy
from tests.support.workspace import state
from berman.files import read, write
from berman import cli
from berman._legacy import api
from berman import lifecycle
from berman.records import closed_by

INFERRED = {
    'reason': 'Existing explicit finalization or migration authorizes completing preservation',
    'basis': lifecycle.INFERRED_ENDING,
}
DECLARED = {
    'declared_at_utc': '2026-10-24T13:45:31.459668Z',
    'reason': 'The researcher states the run is closed.',
    'allow_incomplete': None,
    'meaning': legacy.ENDING_MEANING,
}
# The shape of the real run-008 record (adopted after preparation, declared, migrated and packaged).
RUN_008_SHAPE = {
    'schema': 'berman.operation/1',
    'run': 'run-008',
    'phase': 'archived',
    'project': '/fixture/stewart',
    'documents': ['README.md'],
    'adopted_existing_run_at_utc': '2026-10-24T13:45:31.459562Z',
    'started_session': '01a16bb4-eff6-7cb3-b3e7-cb79077117f6',
    'ending': DECLARED,
    'package': {
        'path': '/fixture/archive/recordings/run-008.zip',
        'sha256': '0' * 64,
        'bytes': 1,
        'members': 1,
        'namespace': 'stewart-analysis/',
    },
}
# What status reports for each listed run.
STATUS_KEYS = {
    'run',
    'connection',
    'state',
    'recipe',
    'assignment_origin',
    'phase',
    'session',
    'ending_declared',
    'started',
    'record',
    'last_capture',
    'problems',
    'summary',
    'unsettled',
    'opened_with',
    'unknown',
    'lifecycle',
}
CLOSE = 'It has to be closed with the release that opened it (Berman 0.26.3); this version does not close'


def evidence(**values):
    location = values.get('location', 'active')
    base = {
        'location': 'active',
        'placed': None if location == 'problem' else location,
        'record_error': None,
        'binding': None,
        'registrations': [],
        'replacements': [],
        'closed_by': [],
        'session_material': False,
        'capture': None,
        'journal': None,
        'indexed': False,
        'problems': [],
    }
    return dict(base, **values)


def record(phase='ready', **values):
    return dict({'schema': 'berman.operation/1', 'run': 'run-001', 'phase': phase}, **values)


def row(location='active', registered=False, value=None, migration_pending=False, problems=()):
    return {
        'run': 'run-001',
        'location': location,
        'record': value,
        'migration_pending': migration_pending,
        'problems': list(problems),
        'lifecycle': {'registered': lifecycle.fact(registered, unknown=['fixture'])},
    }


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

    def test_record_validation_accepts_written_shapes_and_rejects_others(self):
        accepted = [record(phase) for phase in ('reserved', 'prepared', 'ready')]
        accepted += [
            record('launching', launch_attempts=[{'requested_at_utc': 'now'}]),
            record('active', started_session='fixture-session'),
            record('closing', ending=DECLARED),
            record('closing', migration_authority={'journal': 'fixture'}),
            record('closing', ending=INFERRED, migration_authority={'journal': 'fixture'}),
            record('archived', package={'path': '/fixture.zip'}, ending=dict(DECLARED, allow_incomplete='boundary')),
            # d1f71cd stored --allow-incomplete verbatim, including a blank value.
            record('closing', ending=dict(DECLARED, allow_incomplete='')),
            record('closing', ending=dict(DECLARED, allow_incomplete='  ')),
            record('cancelling', cancellation={'reason': 'unwanted'}),
            record('cancelled', cancellation={'reason': 'unwanted'}),
            record('ready', retained_open_runs=['run-002'], started_session=None),
        ]
        for value in accepted:
            with self.subTest(phase=value['phase']):
                self.assertIs(lifecycle.validate_record(value, 'run-001'), value)
        self.assertIs(lifecycle.validate_record(RUN_008_SHAPE, 'run-008'), RUN_008_SHAPE)
        refused = {
            'unknown phase': record('bogus'),
            'phase closing needs ending or migration_authority': record('closing'),
            'phase archived needs package': record('archived'),
            'phase cancelled needs cancellation': record('cancelled'),
            'phase launching needs launch_attempts': record('launching', launch_attempts=[]),
            'phase active needs started_session': record('active'),
            'ending needs a reason': record('closing', ending=dict(DECLARED, reason=' ')),
            'a declared ending needs declared_at_utc': record('closing', ending={'reason': 'Ended'}),
            'allow_incomplete must be absent, null or text': record(
                'closing', ending=dict(DECLARED, allow_incomplete=True)
            ),
            'started_session is not a session identity': record('active', started_session='../escape'),
            'retained_open_runs must list run identities': record(retained_open_runs=['run-1']),
            'package needs a path': record('archived', package=True),
            'unknown record purpose': record(purpose='unrecognised'),
        }
        for message, value in refused.items():
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                lifecycle.validate_record(value, 'run-001')
        for value, run in ((record(), 'run-002'), (dict(record(), schema='other'), 'run-001'), ([], 'run-001')):
            with self.assertRaisesRegex(ValueError, 'not a berman.operation/1 record'):
                lifecycle.validate_record(value, run)

    def test_journal_validation(self):
        # run-006's journal names the pre-rename root; destinations are not compared.
        journal = {
            'schema': 'stewart.migration/1',
            'source': '/fieldwork/run-006',
            'destination': '/historical/study/runs/run-006',
            'phase': 'indexed',
            'entry': {'run': 'run-006'},
        }
        self.assertEqual(lifecycle.journal_phase(journal, 'run-006'), 'indexed')
        self.assertEqual(
            lifecycle.journal_phase(dict(journal, run='run-006', phase='inventoried'), 'run-006'), 'inventoried'
        )
        for value, message in (
            (dict(journal, entry={'run': 'run-007'}), 'another run'),
            (dict(journal, run='run-007'), 'another run'),
            (dict(journal, entry=None), 'another run'),
            (dict(journal, phase='moved'), 'unknown migration phase'),
            (dict(journal, schema='other'), 'not a stewart.migration/1'),
            ([], 'not a stewart.migration/1'),
        ):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, message):
                lifecycle.journal_phase(value, 'run-006')

    def test_archived_run_without_record_leaves_ending_and_package_unknown(self):
        facts = lifecycle.assess(
            None,
            evidence(
                location='archived',
                indexed=True,
                closed_by=['finalized.json'],
                binding={'run': 'run-004', 'session_id': 'old'},
            ),
        )
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
                self.assertEqual(
                    facts['unknown'],
                    ['registered', 'capture', 'finalized']
                    + ([] if placed == 'active' else ['ending', 'migration', 'packaged']),
                )
        self.assertEqual(
            lifecycle.assess(None, evidence(location='problem', problems=problem))['migration']['unknown'], problem
        )
        # A readable record still says whether an ending was declared or a package made.
        moved = lifecycle.assess(
            record('active', started_session='s'), evidence(location='problem', placed='archived', problems=problem)
        )
        self.assertEqual(
            (moved['ending']['value'], moved['packaged']['value'], moved['migration']['value']), (False, False, None)
        )
        self.assertEqual(moved['migration']['unknown'], ['archived without inventory or migration journal'])
        self.assertEqual(moved['unknown'], ['capture', 'finalized', 'migration'])
        # An indexed journal without its inventory entry is neither unmigrated nor indexed.
        journal = lifecycle.assess(
            None, evidence(location='problem', placed='archived', journal='indexed', problems=problem)
        )
        self.assertIsNone(journal['migration']['value'])
        self.assertIn('source-layout.json has no entry', journal['migration']['unknown'][0])
        indexed = lifecycle.assess(
            RUN_008_SHAPE | {'run': 'run-001'}, evidence(location='archived', journal='indexed', indexed=True)
        )
        self.assertEqual((indexed['migration']['value'], indexed['disagreements']), ('indexed', []))

    def test_unreadable_record_keeps_registration_evidence(self):
        error = 'Invalid operation record: /fixture/operation.json: unknown phase'
        facts = lifecycle.assess(
            None, evidence(record_error=error, problems=[error], binding={'run': 'run-001', 'session_id': 'fixture'})
        )
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
        cases = {
            'open': evidence(binding=bound, capture={'event': 'SessionEnd'}),
            'attempted': evidence(binding=bound, capture={'event': 'manual-finalize'}),
            'finalized': evidence(binding=bound, closed_by=['finalized.json']),
            'switched': evidence(binding=bound, closed_by=['ended.json']),
        }
        lines = {
            name: lifecycle.describe(lifecycle.assess(record('active', started_session='fixture'), ev), 'active')
            for name, ev in cases.items()
        }
        self.assertEqual(
            lines['open'], 'session registered; capture open after SessionEnd; no engagement ending declared'
        )
        self.assertEqual(
            lines['attempted'],
            'session registered; capture open after a finalization attempt that did not '
            'freeze it; no engagement ending declared',
        )
        self.assertIn('finalized.json (frozen; not an ending)', lines['finalized'])
        self.assertIn('ended.json (registration moved to another run; not an ending)', lines['switched'])
        for line in lines.values():
            self.assertIn('no engagement ending declared', line)
            self.assertNotIn('ending declared;', line.replace('no engagement ending declared', ''))
        self.assertEqual(lifecycle.describe(lifecycle.assess(record(), evidence()), 'ready'), 'ready; not yet started')
        self.assertEqual(
            lifecycle.describe(lifecycle.assess(record('launching', launch_attempts=[{}]), evidence()), 'launching'),
            'launch attempted; no session registered yet',
        )
        unknown = lifecycle.assess(record(), evidence(problems=['run-001: damaged']))
        self.assertEqual(
            lifecycle.describe(unknown, 'ready'),
            'starting state unknown; no engagement ending declared; unknown: registered, capture, finalized',
        )
        displaced = lifecycle.assess(record(), evidence(replacements=['session-history/x/replacement.json']))
        self.assertEqual(displaced['capture']['value'], 'displaced')
        self.assertIn('session displaced', lifecycle.describe(displaced, 'ready'))

    def test_disagreements_name_records_that_conflict_with_evidence(self):
        bound = {'run': 'run-001', 'session_id': 'fixture'}
        migrated = evidence(location='archived', indexed=True, binding=bound, closed_by=['finalized.json'])
        started = record('active', started_session='fixture')
        self.assertEqual(lifecycle.assess(started, migrated)['disagreements'], [lifecycle.LOW_LEVEL])
        # A migration still under way is resumed by settlement; declared or authorized ones are ordinary.
        for value, ev in (
            (started, dict(migrated, journal='moved-and-verified')),
            (record('closing', ending=DECLARED), migrated),
            (record('closing', migration_authority={'journal': 'j'}), migrated),
            (None, migrated),
            (RUN_008_SHAPE | {'run': 'run-001'}, migrated),
            (record('archived', started_session='fixture', package={'path': '/p.zip'}, ending=INFERRED), migrated),
        ):
            with self.subTest(value=value, journal=ev['journal']):
                facts = lifecycle.assess(value, ev)
                self.assertEqual(facts['disagreements'], [])
                if (value or {}).get('package'):
                    self.assertTrue(lifecycle.describe(facts, 'archived').endswith('recording package made'))
        moved_back = lifecycle.assess(RUN_008_SHAPE | {'run': 'run-001'}, evidence(binding=bound))
        self.assertEqual(moved_back['disagreements'], ['record says archived but the packet is not under runs/'])
        self.assertEqual(lifecycle.assess(record(), evidence())['disagreements'], [])

    def test_unsettled_names_what_a_run_awaits_and_an_unknown_start_is_neither_unused_nor_open(self):
        self.assertEqual(
            lifecycle.unsettled(row('reserved', False, record('reserved'))),
            'it was reserved and its packet was never prepared',
        )
        self.assertEqual(
            lifecycle.unsettled(row('active', False, record())), 'its prepared packet was never started or retired'
        )
        self.assertTrue(lifecycle.unsettled(row('active', True, record())).startswith('its engagement is open'))
        # An unknown start is not called unused: the run is open as far as anything establishes.
        self.assertTrue(lifecycle.unsettled(row('active', None, record())).startswith('its engagement is open'))
        # A row with problems states no reason; what it awaits is unknown.
        self.assertIsNone(lifecycle.unsettled(row('active', None, record(), problems=['run-001: damaged'])))
        self.assertIsNone(lifecycle.unsettled(row('problem', None, None, problems=['run-001: damaged'])))
        # At rest: retired, or archived with whatever history left it.
        self.assertIsNone(lifecycle.unsettled(row('unused', False, record('cancelled', cancellation={'reason': 'x'}))))
        self.assertIsNone(lifecycle.unsettled(row('archived', True, RUN_008_SHAPE)))
        self.assertIsNone(lifecycle.unsettled(row('archived', True, None)))
        # Migrated by hand with no declared ending and no package: reported as it is, never as awaiting an ending.
        self.assertIsNone(lifecycle.unsettled(row('archived', True, record('active', started_session='s'))))
        self.assertIsNone(lifecycle.unsettled(row('archived', True, record('closing', ending=INFERRED))))
        pending = dict(row('archived', True, RUN_008_SHAPE), reading_pending=True)
        self.assertIn('never published a reading edition', lifecycle.unsettled(pending))

    def test_unsettled_covers_everything_the_old_writer_would_have_settled(self):
        def settled_by_the_writer(r):
            value = r['record']
            return bool(
                (value and value.get('phase') == 'cancelling')
                or (value and lifecycle.declared(value) and value.get('phase') != 'archived')
                or (
                    r['migration_pending']
                    or (value and value.get('migration_authority') and value.get('phase') != 'archived')
                )
            )

        values = [
            None,
            record(),
            record('cancelling', cancellation={'reason': 'x'}),
            record('closing', ending=DECLARED),
            record('archived', ending=DECLARED, package={'path': 'p'}),
            record('closing', ending=INFERRED),
            record('closing', migration_authority={'journal': 'j'}),
            record('archived', migration_authority={'journal': 'j'}, package={'path': 'p'}),
        ]
        for value in values:
            for pending in (False, True):
                with self.subTest(value=value, pending=pending):
                    candidate = row('archived', True, value, pending)
                    self.assertEqual(lifecycle.unsettled(candidate) is not None, settled_by_the_writer(candidate))
        self.assertEqual(
            lifecycle.unsettled(row(value=record('cancelling', cancellation={'reason': 'x'}))),
            'retiring its unused preparation was begun and not completed',
        )
        self.assertEqual(
            lifecycle.unsettled(row(value=record('closing', ending=DECLARED))),
            'its ending is declared, but its packet was never preserved',
        )
        self.assertEqual(
            lifecycle.unsettled(row(value=record(), migration_pending=True)),
            'its migration was begun and not completed',
        )
        self.assertEqual(
            lifecycle.unsettled(row('archived', True, record('closing', ending=DECLARED))),
            'its ending is declared and its packet moved, but its recording package was never made',
        )

    def test_outstanding_rows(self):
        self.assertFalse(lifecycle.outstanding(row('unused', value=record('cancelled', cancellation={'reason': 'x'}))))
        self.assertTrue(
            lifecycle.outstanding(row('unused', value=record('cancelled', cancellation={}), problems=['x']))
        )
        self.assertFalse(lifecycle.outstanding(row('archived', True)))
        self.assertFalse(lifecycle.outstanding(row('archived', True, RUN_008_SHAPE)))
        self.assertTrue(lifecycle.outstanding(row('archived', True, record('active', started_session='s'))))
        self.assertTrue(lifecycle.outstanding(row('archived', True, None, migration_pending=True)))
        self.assertTrue(lifecycle.outstanding(row('reserved')))


class StatusLifecycleTests(Base):
    def status_row(self, run):
        return next(r for r in self.status()['runs'] if r['run'] == run)

    def discovered(self, run):
        return next(r for r in self.store.discover() if r['run'] == run)

    def status_text(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(cli.main(['--root', str(self.root), 'status']), 0)
        return output.getvalue()

    def started(self, connection=None, **session):
        run = legacy.prepare(self.store, connection)
        return run, legacy.start(self.store, run, **session)

    def test_reading_status_leaves_every_file_unchanged(self):
        self.finished()
        self.started()
        legacy.prepare(self.store, 'orchard')
        before = state(self.base)
        self.status()
        self.status_text()
        self.assertEqual(state(self.base), before)
        self.assertFalse((self.root / '.berman-locks').exists() or (self.root / '.berman.lock').exists())

    def test_terminal_stop_and_session_end_without_declaration_is_not_ended(self):
        run, _ = self.started(end=True)
        status = self.status_row(run)
        capture = status['lifecycle']['capture']
        self.assertEqual((capture['value'], capture['last_event'], capture['closed_by']), ('open', 'SessionEnd', []))
        self.assertIs(status['lifecycle']['ending']['value'], False)
        self.assertIs(status['lifecycle']['finalized']['value'], False)
        self.assertIn('no engagement ending declared', status['summary'])
        self.assertNotIn(status['phase'], status['summary'])
        self.assertTrue(status['unsettled'].startswith('its engagement is open'))
        self.assertNotIn('ending', self.store.record(run))

    def test_finalized_but_undeclared_engagement_stays_open(self):
        run, _ = self.started()
        legacy.finalize(self.store, run)
        status = self.status_row(run)
        facts = status['lifecycle']
        self.assertIs(facts['finalized']['value'], True)
        self.assertEqual((facts['capture']['value'], facts['capture']['closed_by']), ('closed', ['finalized.json']))
        self.assertIs(facts['ending']['value'], False)
        self.assertEqual(status['state'], 'finalized')
        self.assertTrue(lifecycle.unsettled(self.discovered(run)).startswith('its engagement is open'))
        text = self.status_text()
        self.assertIn(
            f'{run} (stewart): capture closed by finalized.json (frozen; not an ending); no engagement ending declared',
            text,
        )
        self.assertIn(
            f'  {run}: its engagement is open: the packet is still in the packet root with no declared ending. {CLOSE}',
            text,
        )
        self.assertNotIn('ending', self.store.record(run))

    def test_run_switch_closes_capture_without_ending(self):
        run, _ = self.started()
        session = self.store.field / run / 'session'
        # As the collector left it when the session's registration moved to another run.
        write(
            session / 'ended.json',
            {
                'ended_at_utc': 'now',
                'next_run': 'run-002',
                'capture_sha256': read(session / 'current.json')['generation'],
            },
        )
        status = self.status_row(run)
        self.assertEqual(status['state'], 'ended')  # The collector's word.
        facts = status['lifecycle']
        self.assertEqual((facts['capture']['value'], facts['capture']['closed_by']), ('closed', ['ended.json']))
        self.assertIs(facts['ending']['value'], False)
        self.assertIs(facts['finalized']['value'], False)
        self.assertIn('ended.json (registration moved to another run; not an ending)', status['summary'])
        self.assertFalse(status['ending_declared'])

    def test_damaged_binding_and_registry_yield_unknown(self):
        run, sid = self.started()
        (self.store.field / run / 'session/binding.json').write_text('{broken binding')
        facts = self.discovered(run)['lifecycle']
        self.assertIs(facts['registered']['value'], True)
        self.assertIn(f'registration berman-capture/{sid}.json', facts['registered']['evidence'])
        self.assertEqual(facts['unknown'], ['capture', 'finalized'])
        (self.home / 'berman-capture' / (sid + '.json')).write_text('{broken registry')
        status = self.status()
        row = next(r for r in status['runs'] if r['run'] == run)
        self.assertIsNone(row['started'])
        self.assertEqual(row['unknown'], ['registered', 'capture', 'finalized'])
        self.assertIn('damaged', row['lifecycle']['registered']['unknown'][0])
        self.assertIsNone(status['connections']['stewart']['next_recipe'])
        self.assertIn('starting state is unknown (', status['connections']['stewart']['plan_error'])
        self.assertIn('damaged', status['connections']['stewart']['plan_error'])
        text = self.status_text()
        self.assertIn(
            'starting state unknown; no engagement ending declared; unknown: registered, capture, finalized', text
        )
        self.assertIn('  ' + row['problems'][0], text)

    def test_damaged_started_run_keeps_its_plan_step_and_started_state(self):
        run, sid = self.started()
        self.assertNotIn('started_session', self.store.record(run))
        journal = self.root / 'migrations' / (run + '.json')
        journal.parent.mkdir(exist_ok=True)
        journal.write_text('{broken journal')
        status = self.status()
        self.assertEqual(status['connections']['stewart']['next_recipe'], 'participant-inquiry')
        row = next(r for r in status['runs'] if r['run'] == run)
        self.assertTrue(row['started'])
        self.assertEqual(row['session'], sid)
        self.assertIn('session/binding.json names ' + sid, row['lifecycle']['registered']['evidence'])
        self.assertIsNone(row['lifecycle']['migration']['value'])
        # A damaged binding still has independent registration evidence of a start.
        (self.store.field / run / 'session/binding.json').write_text('{broken binding')
        status = self.status()
        self.assertEqual(status['connections']['stewart']['next_recipe'], 'participant-inquiry')
        row = next(r for r in status['runs'] if r['run'] == run)
        self.assertEqual(row['lifecycle']['registered']['evidence'], [f'registration berman-capture/{sid}.json'])
        # With every start indicator unreadable, status reports uncertainty, not the repeat.
        (self.home / 'berman-capture' / (sid + '.json')).write_text('{broken registry')
        status = self.status()
        self.assertIsNone(status['connections']['stewart']['next_recipe'])
        self.assertIn('starting state is unknown', status['connections']['stewart']['plan_error'])
        row = next(r for r in status['runs'] if r['run'] == run)
        self.assertIn('registered', row['unknown'])
        self.assertEqual(row['lifecycle']['registered']['unknown'], row['problems'])

    def test_damaged_journal_is_scoped_to_its_connection_and_status_still_works(self):
        run, _ = self.started('orchard')
        journal = self.root / 'migrations' / (run + '.json')
        journal.parent.mkdir(exist_ok=True)
        journal.write_text('{interrupted journal')
        status = self.status()
        self.assertTrue(status['connections']['orchard']['problems'])
        self.assertFalse(status['connections']['stewart']['problems'])
        self.assertIn(str(journal), self.status_text())
        self.assertEqual(journal.read_text(), '{interrupted journal')

    def test_damaged_registry_is_attributed_from_its_binding_or_reported_for_the_workspace(self):
        run, sid = self.started('orchard')
        (self.home / 'berman-capture' / (sid + '.json')).write_text('{broken registry')
        status = self.status()
        self.assertTrue(status['connections']['orchard']['problems'])
        self.assertEqual(status['workspace_problems'], [])
        # A registry no binding names belongs to no run; the workspace reports it.
        path = self.home / 'berman-capture/unknown-session.json'
        path.write_text('{broken registry')
        status = self.status()
        self.assertIn('Unattributed damaged session registry', status['workspace_problems'][0])
        self.assertIn(str(path), status['workspace_problems'][0])

    def test_a_claude_registration_in_the_workspace_is_registration_evidence_too(self):
        # The Claude adapter kept its registry inside the workspace, not in a harness home.
        run = legacy.prepare(self.store)
        sid = '0b6f3d0e-7a51-4c52-9d0c-7d1f6a1b2c3d'
        write(
            self.root / '.claude-fieldwork/sessions' / (sid + '.json'),
            {'run': run, 'session_id': sid, 'workspace': str(self.root), 'harness': 'claude'},
        )
        facts = self.discovered(run)['lifecycle']
        self.assertIs(facts['registered']['value'], True)
        self.assertEqual(facts['registered']['evidence'], [f'registration sessions/{sid}.json'])
        # Registered with no binding in the packet: the capture is unknown, never "none".
        self.assertIsNone(facts['capture']['value'])
        self.assertTrue(self.status_row(run)['unsettled'].startswith('its engagement is open'))

    def test_a_registration_without_a_packet_is_a_problem_and_a_support_collection_is_not_research(self):
        support = self.root / 'support-trial'
        write(support / 'source-layout.json', {'runs': [{'run': 'run-900101', 'current_root': 'runs/run-900101'}]})
        write(support / 'runs/run-900101/session/binding.json', {'run': 'run-900101', 'session_id': 'support-session'})
        write(self.home / 'berman-capture/support-session.json', {'run': 'run-900101', 'session_id': 'support-session'})
        self.store.catalogue.support = ['support-trial']
        self.assertEqual(self.store.discover(), [])
        write(self.home / 'berman-capture/missing-session.json', {'run': 'run-900102', 'session_id': 'missing-session'})
        self.assertIn('packet is missing', self.status()['connections']['stewart']['problems'][0])

    def test_an_open_support_trial_is_unsettled_never_research_and_a_closed_one_is_not_listed(self):
        run = legacy.support_trial(self.store, phase='launching')
        self.assertEqual(self.store.discover(), [], 'a support trial was read as research')
        status = self.status()
        self.assertEqual(status['runs'], [])
        self.assertEqual(
            [(t['run'], t['adapter'], t['phase'], t['evidence'], t['problems']) for t in status['support_trials']],
            [(run, 'berman-codex', 'launching', 'experiments/trial-a', [])],
        )
        self.assertEqual(
            status['unsettled'],
            [f'{run}: support trial experiments/trial-a was left at phase launching. {CLOSE} co-located fieldwork.'],
        )
        self.assertIn(f'{run} (support trial, berman-codex; not research): launching', self.status_text())
        # It consumes no plan step and no research number.
        self.assertEqual(status['connections']['stewart']['next_recipe'], 'work-accounts')
        receipt = self.root / 'experiments/trial-a/trial.json'
        for closed in ('cleaned', 'abandoned'):
            write(receipt, dict(read(receipt), phase=closed))
            status = self.status()
            self.assertEqual((status['support_trials'], status['unsettled']), ([], []))

    def test_an_unreadable_missing_or_foreign_support_receipt_is_a_problem_not_a_crash(self):
        run = legacy.support_trial(self.store, phase='launching')
        receipt = self.root / 'experiments/trial-a/trial.json'
        original = read(receipt)
        for damage, expected in (
            (lambda: receipt.write_text('{broken'), 'cannot be read'),
            (
                lambda: write(receipt, dict(original, run='run-900199')),
                'not the berman.support-trial/1 receipt of ' + run,
            ),
            (
                lambda: write(receipt, dict(original, schema='other/1')),
                'not the berman.support-trial/1 receipt of ' + run,
            ),
            (lambda: receipt.unlink(), 'which was never written'),
        ):
            damage()
            with self.subTest(expected=expected):
                status = self.status()
                (trial,) = status['support_trials']
                self.assertIn(expected, trial['problems'][0])
                self.assertIsNone(trial['phase'])
                # What it awaits is unknown, so it is a problem and no reason is offered.
                self.assertEqual(status['unsettled'], [])
                self.assertIn(expected, self.status_text())

    def test_declared_but_not_migrated_reports_preservation_pending(self):
        run, _ = self.started(stop=False)
        legacy.capture(self.store, run, 'manual-finalize')  # A finalization attempt that froze nothing.
        legacy.declare(self.store, run, 'Session was lost')
        current = self.discovered(run)
        facts = current['lifecycle']
        self.assertEqual(
            (facts['ending']['value'], facts['migration']['value'], facts['packaged']['value']), (True, 'none', False)
        )
        self.assertEqual(lifecycle.unsettled(current), 'its ending is declared, but its packet was never preserved')
        status = self.status_row(run)
        self.assertEqual(
            status['summary'],
            'session registered; capture open after a finalization attempt that did not freeze it; '
            'ending declared; preservation pending',
        )
        self.assertTrue((self.store.field / run).is_dir(), 'reading preserved a declared ending')
        self.assertFalse((self.root / 'runs' / run).exists())

    def test_migrated_but_not_packaged_reports_packaging_pending(self):
        run, _ = self.started()
        legacy.declare(self.store, run, 'The engagement ended')
        legacy.finalize(self.store, run)
        legacy.migrate(self.store, run)
        status = self.status_row(run)
        facts = status['lifecycle']
        self.assertEqual(
            (facts['migration']['value'], facts['packaged']['value'], facts['ending']['value']),
            ('indexed', False, True),
        )
        self.assertTrue(status['summary'].endswith('ending declared; migration indexed; recording package pending'))
        self.assertEqual(facts['disagreements'], [])
        self.assertEqual(
            status['unsettled'], 'its ending is declared and its packet moved, but its recording package was never made'
        )
        self.assertFalse((self.root / 'archive/recordings').exists(), 'reading packaged a run')

    def test_an_interrupted_migration_is_unsettled_and_left_where_it_stopped(self):
        run, _ = self.started()
        legacy.declare(self.store, run, 'The engagement ended')
        legacy.finalize(self.store, run)
        legacy.migrate(self.store, run, phase='moved-and-verified')
        before = state(self.root)
        status = self.status_row(run)
        self.assertEqual(status['lifecycle']['migration']['value'], 'moved-and-verified')
        self.assertEqual(status['unsettled'], 'its migration was begun and not completed')
        self.assertEqual(state(self.root), before)
        self.assertEqual(read(self.root / 'source-layout.json')['runs'], [])

    def test_a_preserved_run_without_its_reading_closeout_is_unsettled_not_complete(self):
        run, _ = self.started()
        legacy.finish(self.store, run, reading=False)
        status = self.status_row(run)
        self.assertEqual(status['state'], 'reading-pending')
        self.assertIs(status['lifecycle']['packaged']['value'], True)
        self.assertEqual(
            status['unsettled'], 'its recording is preserved, but its closeout never published a reading edition'
        )
        self.assertFalse((self.store.folder(run) / 'closeout.json').exists())
        self.assertFalse((self.root / 'archive/editions').exists(), 'reading published an edition')

    def test_problem_row_leaves_unsupported_facts_unknown(self):
        run = self.finished()
        # Historical records have no immutable manifest authority.
        shutil.rmtree(self.root / 'recordings')
        layout_path = self.root / 'source-layout.json'
        layout = read(layout_path)
        write(layout_path, dict(layout, runs=[r for r in layout['runs'] if r['run'] != run]))
        self.assertEqual(self.discovered(run)['location'], 'problem')
        status = self.status_row(run)
        facts = status['lifecycle']
        self.assertEqual((facts['ending']['value'], facts['packaged']['value']), (True, True))
        self.assertIsNone(facts['migration']['value'])
        self.assertIn('migration journal phase indexed', facts['migration']['unknown'][0])
        self.assertIn('migration', status['unknown'])
        self.assertNotIn('preservation pending', status['summary'])
        self.assertIn('ending declared; migration unknown; recording package made', status['summary'])
        # Without an operation record, as for runs preserved before Berman kept them, and with a damaged binding.
        write(layout_path, layout)
        (self.store.folder(run) / 'operation.json').unlink()
        (self.root / 'runs' / run / 'session/binding.json').write_text('{broken binding')
        current = self.discovered(run)
        self.assertEqual(current['location'], 'problem')
        facts = current['lifecycle']
        self.assertEqual(
            [facts[name]['value'] for name in ('ending', 'migration', 'packaged')], [None, 'indexed', None]
        )
        self.assertIn('preserved before Berman', facts['ending']['unknown'][0])
        summary = self.status_row(run)['summary']
        self.assertIn('ending unknown; migration indexed; recording package unknown', summary)
        self.assertNotIn('no engagement ending declared', summary)

    def test_low_level_migration_without_declaration_stays_visible_and_gains_nothing(self):
        run, _ = self.started()
        legacy.finalize(self.store, run)
        legacy.migrate(self.store, run)
        status = self.status_row(run)
        self.assertEqual(status['state'], 'archived')
        self.assertTrue(status['lifecycle']['disagreements'][0].startswith('migrated without a declared ending'))
        self.assertIn('no recording package', status['summary'])
        self.assertIn('  migrated without a declared ending', self.status_text())
        # It is at rest as history left it: nothing is awaited, and nothing is inferred for it.
        self.assertIsNone(status['unsettled'])
        self.assertEqual(self.status()['unsettled'], [])
        self.assertIs(status['lifecycle']['ending']['value'], False)
        value = self.store.record(run)
        self.assertNotIn('ending', value)
        self.assertNotIn('package', value)
        self.assertFalse((self.root / 'archive/recordings').exists())
        self.assertIn(run, [r['run'] for r in self.status()['runs']])

    def test_an_old_inferred_ending_is_not_read_as_a_declaration(self):
        run, _ = self.started()
        legacy.finalize(self.store, run)
        legacy.save(self.store, dict(self.store.record(run), phase='closing', ending=INFERRED))
        status = self.status_row(run)
        self.assertFalse(status['ending_declared'])
        self.assertIs(status['lifecycle']['ending']['value'], False)
        self.assertIn(
            'earlier inferred interpretation retained; not a declaration', status['lifecycle']['ending']['evidence']
        )
        self.assertTrue(status['unsettled'].startswith('its engagement is open'))

    def test_replaced_session_is_displaced_not_unused(self):
        run, _ = self.started()
        packet = self.store.field / run
        # As the collector left a replaced session: its capture set aside, with the receipt.
        (packet / 'session-history/one').mkdir(parents=True)
        (packet / 'session').rename(packet / 'session-history/one/session')
        write(packet / 'session-history/one/replacement.json', {'session_id': 'fixture-' + run, 'reason': 'Fixture'})
        status = self.status_row(run)
        self.assertTrue(status['started'])
        self.assertEqual(status['lifecycle']['capture']['value'], 'displaced')
        self.assertIn('session displaced', status['summary'])
        self.assertTrue(status['unsettled'].startswith('its engagement is open'))
        self.assertTrue((packet / 'ASSIGNMENT.md').exists())
        self.assertNotIn('ending', self.store.record(run))

    def test_session_material_without_registration_is_reported_and_kept(self):
        run = legacy.prepare(self.store)
        (self.store.field / run / 'session').mkdir()  # As left by a registration interrupted before its binding.
        status = self.status_row(run)
        self.assertEqual(status['summary'], 'ready; not yet started')
        self.assertIn(
            'session material is present although no session registered', status['lifecycle']['disagreements'][0]
        )
        self.assertTrue((self.store.field / run / 'session').is_dir())

    def test_invalid_operation_record_is_scoped_to_its_connection(self):
        run = legacy.prepare(self.store, 'orchard')
        path = self.store.folder(run) / 'operation.json'
        write(path, dict(read(path), phase='bogus'))
        status = self.status()
        self.assertIn('Invalid operation record', status['connections']['orchard']['problems'][0])
        self.assertIn("unknown phase 'bogus'", status['connections']['orchard']['problems'][0])
        self.assertIsNone(next(r for r in status['runs'] if r['run'] == run)['lifecycle']['ending']['value'])
        self.assertEqual(status['connections']['stewart']['problems'], [])

    def test_historical_records_keep_what_they_never_recorded(self):
        run = legacy.prepare(self.store)
        value = self.store.record(run)
        del value['assignment_origin']
        value['recipe']['metadata'] = None
        legacy.save(self.store, value)
        status = self.status_row(run)
        self.assertEqual((status['recipe'], status['assignment_origin']), (None, None))
        self.assertIn('Assignment: not recorded; origin: not recorded', self.status_text())
        self.assertNotIn('assignment_origin', self.store.record(run))

    def test_status_reports_these_keys_for_each_listed_run(self):
        legacy.prepare(self.store)
        self.started('orchard')
        rows = self.status()['runs']
        self.assertEqual(len(rows), 2)
        for current in rows:
            self.assertEqual(set(current), STATUS_KEYS)
            facts = current['lifecycle']
            self.assertIs(current['started'], facts['registered']['value'])
            self.assertEqual(current['ending_declared'], facts['ending']['value'] is True)
            self.assertEqual(current['unknown'], [])
            self.assertEqual(current['opened_with'], legacy.RELEASE)
        self.assertEqual([r['started'] for r in rows], [False, True])
        self.assertEqual(rows[0]['summary'], 'ready; not yet started')

    def test_closure_is_read_by_presence(self):
        self.assertIs(lifecycle.RUN, api.RUN)
        self.assertIs(lifecycle.SESSION, api.SESSION)
        packet = self.store.field / 'run-042'
        (packet / 'session').mkdir(parents=True)
        for closing in (None, 'finalized.json', 'ended.json'):
            with self.subTest(closing=closing):
                if closing:
                    for name in ('finalized.json', 'ended.json'):
                        (packet / 'session' / name).unlink(missing_ok=True)
                    write(packet / 'session' / closing, {'fixture': True})
                self.assertEqual(closed_by(packet), [closing] if closing else [])


if __name__ == '__main__':
    unittest.main()
