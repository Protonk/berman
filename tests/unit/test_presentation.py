"""Presentation: redirected text unchanged, terminal text complete and literal, machine output untouched.

tests/fixtures/presentation holds synthetic command results as JSON, and beside each the text
the 0.4.1 renderers printed for it. That text is the contract for redirected output. The
terminal rendering is free to lay the same results out differently, so it is held to a different
standard: it shows every word the plain text shows, keeps each distinction legible without
color, and interprets nothing in the recorded text it displays.
"""

import contextlib
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


def kind(name):
    return name.partition('-')[0]


def shown(name_or_kind, result, **options):
    """The terminal rendering as text; colorless and unwrapped unless options say otherwise."""
    out = io.StringIO()
    options = {'color_system': None, 'width': 10_000, **options}
    terminal.render(kind(name_or_kind), result, terminal.console(out, force_terminal=True, **options))
    return out.getvalue()


def words(text):
    """The words of a rendering, without the punctuation that joins them into sentences."""
    return {word.strip('.,;:()') for word in text.split()} - {''}


class Terminal(io.StringIO):
    def isatty(self):
        return True


def environment(**changes):
    """os.environ as an interactive terminal session has it, without NO_COLOR or a forced color."""
    kept = {k: v for k, v in os.environ.items() if k not in ('NO_COLOR', 'FORCE_COLOR', 'TERM', 'COLUMNS', 'LINES')}
    return mock.patch.dict(os.environ, {**kept, **TERMINAL, **changes}, clear=True)


class PlainTests(unittest.TestCase):
    def test_plain_text_is_what_version_0_4_printed(self):
        self.assertGreaterEqual(len(CASES), 9)
        for name, result in CASES.items():
            with self.subTest(case=name):
                self.assertEqual(getattr(plain, kind(name))(result) + '\n', (FIXTURES / f'{name}.txt').read_text())

    def test_anything_but_an_interactive_terminal_receives_the_plain_text(self):
        for name, result in CASES.items():
            expected = (FIXTURES / f'{name}.txt').read_text()
            with self.subTest(case=name, stream='pipe'):
                out = io.StringIO()
                presentation.show(kind(name), result, out)
                self.assertEqual(out.getvalue(), expected)
            for term in ('dumb', 'unknown'):
                with self.subTest(case=name, stream=term), environment(TERM=term):
                    out = Terminal()
                    presentation.show(kind(name), result, out)
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
        self.assertIn('Failed: recorded-hashes, workspace', text)
        self.assertIn('Not run: archive, native-access', text)
        self.assertIn('Not established: hook-trust:berman-codex, native-capture:berman-codex', text)

    def test_an_unknown_fact_is_shown_as_unknown_with_its_reason_never_as_no(self):
        result = CASES['status-busy']
        text = shown('status', result)
        blocks = {block.split()[0]: block for block in text.split('\n\n') if block.startswith('run-')}
        damaged, ready, declared = blocks['run-007'], blocks['run-006'], blocks['run-003']

        def fact(block, name):
            return next(line.split(None, 2) for line in block.splitlines() if line.split()[:1] == [name])

        for name in ('registered', 'ending', 'packaged'):
            with self.subTest(fact=name):
                self.assertEqual(fact(damaged, name)[1], 'unknown')
                self.assertIn('Invalid operation record', fact(damaged, name)[2])
                self.assertEqual(fact(ready, name)[1], 'no')
        self.assertEqual(fact(declared, 'ending')[1], 'yes')
        self.assertIn('declared 2026-11-13T01:00:00.000000Z', fact(declared, 'ending')[2])
        self.assertEqual(fact(declared, 'packaged')[1], 'no')
        self.assertEqual(fact(declared, 'finalized')[1], 'yes')
        # A closed capture beside an undeclared ending: both on the screen, neither implying the other.
        frozen = blocks['run-005']
        self.assertEqual((fact(frozen, 'capture')[1], fact(frozen, 'ending')[1]), ('closed', 'no'))
        self.assertIn('not an ending', frozen)

    def test_actions_are_grouped_by_whether_a_decision_comes_first(self):
        text = shown('cheatsheet', CASES['cheatsheet-busy'])
        orchard = text[text.index('Orchard (orchard') : text.index('Meadow (meadow')]
        now, decision = orchard.index('Available now'), orchard.index("Needs the researcher's decision")
        attention = orchard.index('Needs attention')
        self.assertLess(attention, now)
        self.assertLess(now, decision)
        launch = CASES['cheatsheet-busy']['connections'][0]['commands']['launch'][0]
        self.assertLess(now, orchard.index(launch + '\n'))
        self.assertLess(orchard.index(launch + '\n'), decision)
        for needs_decision in (
            'finish --run run-004',
            'new --finish-previous',
            'new --keep-open',
            'supplement --run run-005',
        ):
            self.assertGreater(orchard.index(needs_decision), decision, needs_decision)
        copse = text[text.index('Copse (copse') : text.index('Heath (heath')]
        self.assertIn("Needs the researcher's decision", copse)
        self.assertNotIn('Available now', copse)
        heath = text[text.index('Heath (heath') : text.index('Fen (fen')]
        self.assertIn('Available now', heath)
        self.assertNotIn("Needs the researcher's decision", heath)

    def test_commands_are_printed_whole_however_narrow_the_terminal(self):
        result = CASES['cheatsheet-busy']
        # Every command the plain text prints on a line of its own, for this recorded situation.
        printed = plain.cheatsheet(result).splitlines()
        commands = [
            line.strip().partition('    (launcher')[0] for line in printed if line.strip().startswith('berman --root')
        ]
        self.assertGreater(len(commands), 15)
        narrow = shown('cheatsheet', result, width=40).splitlines()
        for command in commands:
            with self.subTest(command=command):
                self.assertTrue(any(line.strip().startswith(command) for line in narrow))
        prose = [line for line in narrow if 'berman --root' not in line]
        self.assertLessEqual(max(len(line) for line in prose), 40)
        launch = CASES['prepared-with-history']['launch']
        self.assertIn('\n' + launch + '\n', shown('prepared', CASES['prepared-with-history'], width=30))
        steps = [check['next'] for check in CASES['verification-failed']['checks'].values() if check.get('next')]
        verification = shown('verification', CASES['verification-failed'], width=50).splitlines()
        for step in steps:
            self.assertTrue(any(line.strip() == 'next: ' + step for line in verification), step)

    def test_recorded_text_is_shown_literally(self):
        text = shown('verification', CASES['verification-failed'], color_system='standard')
        self.assertIn('[bold]also-gone[/bold].txt', text)
        self.assertIn(
            'Meadow [draft]', shown('status', dict(CASES['status-busy'], default_connection='Meadow [draft]'))
        )
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
        for word, _, fallback, _ in terminal.STATUS.values():
            self.assertIn(f'{fallback} {word}', text)


