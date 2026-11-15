"""Which step a plan issues next and which identity a run receives, over generated histories.

Both answers are made from discovered rows. A retry sees the same rows and must get the same
answer; an own-plan unknown start must never be read as "not started"; an identity is never issued twice.
"""

from types import SimpleNamespace
import unittest

from hypothesis import given
from hypothesis import strategies as st

from berman._legacy.api import RUN
from berman.config import Complete, Plan
from berman.files import Decision
from berman.records import SUPPORT_RANGE, Store
from tests.support.properties import budget

STEPS = ('first', 'second', 'third', 'fourth', 'fifth')


def plan(steps):
    """A plan for connection orchard, built without files."""
    made = Plan.__new__(Plan)
    made.id, made.connection = 'orchard-roles', 'orchard'
    made.steps = [{'id': step, 'recipe': 'recipe-' + step} for step in steps]
    made.ids = list(steps)
    return made


def row(number, step, started, connection='orchard', owner='orchard-roles'):
    record = {'plan': {'id': owner, 'step': step}, 'step': step} if step else None
    unknown = {'unknown': ['its operation record cannot be read']}
    return {
        'run': f'run-{number:03d}',
        'connection': connection,
        'started': started,
        'record': record,
        'lifecycle': {'registered': unknown},
    }


# Only this plan's claims affect its sequence. Other assignments may even have unknown starts.
unstarted = st.builds(row, st.integers(100, 199), st.sampled_from(STEPS), st.just(False))
elsewhere = st.builds(
    row,
    st.integers(200, 299),
    st.sampled_from(STEPS),
    st.sampled_from((True, False, None)),
    st.just('meadow'),
    st.just('meadow-roles'),
)
other_plan = st.builds(
    row,
    st.integers(300, 399),
    st.sampled_from(STEPS),
    st.sampled_from((True, False, None)),
    st.just('orchard'),
    st.just('another-orchard-plan'),
)
unassociated = st.builds(row, st.integers(600, 699), st.none(), st.sampled_from((True, False, None)))


def direct_row(number, step, started):
    made = row(number, None, started)
    made['record'] = {'assignment_origin': 'direct', 'recipe': {'metadata': {'id': 'recipe-' + step}}}
    return made


direct = st.builds(direct_row, st.integers(700, 799), st.sampled_from(STEPS), st.sampled_from((True, False, None)))
bystanders = st.lists(unstarted | elsewhere | other_plan | unassociated | direct, max_size=6)
lengths = st.integers(1, len(STEPS))


def started_prefix(count):
    return [row(number + 1, STEPS[number], True) for number in range(count)]


