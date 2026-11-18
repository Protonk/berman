"""Lifecycle facts over generated records and evidence.

berman.lifecycle is a pure function of what discovery observed, so these properties range over
combinations no example suite enumerates: every closure with every record, every damaged input
with every packet location.
"""

import unittest

from hypothesis import example, given
from hypothesis import strategies as st

from berman.lifecycle import (
    FACTS,
    INFERRED_ENDING,
    PHASES,
    assess,
    declared,
    describe,
    unsettled,
)
from tests.support.properties import budget

words = st.text(alphabet='abcdefghij -', min_size=1, max_size=12).filter(str.strip)
declarations = st.fixed_dictionaries(
    {'declared_at_utc': st.just('2026-11-13T00:00:00Z'), 'reason': words},
    optional={'allow_incomplete': st.none() | st.text(max_size=6), 'meaning': words},
)
inferences = st.fixed_dictionaries({'reason': words, 'basis': st.just(INFERRED_ENDING)})
records = st.none() | st.fixed_dictionaries(
    {'schema': st.just('berman.operation/1'), 'run': st.just('run-001'), 'phase': st.sampled_from(PHASES)},
    optional={
        'ending': st.none() | declarations | inferences,
        'package': st.none() | st.fixed_dictionaries({'path': words}),
        'started_session': st.none() | st.just('session-1'),
        'migration_authority': st.fixed_dictionaries({'journal': words}),
        'previous_ending_interpretations': st.lists(inferences, max_size=1),
    },
)
# The last capture metadata an active packet holds, as far as the facts read it.
captures = st.none() | st.fixed_dictionaries(
    {'event': st.sampled_from(('UserPromptSubmit', 'Stop', 'SessionEnd', 'manual-finalize', 'manual-refresh'))}
)
# What a packet and its surroundings can show about a session, a closure or a migration.
observed = st.fixed_dictionaries(
    {
        'binding': st.none() | st.fixed_dictionaries({'session_id': st.just('session-1'), 'run': st.just('run-001')}),
        'registrations': st.lists(st.fixed_dictionaries({'path': words, 'retired': st.booleans()}), max_size=2),
        'replacements': st.lists(words, max_size=2),
        'closed_by': st.sampled_from(([], ['finalized.json'], ['ended.json'], ['finalized.json', 'ended.json'])),
        'session_material': st.booleans(),
        'capture': captures,
        'journal': st.sampled_from((None, 'inventoried', 'moved-and-verified', 'indexed', 'damaged')),
        'indexed': st.booleans(),
    }
)


@st.composite
def readings(draw):
    """(record, evidence) as discovery pairs them: an unreadable record is passed as None with its error."""
    record = draw(records)
    error = draw(st.none() | st.just('Invalid operation record: operations/run-001/operation.json: damaged'))
    problems = draw(st.lists(words, max_size=2))
    if error:
        record, problems = None, [error, *problems]
    location = 'problem' if problems else draw(st.sampled_from(('active', 'archived', 'reserved', 'unused')))
    placed = draw(st.sampled_from((None, 'active', 'archived'))) if problems else location
    return record, dict(draw(observed), location=location, placed=placed, record_error=error, problems=problems)


def row(record, evidence):
    lifecycle = assess(record, evidence)
    return {
        'run': 'run-001',
        'record': record,
        'location': evidence['location'],
        'problems': evidence['problems'],
        'migration_pending': evidence['journal'] not in (None, 'indexed'),
        'lifecycle': lifecycle,
        'started': lifecycle['registered']['value'],
    }


def parts(lifecycle, phase):
    return describe(lifecycle, phase).split('; ')


