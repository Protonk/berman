"""Renderings for an interactive terminal, with Rich. The only module that imports it.

The same results as `plain`, laid out so that an operator can tell at a glance what passed, what
failed, what was never run, what is known and what is not, and what is unsettled.

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
from rich.table import Table
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
FACTS = ('registered', 'capture', 'finalized', 'ending', 'migration', 'packaged')
HEADING, ATTENTION, CAUTION, QUIET = 'bold', 'bold red', 'yellow', 'dim'
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
    if result['record']:
        screen.line('Recorded in ', (result['record']['directory'], HEADING))


def fact_value(value):
    """(word, style) for one lifecycle fact: established, established as absent, or unknown."""
    if value is None:
        return 'unknown', 'bold yellow'
    if value is True:
        return 'yes', 'green'
    if value is False:
        return 'no', None
    return str(value), 'cyan'


def facts(row):
    """Each lifecycle fact with what establishes it, or why it is unknown."""
    table = Table(box=None, padding=(0, 2, 0, 0), show_header=True, header_style=QUIET, pad_edge=False)
    table.add_column('fact', overflow='fold')
    table.add_column('value', overflow='fold')
    table.add_column('evidence, or why it is unknown', overflow='fold')
    for name in FACTS:
        fact = row['lifecycle'][name]
        word, style = fact_value(fact['value'])
        basis = fact['evidence'] if fact['value'] is not None else fact.get('unknown', [])
        table.add_row(text(name), text((word, style)), text('; '.join(basis)))
    return table


def status(screen, result):
    screen.line(f'{result["archived"]} archived runs. Default connection: ', (result['default_connection'], HEADING))
    for problem in result['workspace_problems']:
        screen.line(('Workspace needs attention: ', ATTENTION), problem)
    for name, row in result['connections'].items():
        screen.line((name, HEADING), ': ', (plain.plan(row), CAUTION if row.get('plan_error') else None))
        for problem in row['problems']:
            screen.line(('needs attention: ', ATTENTION), problem, depth=2)
    for row in result['pending_requests']:
        screen.line((plain.request(row), CAUTION))
    if not result['runs']:
        screen.line('No outstanding runs.')
    for row in result['runs']:
        screen.gap()
        screen.line((row['run'], HEADING), f' ({row["connection"]}): ', row['summary'])
        screen.line(plain.assignment(row), depth=2)
        if row['last_capture']:
            screen.line(f'Last capture at {row["last_capture"]}', depth=2)
        if screen.console.width < 70:
            for name in FACTS:
                fact = row['lifecycle'][name]
                screen.line(name, ': ', fact_value(fact['value']), depth=2)
                basis = fact['evidence'] if fact['value'] is not None else fact.get('unknown', [])
                for item in basis:
                    screen.line(item, depth=4)
        else:
            screen.console.print(indented(facts(row), 2))
        for line in row['lifecycle']['disagreements']:
            screen.line((line, CAUTION), depth=2)
        for line in row['problems']:
            screen.line((line, ATTENTION), depth=2)
    for row in result['support_trials']:
        screen.gap()
        screen.line((row['run'], HEADING), plain.trial(row).removeprefix(row['run']))
        for problem in row['problems']:
            screen.line((problem, ATTENTION), depth=2)
    if result['unsettled']:
        screen.gap()
        screen.line(('Unsettled:', 'bold yellow'))
        for item in result['unsettled']:
            screen.line(item, depth=2)
    screen.gap()
    screen.line((result['meaning'], QUIET))


RENDERERS = {'status': status, 'verification': verification}


def render(kind, result, target):
    """Print one command's result to a console made by console()."""
    RENDERERS[kind](Screen(target), result)
