"""Guest plan sequencing over prefixes, gaps and duplicate claims."""

import unittest
from hypothesis import given, strategies as st
from berman.study import Complete, Plan
from berman.files import Decision
from tests.support.properties import budget


class NextStepTests(unittest.TestCase):
    @budget(150)
    @given(st.integers(1, 12), st.data())
    def test_a_started_prefix_selects_the_next_step(self, count, data):
        plan = Plan.__new__(Plan)
        plan.id = 'test'
        plan.ids = [f'step-{i}' for i in range(count)]
        plan.steps = [{'id': step, 'recipe': 'baseline'} for step in plan.ids]
        done = data.draw(st.integers(0, count))
        prefix = plan.ids[:done]
        if done == count:
            with self.assertRaises(Complete):
                plan.next_step(prefix)
        else:
            self.assertEqual(plan.next_step(prefix), plan.steps[done])
        if done:
            with self.assertRaisesRegex(Decision, 'Conflicting'):
                plan.next_step([*prefix, prefix[0]])
        if done > 1:
            with self.assertRaisesRegex(Decision, 'out of sequence'):
                plan.next_step(prefix[1:])
