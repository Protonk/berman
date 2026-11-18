"""Any verification result is shown completely by both renderings and literally by the terminal one.

The fixtures pin particular results. These properties range over generated ones: any mix of
statuses, any optional checks, any detail lists, and summaries containing brackets, markup-like
text, escape sequences and bidirectional overrides.
"""

import io
import re
import unicodedata
import unittest

from hypothesis import given
from hypothesis import strategies as st
from rich.cells import cell_len

from berman import schemas
from berman.presentation import plain, terminal
from tests.support.properties import budget

STATUSES = ('passed', 'failed', 'not-established', 'not-run')
names = st.text(alphabet='abcdefghijklmnop-', min_size=1, max_size=10).map(lambda s: 'check-' + s)
# Visible text, including the punctuation Rich would read as markup or emoji if it interpreted any.
# Characters that occupy no cell are left out: a word nobody can see cannot be shown or missed.
seen = st.characters(categories=('L', 'N', 'P', 'S'), max_codepoint=0x2FFF).filter(lambda ch: cell_len(ch) > 0)
visible = st.text(alphabet=seen | st.just(' '), max_size=30)
hostile = st.text(
    alphabet=st.characters(categories=('Cc', 'Cf', 'Zl', 'Zp')) | st.sampled_from('[]/: ab'), min_size=1, max_size=20
)


def checks(texts):
    check = st.fixed_dictionaries(
        {
            'status': st.sampled_from(STATUSES),
            'required': st.booleans(),
            'summary': texts,
            'details': st.fixed_dictionaries({}, optional={key: st.lists(texts, max_size=13) for key in plain.LISTED}),
            'meaning': texts,
        },
        optional={'next': texts.filter(bool)},
    )
    return st.dictionaries(names, check, min_size=1, max_size=6)


@st.composite
def results(draw, texts=visible):
    found = draw(checks(texts))
    by_status = {status: [name for name, check in found.items() if check['status'] == status] for status in STATUSES}
    failed = bool(by_status['failed'])
    incomplete = any(check['required'] and check['status'] != 'passed' for check in found.values())
    return {
        'schema': 'berman.verification/1',
        'scope': 'software',
        'verdict': 'failed' if failed else 'incomplete' if incomplete else 'passed',
        'checked_at_utc': '2026-11-13T00:00:00.000000Z',
        'root': None,
        'code': '/synthetic/berman',
        'checks': found,
        'failed': by_status['failed'],
        'not_run': by_status['not-run'],
        'not_established': by_status['not-established'],
        'not_covered': draw(st.lists(texts, max_size=3)),
        'record': None,
        'meaning': 'Synthetic.',
    }


def shown(result, **options):
    out = io.StringIO()
    options = {'color_system': None, 'width': 10_000, 'height': 40, **options}
    terminal.render('verification', result, terminal.console(out, force_terminal=True, **options))
    return out.getvalue()


def words(text):
    return {word.strip('.,;:()') for word in text.split()} - {''}


class CompletenessTests(unittest.TestCase):
    @budget(150)
    @given(results())
    def test_both_renderings_state_every_check_with_its_own_status(self, result):
        self.assertEqual(schemas.problems('berman.verification/1', result), [])
        printed, displayed = plain.verification(result), shown(result)
        self.assertEqual(words(printed) - words(displayed), set())
        plain_lines, terminal_lines = printed.splitlines(), displayed.splitlines()
        for name, check in result['checks'].items():
            label = plain.LABELS[check['status']] + ('' if check['required'] else ' (optional)')
            self.assertIn(f'  {label:<28}{name}: {check["summary"]}', plain_lines)
            word, mark = terminal.STATUS[check['status']][:2]
            optional = '' if check['required'] else ' (optional)'
            self.assertIn(f'  {mark} {word:<16}{name}{optional}', [line.rstrip() for line in terminal_lines])
        # A verdict is never better than its checks, in either rendering.
        self.assertTrue(printed.startswith(f'Berman verification: {result["verdict"].upper()}'))
        self.assertTrue(displayed.startswith(f'Berman verification: {result["verdict"].upper()}'))
        for key, title in (('failed', 'Failed'), ('not_run', 'Not run'), ('not_established', 'Not established')):
            self.assertEqual(any(line.startswith(title + ': ') for line in terminal_lines), bool(result[key]))

    @budget(150)
    @given(results(texts=hostile | visible), st.sampled_from((None, 'standard')))
    def test_nothing_recorded_reaches_the_terminal_as_a_control(self, result, colors):
        displayed = shown(result, color_system=colors, width=80)
        text = displayed if colors is None else terminal_text(displayed)
        acting = sorted({ch for ch in text if ch != '\n' and unicodedata.category(ch) in terminal.HIDDEN})
        self.assertEqual(acting, [])


def terminal_text(displayed):
    """The rendering without the style sequences Rich itself wrote: ESC [ ... m, and nothing else."""
    return re.sub(r'\x1b\[[0-9;]*m', '', displayed)


if __name__ == '__main__':
    unittest.main()
