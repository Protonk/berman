"""Renderings for an interactive terminal, with Rich. The only module that imports it.

The same results as `plain`, laid out so that an operator can tell at a glance what passed, what
failed, what was never run, what is known and what is not, and which commands can be typed now.

Three rules hold throughout:

- A distinction is carried by a word and a mark, and color is added on top. Output stays
  unambiguous without color (NO_COLOR) and where the marks cannot be drawn.
- Recorded text is shown literally. No markup is interpreted, and control characters are made
  visible, so a reason or path taken from a record cannot restyle or rewrite the screen.
- A command is printed on a line of its own that Rich does not wrap, so it copies intact.

Nothing here decides anything: every word about state comes from the result it is given.
"""

import shlex
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
HEADING, COMMAND, ATTENTION, CAUTION, QUIET = 'bold', 'bold cyan', 'bold red', 'yellow', 'dim'
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


def shell_command(value):
    """Keep generated commands copyable without emitting terminal controls.

    Commands are shell-quoted argument vectors, not arbitrary shell programs. Only a command
    containing hidden characters needs requoting. ANSI-C quotes (Bash, Zsh and POSIX.1-2024)
    preserve those argument bytes, unlike the literal escapes used in explanatory prose.
    """
    if not any(unicodedata.category(ch) in HIDDEN for ch in value):
        return value
    arguments = []
    for argument in shlex.split(value):
        if not any(unicodedata.category(ch) in HIDDEN for ch in argument):
            arguments.append(shlex.quote(argument))
            continue
        encoded = ''.join(
            '\\' + ch
            if ch in "\\'"
            else ''.join(f'\\{byte:03o}' for byte in ch.encode('utf-8', 'surrogateescape'))
            if unicodedata.category(ch) in HIDDEN
            else ch
            for ch in argument
        )
        arguments.append("$'" + encoded + "'")
    return ' '.join(arguments)


def indented(renderable, depth):
    return Padding(renderable, (0, 0, 0, depth), expand=False)


class Screen:
    """One command's output: lines of prose that wrap to the terminal, and commands that never wrap."""

    def __init__(self, target):
        self.console = target
        self.ascii = target.options.ascii_only

    def line(self, *parts, depth=0):
        self.console.print(indented(text(*parts), depth))

    def command(self, value, depth, note=None):
        # soft_wrap leaves wrapping to the terminal, which inserts no line break into a copied command.
        parts = [' ' * depth, (shell_command(value), COMMAND)] + ([(note, QUIET)] if note else [])
        self.console.print(text(*parts), soft_wrap=True)

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
        busy = [('; an operation is in progress', CAUTION)] if row['operation_in_progress'] else []
        screen.line((name, HEADING), ': next planned fieldwork role: ', row['next_recipe'] or 'none', *busy)
        if row.get('plan_error'):
            screen.line(('plan needs attention: ', CAUTION), row['plan_error'], depth=2)
        if not row['next_recipe'] and row.get('next_note'):
            screen.line(row['next_note'], depth=2)
        for problem in row['blocked_by']:
            screen.line(('needs attention: ', ATTENTION), problem, depth=2)
        for path in row['pending_requests']:
            screen.line(('pending combined request: ', CAUTION), path, depth=2)
    if not result['runs']:
        screen.line('No outstanding engagements or preparations.')
    for row in result['runs']:
        screen.gap()
        screen.line((row['run'], HEADING), f' ({row["connection"]}): ', row['summary'])
        screen.line(plain.assignment(row), depth=2)
        if row['last_capture']:
            screen.line(f'Capture at {row["last_capture"]}; consistent: {row["capture_consistent"]}', depth=2)
        if screen.console.width < 70:
            for name in FACTS:
                fact = row['lifecycle'][name]
                screen.line(name, ': ', fact_value(fact['value']), depth=2)
                basis = fact['evidence'] if fact['value'] is not None else fact.get('unknown', [])
                for item in basis:
                    screen.line(item, depth=4)
        else:
            screen.console.print(indented(facts(row), 2))
        for line in row['lifecycle']['disagreements'] + row['warnings']:
            screen.line((line, CAUTION), depth=2)
        for line in row['problems']:
            screen.line((line, ATTENTION), depth=2)
    for trial in result['support_trials']:
        screen.gap()
        screen.line(
            (trial['run'], HEADING),
            f' (support trial, {trial["adapter"]}; not research): {trial["phase"]}; capture: {trial["capture_state"]}',
        )
        screen.console.print(text('  ', ('next: ', QUIET), trial['next']), soft_wrap=True)
        for problem in trial['problems']:
            screen.line((problem, ATTENTION), depth=2)


def cheatsheet(screen, result):
    for problem in result['workspace_problems']:
        screen.line(('Workspace needs attention: ', ATTENTION), problem)
    for conn in result['connections']:
        default = ', default connection)' if conn['default'] else ')'
        screen.line((conn['name'], HEADING), f' ({conn["id"]}{default}')
        screen.line('checkout: ', conn['checkout'], depth=2)
        screen.gap()
        development(screen, conn)
        screen.gap()
        fieldwork(screen, conn)
        screen.gap()
    screen.line((result['meaning'], QUIET))