class FactTests(unittest.TestCase):
    @budget(400)
    @given(readings())
    def test_a_fact_is_stated_with_evidence_or_unknown_with_a_reason(self, reading):
        lifecycle = assess(*reading)
        for name in FACTS:
            fact = lifecycle[name]
            if fact['value'] is None:
                self.assertTrue(fact['unknown'], name)
                self.assertEqual(fact['evidence'], [], name)
            else:
                self.assertTrue(fact['evidence'], name)
                self.assertNotIn('unknown', fact, name)
        self.assertEqual(lifecycle['unknown'], [name for name in FACTS if lifecycle[name]['value'] is None])

    @budget(300)
    @given(readings())
    def test_a_damaged_record_leaves_what_it_held_unknown(self, reading):
        record, evidence = reading
        lifecycle = assess(record, evidence)
        if evidence['record_error']:
            # Never False: "no ending declared" would be a finding made from a record nobody could read.
            self.assertIsNone(lifecycle['ending']['value'])
            self.assertIsNone(lifecycle['packaged']['value'])
            self.assertIn(evidence['record_error'], ' '.join(lifecycle['ending']['unknown']))
            self.assertNotEqual(lifecycle['registered']['value'], False)
        if lifecycle['unknown']:
            self.assertIn('unknown', describe(lifecycle, (record or {}).get('phase') or 'needs-attention'))

    @budget(400)
    @given(readings())
    @example(
        (
            {'schema': 'berman.operation/1', 'run': 'run-001', 'phase': 'active', 'started_session': 'session-1'},
            {
                'binding': {'session_id': 'session-1', 'run': 'run-001'},
                'registrations': [],
                'replacements': [],
                'closed_by': ['finalized.json', 'ended.json'],
                'session_material': True,
                'capture': {'event': 'SessionEnd'},
                'journal': 'indexed',
                'indexed': True,
                'location': 'archived',
                'placed': 'archived',
                'record_error': None,
                'problems': [],
            },
        )
    )
    def test_only_an_operator_declaration_is_an_ending(self, reading):
        record, evidence = reading
        current = row(record, evidence)
        ending = current['lifecycle']['ending']['value']
        self.assertEqual(ending is True, declared(record))
        if not declared(record):
            # No callback, closure, finalization, migration or package makes one.
            self.assertNotIn('ending is declared', unsettled(current) or '')
            self.assertNotIn('ending declared', parts(current['lifecycle'], (record or {}).get('phase') or 'active'))
        if current['lifecycle']['capture']['value'] == 'closed':
            text = describe(current['lifecycle'], (record or {}).get('phase') or 'active')
            if (record or {}).get('phase') not in ('cancelling', 'cancelled'):
                self.assertIn('not an ending', text)

    @budget(300)
    @given(readings())
    def test_a_package_is_stated_only_where_the_record_names_one(self, reading):
        record, evidence = reading
        packaged = assess(record, evidence)['packaged']['value']
        if packaged is not None:
            # Migration, indexing or an archived location never stands in for the recorded package.
            self.assertEqual(packaged, bool((record or {}).get('package')))
        if record is not None:
            self.assertIsNotNone(packaged)

    @budget(400)
    @given(readings(), observed)
    def test_what_a_packet_shows_never_changes_what_the_record_says(self, reading, other):
        record, evidence = reading
        varied = dict(evidence, **other)
        for name in ('ending', 'packaged'):
            self.assertEqual(assess(record, evidence)[name], assess(record, varied)[name], name)

    @budget(400)
    @given(readings(), st.none() | declarations | inferences, st.none() | st.fixed_dictionaries({'path': words}))
    def test_what_the_record_says_never_changes_what_the_packet_shows(self, reading, ending, package):
        record, evidence = reading
        if record is None:
            return
        varied = dict(record, ending=ending, package=package)
        for name in ('registered', 'capture', 'finalized', 'migration'):
            self.assertEqual(assess(record, evidence)[name], assess(varied, evidence)[name], name)

    @budget(400)
    @given(readings())
    def test_an_unknown_start_is_never_called_unused_and_a_problem_states_no_reason(self, reading):
        current = row(*reading)
        reason = unsettled(current)
        if current['problems'] or current['started'] is None:
            # Unknown is not "no session", and what a damaged run awaits is not guessed.
            self.assertIsNone(reason)
        if reason and 'never started' in reason:
            self.assertIs(current['started'], False)
        if reason and reason.startswith('its engagement is open'):
            self.assertIs(current['started'], True)

    @budget(400)
    @given(readings())
    def test_a_run_at_rest_in_the_archive_awaits_nothing_whatever_history_left_out(self, reading):
        record, evidence = reading
        current = row(record, evidence)
        at_rest = (
            evidence['location'] == 'archived'
            and not current['migration_pending']
            and not (record or {}).get('migration_authority')
            and (record or {}).get('phase') != 'cancelling'
            and not (declared(record) and record.get('phase') != 'archived')
        )
        if at_rest:
            # No ending, no package and no closure are absences, never work this version calls outstanding.
            self.assertIsNone(unsettled(current))


if __name__ == '__main__':
    unittest.main()
