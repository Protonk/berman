"""Presentation: redirected text pinned, terminal text complete and literal, machine output untouched.

tests/fixtures/presentation holds synthetic command results as JSON, and beside each the
deliberately pinned plain text. That text is the contract for redirected output. The terminal
rendering is free to lay the same results out differently, so it is held to a different standard:
it shows every word the plain text shows, keeps each distinction legible without color, and
interprets nothing in the recorded text it displays.
"""

import contextlib
import builtins
import io
import json
import os
import pty
import re
import select
import subprocess
import sys
import unittest
from unittest import mock

from berman import cli, presentation
from berman.presentation import plain, terminal
from tests.support.paths import REPO

FIXTURES = REPO / 'tests/fixtures/presentation'
CASES = {path.stem: json.loads(path.read_text()) for path in sorted(FIXTURES.glob('*.json'))}
ESCAPE = '\x1b'
COLOR = re.compile(r'\x1b\[(?:3\d|9\d|4\d|38|48)')  # A foreground or background color, as opposed to bold or dim.
TERMINAL = {'TERM': 'xterm-256color', 'COLUMNS': '120', 'LINES': '40'}
PLAIN = {'BERMAN_OUTPUT': 'plain'}  # The operator's request for plain text on a terminal.


def kind(name):
    return name.partition('-')[0]


def shown(name_or_kind, result, **options):
    """The terminal rendering as text; colorless and unwrapped unless options say otherwise."""
    out = io.StringIO()
    options = {'color_system': None, 'width': 10_000, 'height': 40, **options}
    terminal.render(kind(name_or_kind), result, terminal.console(out, force_terminal=True, **options))
    return out.getvalue()


def words(text):
    """The words of a rendering, without the punctuation that joins them into sentences."""
    return {word.strip('.,;:()') for word in text.split()} - {''}


class Terminal(io.StringIO):
    def isatty(self):
        return True


SESSION = ('NO_COLOR', 'FORCE_COLOR', 'TERM', 'COLUMNS', 'LINES', 'BERMAN_OUTPUT')


def environment(**changes):
    """os.environ as an interactive terminal session has it: no NO_COLOR, no forced color, no rendering chosen."""
    kept = {k: v for k, v in os.environ.items() if k not in SESSION}
    return mock.patch.dict(os.environ, {**kept, **TERMINAL, **changes}, clear=True)


@contextlib.contextmanager
def unimportable(*names):
    """Make the named modules fail to import, as in a damaged environment, then restore them."""
    attributes = {}
    for name in names:
        parent, _, child = name.rpartition('.')
        if parent in sys.modules and hasattr(sys.modules[parent], child):
            attributes[name] = getattr(sys.modules[parent], child)
            delattr(sys.modules[parent], child)
    try:
        with mock.patch.dict(sys.modules, dict.fromkeys(names)):
            yield
    finally:
        for name, value in attributes.items():
            parent, _, child = name.rpartition('.')
            setattr(sys.modules[parent], child, value)