def development(screen, conn):
    title = 'Ordinary development'
    screen.line((title, HEADING), ' (no fieldwork record, no research-plan step; works from any directory):', depth=2)
    if not conn['development']:
        screen.line('no development launcher is configured; use the harness directly from the checkout', depth=4)
        return
    for dev in conn['development']:
        missing = None if dev['available'] else '    (launcher missing or not executable: ' + dev['launcher'] + ')'
        screen.command(dev['command'], 4, missing)
    screen.line('after --: --model MODEL, --effort LEVEL, one quoted opening prompt', depth=4)
    screen.line(
        'Codex automatically rechecks changed installations before starting; '
        'explicit checks exit without starting development (Claude uses a model session):',
        depth=4,
    )
    for dev in conn['development']:
        screen.command(dev['command'] + ' --check', 6)


def fieldwork(screen, conn):
    """A connection's fieldwork, grouped by whether a command can be typed now or a decision comes first."""
    commands, found = conn['commands'], conn['fieldwork']
    recipe_choice = not conn['next_recipe'] and not (
        found['ready'] or found['incomplete'] or conn.get('pending_requests')
    )
    screen.line(('Fieldwork', HEADING), ':', depth=2)
    if conn.get('plan_error'):
        screen.line(('plan needs attention: ', CAUTION), conn['plan_error'], depth=4)
    for path in conn.get('pending_requests', []):
        screen.line(('pending combined request: ', CAUTION), path, depth=4)
    if conn.get('pending_requests'):
        screen.line('resume the recorded request with:', depth=4)
        screen.command(commands['new'], 6)
    if conn['operation_in_progress']:
        screen.line(('an operation is in progress on this connection; wait for it, then ', CAUTION), depth=4)
        screen.command(commands['status'], 6)
    if found['attention'] or found['ending']:
        screen.line(('Needs attention', ATTENTION), depth=4)
        for item in found['attention']:
            screen.line(f'{item["run"]} needs attention: ', item['problem'], '; see', depth=6)
            screen.command(commands['status'], 8)
        for run in found['ending']:
            screen.line(f'{run} has a declared ending in progress; this shows its state:', depth=6)
            screen.command(commands['status'], 8)
    nothing = not any(found.values()) and not conn.get('pending_requests')
    if found['ready'] or found['incomplete'] or (nothing and conn['next_recipe']):
        screen.line(('Available now', 'bold green'), depth=4)
        for run, command in zip(found['ready'], commands['launch'], strict=True):
            screen.line(f'{run} is prepared and unstarted; start it with:', depth=6)
            screen.command(command, 8)
        for run in found['incomplete']:
            screen.line(f'{run} has an incomplete preparation; complete it with:', depth=6)
            screen.command(commands['new'], 8)
        if nothing and conn['next_recipe']:
            screen.line(
                f'nothing outstanding; the next agreed role is {conn["next_recipe"]}. Prepare it with:', depth=6
            )
            screen.command(commands['new'], 8)
            screen.line('then start it with the launch command that new prints', depth=6)
    if found['open'] or (nothing and not conn['next_recipe']):
        heading = 'Recorded request' if conn.get('pending_requests') else "Needs the researcher's decision"
        screen.line((heading, 'bold yellow'), depth=4)
        for run, finish, supplement in zip(found['open'], commands['finish'], commands['supplement'], strict=True):
            if conn.get('pending_requests'):
                screen.line(f'{run} is an open engagement; resume the recorded request before another choice.', depth=6)
            else:
                screen.line(f'{run} is an open engagement. When the researcher says it has ended:', depth=6)
                screen.command(finish, 8)
                screen.line(
                    'or end it while preparing the next fieldworker'
                    + (' (choose a recipe; replace RECIPE):' if recipe_choice else ':'),
                    depth=6,
                )
                screen.command(commands['finish_previous'], 8)
                screen.line(
                    'or keep it open while preparing another'
                    + (' (choose a recipe; replace RECIPE):' if recipe_choice else ':'),
                    depth=6,
                )
                screen.command(commands['keep_open'], 8)
            screen.line('retain later activity without ending it:', depth=6)
            screen.command(supplement, 8)
        if nothing and not conn['next_recipe']:
            screen.line('nothing outstanding; ', conn['next_note'] or 'no next role', depth=6)
    if not nothing and conn['next_recipe'] and not found['ready'] and not found['incomplete']:
        screen.line(f'the next agreed role after these is {conn["next_recipe"]}', depth=4)
    if commands.get('new_direct') and not (found['ready'] or found['incomplete'] or conn.get('pending_requests')):
        screen.line(('Direct assignment template (choose a recipe; replace RECIPE):', CAUTION), depth=4)
        screen.command(commands['new_direct'], 6)


def prepared(screen, result):
    for row in result['preserved']:
        screen.line('Preserved ', (row['run'], HEADING), ': ', row['package']['path'])
    checks = (
        'Prior native checks reused; configuration unchanged.'
        if result['checks_reused']
        else 'Native checks passed in this preparation.'
    )
    screen.line(
        (result['run'], HEADING),
        f' ({result["connection"]}) ',
        ('ready', 'bold green'),
        f' using {result["recipe"]}. {checks}',
    )
    screen.line(plain.assignment(result))
    for warning in result.get('warnings', []):
        screen.line(('Warning: ', CAUTION), warning)
    screen.line('Launch when ready:')
    screen.gap()
    screen.command(result['launch'], 0)


RENDERERS = {'status': status, 'cheatsheet': cheatsheet, 'verification': verification, 'prepared': prepared}


def render(kind, result, target):
    """Print one command's result to a console made by console()."""
    if kind in ('finished', 'ready'):
        target.print(getattr(plain, kind)(result), markup=False, highlight=False)
    else:
        RENDERERS[kind](Screen(target), result)