class NextStepTests(unittest.TestCase):
    @budget(300)
    @given(
        lengths.flatmap(lambda n: st.tuples(st.just(n), st.integers(0, n - 1))),
        bystanders,
        st.randoms(use_true_random=False),
    )
    def test_the_first_unstarted_step_is_issued_however_often_it_is_asked(self, shape, others, random):
        total, done = shape
        rows = started_prefix(done) + others
        random.shuffle(rows)
        chosen = plan(STEPS[:total])
        step = chosen.next_step(rows)
        self.assertEqual(step['id'], STEPS[done])
        # A retry sees the same rows: an unstarted preparation of this step does not consume it.
        retried = rows + [row(300, step['id'], False)]
        self.assertEqual(chosen.next_step(retried), step)
        self.assertFalse(chosen.fulfilled(rows))

    @budget(100)
    @given(lengths, bystanders)
    def test_a_fully_started_plan_issues_nothing_more(self, total, others):
        chosen = plan(STEPS[:total])
        with self.assertRaises(Complete):
            chosen.next_step(started_prefix(total) + others)
        self.assertTrue(chosen.fulfilled(started_prefix(total) + others))

    @budget(200)
    @given(lengths, st.data(), bystanders)
    def test_an_own_plan_unknown_start_stops_the_sequence_rather_than_counting_as_unstarted(self, total, data, others):
        done = data.draw(st.integers(0, total))
        damaged = row(400, data.draw(st.sampled_from(STEPS[:total])), None)
        chosen = plan(STEPS[:total])
        with self.assertRaisesRegex(Decision, 'starting state is unknown') as refused:
            chosen.next_step(started_prefix(done) + [damaged] + others)
        self.assertNotIsInstance(refused.exception, Complete)
        self.assertFalse(chosen.fulfilled(started_prefix(done) + [damaged] + others))

    @budget(200)
    @given(st.integers(2, len(STEPS)), st.data(), bystanders)
    def test_a_gap_or_a_second_claim_is_refused(self, total, data, others):
        chosen = plan(STEPS[:total])
        later = data.draw(st.integers(1, total - 1))
        earlier = data.draw(st.integers(0, later - 1))
        skipped = [r for r in started_prefix(later + 1) if r['record']['step'] != STEPS[earlier]]
        with self.assertRaisesRegex(Decision, 'out of sequence'):
            chosen.next_step(skipped + others)
        twice = started_prefix(later + 1) + [row(500, STEPS[earlier], True)]
        with self.assertRaisesRegex(Decision, 'Conflicting sequence claim'):
            chosen.next_step(twice + others)

    @budget(100)
    @given(lengths, st.integers(0, 3), bystanders)
    def test_an_unassociated_run_does_not_advance_or_obstruct_a_plan(self, total, done, others):
        stray = row(800, None, True)
        rows = started_prefix(min(done, total - 1)) + [stray] + others
        self.assertEqual(plan(STEPS[:total]).next_step(rows)['id'], STEPS[min(done, total - 1)])

    @budget(100)
    @given(lengths, st.integers(0, 3), bystanders)
    def test_a_direct_encounter_with_the_next_recipe_does_not_consume_that_step(self, total, done, others):
        completed = min(done, total - 1)
        direct = direct_row(800, STEPS[completed], True)
        rows = started_prefix(completed) + [direct] + others
        self.assertEqual(plan(STEPS[:total]).next_step(rows)['id'], STEPS[completed])


research = st.integers(1, 999_990).filter(lambda n: n not in SUPPORT_RANGE)
support = st.sampled_from(SUPPORT_RANGE)


def name(number):
    return f'run-{number:03d}'


class AllocationTests(unittest.TestCase):
    @budget(300)
    @given(
        st.lists(research | support, max_size=12, unique=True), st.lists(research | support, max_size=6, unique=True)
    )
    def test_a_research_identity_is_new_later_and_outside_the_support_range(self, existing, reserved):
        rows = [{'run': name(number)} for number in existing]
        store = SimpleNamespace(support_identities={name(number) for number in reserved})
        run = Store.allocate(store, rows)
        number = int(run[4:])
        self.assertTrue(RUN.fullmatch(run))
        self.assertNotIn(number, SUPPORT_RANGE)
        self.assertNotIn(run, {r['run'] for r in rows} | store.support_identities)
        self.assertGreater(number, max((n for n in existing if n not in SUPPORT_RANGE), default=0))
        # Asking again without recording the first answer gives it again: allocation alone reserves nothing.
        self.assertEqual(Store.allocate(store, rows), run)
        # Once recorded, it is never issued a second time.
        self.assertGreater(int(Store.allocate(store, rows + [{'run': run}])[4:]), number)

    @budget(200)
    @given(st.lists(research | support, max_size=12, unique=True), st.lists(support, max_size=20, unique=True))
    def test_a_support_identity_stays_in_its_range_and_never_moves_research_numbering(self, existing, reserved):
        rows = [{'run': name(number)} for number in existing]
        store = SimpleNamespace(support_identities={name(number) for number in reserved})
        before = Store.allocate(store, rows)
        run = Store.allocate_support(store, rows)
        self.assertIn(int(run[4:]), SUPPORT_RANGE)
        self.assertNotIn(run, {r['run'] for r in rows} | store.support_identities)
        store.support_identities.add(run)
        self.assertEqual(Store.allocate(store, rows), before)

    def test_exhausted_ranges_are_refused_not_wrapped(self):
        store = SimpleNamespace(support_identities=set())
        with self.assertRaisesRegex(Decision, 'identity range is exhausted'):
            Store.allocate(store, [{'run': 'run-999999'}])
        with self.assertRaisesRegex(Decision, 'support identity range .* is exhausted'):
            Store.allocate_support(store, [{'run': name(number)} for number in SUPPORT_RANGE])


if __name__ == '__main__':
    unittest.main()