class PlainTests(unittest.TestCase):
    def test_non_import_error_while_loading_rich_still_shows_the_result(self):
        original = builtins.__import__

        def broken(name, *args, **kwargs):
            if name == 'berman.presentation':
                raise RuntimeError('synthetic damaged dependency')
            return original(name, *args, **kwargs)

        result = CASES['verification-failed']
        out = Terminal()
        with environment(), mock.patch('builtins.__import__', side_effect=broken):
            presentation.show('verification', result, out)
        self.assertEqual(out.getvalue(), plain.verification(result) + '\n')

    def test_plain_text_matches_the_deliberate_redirected_output_contract(self):
        self.assertEqual(len(CASES), 2)
        self.assertEqual({kind(name) for name in CASES}, set(presentation.KINDS))
        for name, result in CASES.items():
            with self.subTest(case=name):
                self.assertEqual(getattr(plain, kind(name))(result) + '\n', (FIXTURES / f'{name}.txt').read_text())

    def test_anything_but_an_interactive_terminal_receives_the_plain_text(self):
        for name, result in CASES.items():
            expected = (FIXTURES / f'{name}.txt').read_text()
            with self.subTest(case=name, stream='pipe'), environment():
                out = io.StringIO()
                presentation.show(kind(name), result, out)
                self.assertEqual(out.getvalue(), expected)
            for term in ('dumb', 'unknown'):
                with self.subTest(case=name, stream=term), environment(TERM=term):
                    out = Terminal()
                    presentation.show(kind(name), result, out)
                    self.assertEqual(out.getvalue(), expected)

    def test_an_interactive_terminal_receives_the_rich_rendering_unless_plain_is_asked_for(self):
        for name, result in CASES.items():
            expected = (FIXTURES / f'{name}.txt').read_text()
            for setting in ({}, {'BERMAN_OUTPUT': ''}, {'BERMAN_OUTPUT': 'rich'}):
                with self.subTest(case=name, setting=setting), environment(**setting):
                    out = Terminal()
                    presentation.show(kind(name), result, out)
                    self.assertNotEqual(out.getvalue(), expected)
                    self.assertIn(ESCAPE, out.getvalue())
            with self.subTest(case=name, setting=PLAIN), environment(**PLAIN):
                out = Terminal()
                presentation.show(kind(name), result, out)
                self.assertEqual(out.getvalue(), expected)

    def test_a_terminal_without_rich_still_gets_the_plain_text(self):
        # Found in review: with rich unimportable, a terminal got a traceback where a pipe got the report.
        expected = (FIXTURES / 'verification-failed.txt').read_text()
        with environment(), unimportable('berman.presentation.terminal', 'rich'):
            out = Terminal()
            presentation.show('verification', CASES['verification-failed'], out)
        self.assertEqual(out.getvalue(), expected)

    def test_a_result_without_a_presentation_is_refused_not_guessed(self):
        with self.assertRaisesRegex(ValueError, 'No presentation is defined for finish'):
            presentation.show('finish', {})


class TerminalTests(unittest.TestCase):
    def test_every_word_of_the_plain_text_is_shown(self):
        for name, result in CASES.items():
            with self.subTest(case=name):
                missing = words((FIXTURES / f'{name}.txt').read_text()) - words(shown(name, result))
                self.assertEqual(missing, set())

    def test_color_is_added_on_top_and_never_needed(self):
        for name, result in CASES.items():
            with self.subTest(case=name):
                colorless = shown(name, result)
                self.assertNotIn(ESCAPE, colorless)
                colored = shown(name, result, color_system='standard')
                self.assertIn(ESCAPE, colored)
                self.assertEqual(re.sub(r'\x1b\[[0-9;]*m', '', colored), colorless)
                # NO_COLOR keeps emphasis and drops color.
                quiet = shown(name, result, color_system='standard', no_color=True)
                self.assertIsNone(COLOR.search(quiet))
                self.assertEqual(re.sub(r'\x1b\[[0-9;]*m', '', quiet), colorless)

    def test_the_four_check_statuses_are_told_apart_by_word_and_mark(self):
        self.assertEqual(set(terminal.STATUS), {'passed', 'failed', 'not-run', 'not-established'})
        for position in (0, 1, 2):  # Word, mark, and the mark used where the first cannot be drawn.
            values = [entry[position] for entry in terminal.STATUS.values()]
            self.assertEqual(len(set(values)), 4, values)
        result = CASES['verification-failed']
        lines = shown('verification', result).splitlines()
        for name, check in result['checks'].items():
            word, mark = terminal.STATUS[check['status']][:2]
            with self.subTest(check=name):
                line = next(line for line in lines if f' {name}' in line and line.strip().startswith(mark))
                self.assertIn(f'{mark} {word}', line)
                self.assertEqual('(optional)' in line, not check['required'])
        text = shown('verification', result)
        self.assertIn('Berman verification: FAILED', text)
        self.assertIn('Failed: dependencies, schemas', text)

    def test_next_steps_are_printed_whole_however_narrow_the_terminal(self):
        steps = [check['next'] for check in CASES['verification-failed']['checks'].values() if check.get('next')]
        self.assertGreaterEqual(len(steps), 2)
        verification = shown('verification', CASES['verification-failed'], width=50).splitlines()
        for step in steps:
            self.assertTrue(any(line.strip() == 'next: ' + step for line in verification), step)
        prose = [line for line in verification if not line.strip().startswith('next: ')]
        self.assertLessEqual(max(len(line) for line in prose), 50)

    def test_recorded_text_is_shown_literally(self):
        text = shown('verification', CASES['verification-failed'], color_system='standard')
        self.assertIn('[bold]missing[/bold]', text)
        hostile = json.loads(json.dumps(CASES['verification-software']))
        hostile['checks']['installation']['summary'] = (
            'clear \x1b[2J\x1b[31m bell \x07 back \x08 :smile: [red]x[/red] nul \x00'
        )
        hostile['not_covered'] = ['carriage\rreturn', 'tab\there']
        text = shown('verification', hostile)
        self.assertEqual(sorted(set(ch for ch in text if not ch.isprintable() and ch != '\n')), [])
        self.assertIn('clear \\x1b[2J\\x1b[31m bell \\x07 back \\x08 :smile: [red]x[/red] nul \\x00', text)
        self.assertIn('carriage\\rreturn', text)
        self.assertIn('tab\\there', text)

    def test_marks_fall_back_where_they_cannot_be_drawn(self):
        raw = io.BytesIO()
        stream = io.TextIOWrapper(raw, encoding='ascii', errors='strict')
        target = terminal.console(stream, force_terminal=True, color_system=None, width=200)
        self.assertTrue(target.options.ascii_only)
        terminal.render('verification', CASES['verification-failed'], target)
        stream.flush()
        text = raw.getvalue().decode('ascii')
        for state in ('passed', 'failed'):
            word, _, fallback, _ = terminal.STATUS[state]
            self.assertIn(f'{fallback} {word}', text)