class CommandLineTests(unittest.TestCase):
    def run_cli(self, *words, stream=None, **env):
        out, err = stream or io.StringIO(), io.StringIO()
        with environment(**env), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(words))
        return code, out.getvalue(), err.getvalue()

    def test_json_output_does_not_depend_on_the_stream(self):
        piped = self.run_cli('--json', 'verify', '--software')
        on_terminal = self.run_cli('--json', 'verify', '--software', stream=Terminal())
        self.assertEqual((piped[0], on_terminal[0]), (0, 0))
        self.assertNotIn(ESCAPE, on_terminal[1])
        first, second = json.loads(piped[1]), json.loads(on_terminal[1])
        first.pop('checked_at_utc'), second.pop('checked_at_utc')
        self.assertEqual(first, second)
        self.assertEqual(on_terminal[1], json.dumps(json.loads(on_terminal[1]), indent=2) + '\n')

    def test_a_terminal_changes_the_presentation_and_nothing_else(self):
        piped = self.run_cli('verify', '--software')
        on_terminal = self.run_cli('verify', '--software', stream=Terminal())
        self.assertEqual((piped[0], on_terminal[0]), (0, 0))
        self.assertNotIn(ESCAPE, piped[1])
        self.assertTrue(
            piped[1].startswith('Berman verification: PASSED\n  passed                      installation: ')
        )
        self.assertIn(ESCAPE, on_terminal[1])
        self.assertIn('✔ passed', on_terminal[1])
        plain_terminal = self.run_cli('verify', '--software', stream=Terminal(), TERM='dumb')
        self.assertEqual(plain_terminal[1], piped[1])
        uncolored = self.run_cli('verify', '--software', stream=Terminal(), NO_COLOR='1')
        self.assertIsNone(COLOR.search(uncolored[1]))
        self.assertIn('✔ passed', uncolored[1])

    def test_refusals_keep_their_exit_code_and_stream_on_a_terminal(self):
        for stream in (io.StringIO(), Terminal()):
            with self.subTest(terminal=isinstance(stream, Terminal)):
                code, out, err = self.run_cli('--root', str(REPO / 'tests'), 'status', stream=stream)
                self.assertEqual((code, out), (2, ''))
                self.assertTrue(err.startswith('Berman needs attention: '), err)
                self.assertNotIn(ESCAPE, err)


class PseudoTerminalTests(unittest.TestCase):
    """The installed command on a real pseudo-terminal, as an operator's shell runs it."""

    def run_on_terminal(self, *words, **env):
        primary, secondary = pty.openpty()
        environ = {k: v for k, v in os.environ.items() if k not in ('NO_COLOR', 'FORCE_COLOR')}
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
        for name in ('installation', 'dependencies', 'frozen-collector', 'schemas'):
            self.assertIn(name, text)
        code, text, err = self.run_on_terminal('verify', '--software', TERM='dumb')
        self.assertEqual((code, text, err), (0, piped.stdout, ''))
        code, text, err = self.run_on_terminal('--json', 'verify', '--software')
        self.assertEqual((code, err), (0, ''))
        self.assertNotIn(ESCAPE, text)
        self.assertEqual(json.loads(text)['verdict'], 'passed')


if __name__ == '__main__':
    unittest.main()
