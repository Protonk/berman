"""Renderings for an interactive terminal, with Rich. The only module that imports it.

The same results as `plain`, laid out so that an operator can tell at a glance what passed, what
failed and what the software verification covered.

Two rules hold throughout:

- A distinction is carried by a word and a mark, and color is added on top. Output stays
  unambiguous without color (NO_COLOR) and where the marks cannot be drawn.
- Recorded text is shown literally. No markup is interpreted, and control characters are made
  visible, so a reason or path taken from a record cannot restyle or rewrite the screen.

Nothing here decides anything: every word about state comes from the result it is given.
"""

import unicodedata

from rich.console import Console
from rich.padding import Padding
from rich.text import Text

from berman.presentation import plain

# status -> (word, mark, plain mark, style). The words are the ones the plain text uses.
STATUS = {
    'passed': (plain.LABELS['passed'], '✔', '+', 'green'),
    'failed': (plain.LABELS['failed'], '✖', 'x', 'bold red'),
    'not-run': (plain.LABELS['not-run'], '○', '-', 'cyan'),
    'not-established': (plain.LABELS['not-established'], '?', '?', 'yellow'),
}
VERDICT = {'passed': 'bold green', 'failed': 'bold red', 'incomplete': 'bold yellow'}
HEADING, QUIET = 'bold', 'dim'
HIDDEN = ('Cc', 'Cf', 'Cs', 'Co', 'Cn', 'Zl', 'Zp')  # Unicode categories never passed through to a terminal.


def console(stream, **options):
    """A console that interprets nothing in the text it is given."""
    return Console(file=stream, markup=False, highlight=False, emoji=False, **options)


def literal(value):
    """A recorded value as text to show: what could act on the terminal is made visible instead.

    Control and format characters (escape sequences, carriage returns, bidirectional overrides),
    line and paragraph separators, and unassigned or private code points are written as escapes.
    """
    text = value if isinstance(value, str) else str(value)
    return ''.join(
        ch if ch == '\n' or unicodedata.category(ch) not in HIDDEN else ch.encode('unicode_escape').decode()
        for ch in text
    )


def text(*parts):
    """Text from (value, style) pairs or bare values; every value is shown literally."""
    made = Text()
    for part in parts:
        value, style = part if isinstance(part, tuple) else (part, None)
        made.append(literal(value), style=style)
    return made


def indented(renderable, depth):
    return Padding(renderable, (0, 0, 0, depth), expand=False)


class Screen:
    """One command's output: lines of prose that wrap to the terminal."""

    def __init__(self, target):
        self.console = target
        self.ascii = target.options.ascii_only

    def line(self, *parts, depth=0):
        self.console.print(indented(text(*parts), depth))

    def gap(self):
        self.console.print()

    def mark(self, status):
        word, mark, fallback, style = STATUS[status]
        return word, (fallback if self.ascii else mark), style


def verification(screen, result):
    screen.line(('Berman verification: ', HEADING), (result['verdict'].upper(), VERDICT[result['verdict']]))
    for name, check in result['checks'].items():
        word, mark, style = screen.mark(check['status'])
        optional = [] if check['required'] else [(' (optional)', QUIET)]
        screen.line((f'{mark} {word:<16}', style), (name, HEADING), *optional, depth=2)
        screen.line(check['summary'], depth=21)
        for prefix, item in plain.listed(check):
            screen.line(((prefix or ''), QUIET), item, depth=23)
        if check.get('next'):
            screen.console.print(text(' ' * 21, ('next: ', QUIET), check['next']), soft_wrap=True)
    screen.gap()
    for key, title, status in (
        ('failed', 'Failed', 'failed'),
        ('not_run', 'Not run', 'not-run'),
        ('not_established', 'Not established', 'not-established'),
    ):
        if result[key]:
            screen.line((title + ': ', screen.mark(status)[2]), plain.named(result, result[key]))
    screen.line(('Never checked by berman verify:', HEADING))
    for item in result['not_covered']:
        screen.line(item, depth=2)


RENDERERS = {'verification': verification}


def render(kind, result, target):
    """Print one command's result to a console made by console()."""
    RENDERERS[kind](Screen(target), result)
