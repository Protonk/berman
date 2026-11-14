"""The opening message is one text for every fieldworker, and the documentation shows that text."""

from pathlib import Path
import re
import unittest

from berman import recipes
from berman.files import read
from berman.recovery import opening
from tests.support.paths import REPO

EXAMPLE = REPO / 'examples/minimal-study'


class OpeningMessageTests(unittest.TestCase):
    def test_the_fieldwork_guide_shows_the_message_the_code_writes(self):
        guide = (REPO / 'docs/usage.md').read_text()
        shown = re.search(r'```text\n(Berman-Fieldwork-Run: run-NNN\n.*?)```', guide, re.DOTALL).group(1)
        self.assertEqual(shown, opening('Berman-Fieldwork-Run: ', 'run-NNN', 'PROJECT', Path('PACKET'), 'NAME', 'HEAD'))

    def test_only_the_run_paths_project_name_and_commit_vary(self):
        one = opening(
            'Berman-Fieldwork-Run: ', 'run-019', '/work/stewart', Path('/packets/run-019'), 'Stewart', 'a' * 40
        )
        two = opening(
            'Berman-Claude-Fieldwork-Run: ', 'run-020', '/work/orchard', Path('/packets/run-020'), 'Orchard', 'b' * 40
        )
        for old, new in (
            ('Berman-Fieldwork-Run: ', 'Berman-Claude-Fieldwork-Run: '),
            ('run-019', 'run-020'),
            ('/work/stewart', '/work/orchard'),
            ('Stewart', 'Orchard'),
            ('a' * 40, 'b' * 40),
        ):
            one = one.replace(old, new)
        self.assertEqual(one, two)
        self.assertEqual(two.count('\n\n'), 3, 'a marker line and three paragraphs')

    def test_a_support_trial_names_no_project_or_commit(self):
        message = opening('Berman-Fieldwork-Run: ', 'run-900001', '/tmp/disposable', Path('/packets/run-900001'))
        self.assertEqual(message.split('\n\n')[1], 'Your working directory is `/tmp/disposable`.')
        self.assertNotIn('commit', message)
        self.assertIn('`/packets/run-900001/INSTRUCTIONS.md`', message)


class BaselineRecipeTests(unittest.TestCase):
    """The example a new study starts from is a recipe this version accepts."""

    def test_the_example_holds_one_assignable_baseline(self):
        meta = read(EXAMPLE / 'recipes/baseline/recipe.json')
        plan = read(EXAMPLE / 'plans/example/plan.json')
        self.assertEqual([step['recipe'] for step in plan['steps']], [meta['id']])
        self.assertEqual((meta['id'], meta['recording_protocol']), ('baseline', 'work-accounts/1'))
        self.assertIn(meta['recording_protocol'], recipes.PROTOCOLS)
        self.assertFalse(set(recipes.RETIRED) & set(meta))
        self.assertEqual(
            sorted(p.name for p in (EXAMPLE / 'recipes/baseline').iterdir()), ['INSTRUCTIONS.md', 'recipe.json']
        )
        self.assertEqual(
            (EXAMPLE / 'recipes/baseline' / meta['instructions']).read_text().count('{{notes_directory}}'), 1
        )


if __name__ == '__main__':
    unittest.main()