class CommandLineTests(unittest.TestCase):
    def run_cli(self, *words, stream=None, **env):
        out, err = stream or io.StringIO(), io.StringIO()
        with environment(**env), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(words))
        return code, out.getvalue(), err.getvalue()

    def test_json_output_does_not_depend_on_the_stream_or_the_setting(self):
        piped = self.run_cli('--json', 'verify', '--software')
        for setting in ({}, PLAIN):
            with self.subTest(setting=setting):
                on_terminal = self.run_cli('--json', 'verify', '--software', stream=Terminal(), **setting)
                self.assertEqual((piped[0], on_terminal[0]), (0, 0))
                self.assertNotIn(ESCAPE, on_terminal[1])
                first, second = json.loads(piped[1]), json.loads(on_terminal[1])
                first.pop('checked_at_utc'), second.pop('checked_at_utc')
                self.assertEqual(first, second)
                self.assertEqual(on_terminal[1], json.dumps(json.loads(on_terminal[1]), indent=2) + '\n')

    def test_a_terminal_changes_the_presentation_and_nothing_else(self):
        piped = self.run_cli('verify', '--software')
        self.assertEqual(piped[0], 0)
        self.assertNotIn(ESCAPE, piped[1])
        self.assertTrue(
            piped[1].startswith('Berman verification: PASSED\n  passed                      installation: ')
        )
        styled = self.run_cli('verify', '--software', stream=Terminal())
        self.assertEqual(styled[0], 0)
        self.assertIn(ESCAPE, styled[1])
        self.assertIn('✔ passed', styled[1])
        # Plain text when it is asked for, and wherever styled text could not be shown.
        self.assertEqual(self.run_cli('verify', '--software', stream=Terminal(), **PLAIN), piped)
        self.assertEqual(self.run_cli('verify', '--software', stream=Terminal(), TERM='dumb'), piped)
        self.assertEqual(self.run_cli('verify', '--software', **PLAIN), piped)
        uncolored = self.run_cli('verify', '--software', stream=Terminal(), NO_COLOR='1')
        self.assertIsNone(COLOR.search(uncolored[1]))
        self.assertIn('✔ passed', uncolored[1])

    def test_refusals_keep_their_exit_code_and_stream_on_a_terminal(self):
        for fresh, setting in ((io.StringIO, {}), (Terminal, {}), (Terminal, PLAIN)):
            with self.subTest(stream=fresh.__name__, setting=setting):
                code, out, err = self.run_cli(
                    'recording', 'verify', str(REPO / 'tests/missing.zip'), stream=fresh(), **setting
                )
                self.assertEqual((code, out), (2, ''))
                self.assertTrue(err.startswith('Berman needs attention: '), err)
                self.assertNotIn(ESCAPE, err)

    def test_a_damaged_environment_is_reported_the_same_way_on_a_terminal_and_in_a_pipe(self):
        # Found in review: with a dependency unimportable, the command meant to diagnose the
        # environment raised, and whether another command failed depended on the terminal.
        from berman import schemas

        self.addCleanup(schemas.validator.cache_clear)
        for fresh, setting in ((io.StringIO, {}), (Terminal, {})):  # A new stream for every command.
            with self.subTest(missing='rich', stream=fresh.__name__):
                with unimportable('berman.presentation.terminal', 'rich'):
                    code, out, err = self.run_cli('verify', '--software', stream=fresh(), **setting)
                self.assertEqual((code, err), (1, ''))
                self.assertIn('  FAILED                      dependencies: 1 runtime dependency problem(s)', out)
                self.assertIn('rich: ', out)
            with self.subTest(missing='jsonschema', stream=fresh.__name__):
                schemas.validator.cache_clear()
                with unimportable('jsonschema'):
                    code, out, err = self.run_cli('verify', '--software', stream=fresh(), **setting)
                    self.assertEqual((code, err), (1, ''))
                    # Rich itself still imports, so a terminal gets its rendering.
                    self.assertIn('Packaged schema berman.connection/1 cannot be used', out)
                    self.assertIn('jsonschema: ', out)
                schemas.validator.cache_clear()


class PseudoTerminalTests(unittest.TestCase):
    """The installed command on a real pseudo-terminal, as an operator's shell runs it."""

    def run_on_terminal(self, *words, **env):
        primary, secondary = pty.openpty()
        environ = {k: v for k, v in os.environ.items() if k not in SESSION}
        environ.update(TERMINAL, **env)
        process = subprocess.Popen(
            [sys.executable, '-I', '-B', '-m', 'berman', *words],
            stdout=secondary,
            stderr=subprocess.PIPE,
            env=environ,
            cwd=REPO / 'tests',
        )
        os.close(secondary)
        chunks = []
        while True:
            ready, _, _ = select.select([primary], [], [], 30)
            if not ready:
                break
            try:
                data = os.read(primary, 65536)
            except OSError:  # The terminal closes when the command exits.
                break
            if not data:
                break
            chunks.append(data)
        os.close(primary)
        with process.stderr:
            errors = process.stderr.read().decode()
        return process.wait(timeout=30), b''.join(chunks).decode().replace('\r\n', '\n'), errors

    def test_the_command_styles_a_terminal_and_keeps_its_exit_code(self):
        piped = subprocess.run(
            [sys.executable, '-I', '-B', '-m', 'berman', 'verify', '--software'],
            capture_output=True,
            text=True,
            cwd=REPO / 'tests',
        )
        self.assertEqual(piped.returncode, 0, piped.stderr)
        self.assertNotIn(ESCAPE, piped.stdout)
        code, text, err = self.run_on_terminal('verify', '--software')
        self.assertEqual((code, err), (0, ''))
        self.assertIn(ESCAPE, text)
        self.assertIn('✔ passed', text)
        for name in ('installation', 'dependencies', 'schemas'):
            self.assertIn(name, text)
        # Asked for plain text, or unable to show styled text, a terminal receives what a pipe receives.
        code, text, err = self.run_on_terminal('verify', '--software', **PLAIN)
        self.assertEqual((code, text, err), (0, piped.stdout, ''))
        code, text, err = self.run_on_terminal('verify', '--software', TERM='dumb')
        self.assertEqual((code, text, err), (0, piped.stdout, ''))
        code, text, err = self.run_on_terminal('--json', 'verify', '--software')
        self.assertEqual((code, err), (0, ''))
        self.assertNotIn(ESCAPE, text)
        self.assertEqual(json.loads(text)['verdict'], 'passed')
