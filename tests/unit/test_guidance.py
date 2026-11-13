"""Current guidance agrees with the code: its links resolve, its commands exist and its quoted digest holds.

Reads only the repository's own current guidance documents and _legacy/capture_session.py. It never
reads beneath runs/ (links there are skipped unopened) or ~/.codex, and runs no command: each
documented `berman` invocation is checked against the argparse help of berman_cli.main.
"""
from tests.support.paths import REPO
import contextlib
import functools
import hashlib
import io
import os
from pathlib import Path
import re
import shlex
import unittest

from berman import cli

ROOT = REPO
# Current guidance and planning entry points; historical reports and preserved records are excluded.
GUIDANCE = ('README.md', 'AGENTS.md', 'docs/usage.md', 'docs/limitations.md', 'docs/development.md',
            'docs/development-launchers.md')
COMMANDS = ('AGENTS.md', 'docs/usage.md', 'docs/development.md')
# Current procedure names maintained commands; experiment scripts are evidence of their own dates.
NO_SCRIPTS = ('README.md', 'AGENTS.md', 'docs/usage.md', 'docs/development.md', 'docs/capture.md')
DIGESTS = ('docs/capture.md', 'docs/development.md')
FENCE = re.compile(r'^```[^\n]*\n(.*?)^```', re.M | re.S)
LINK = re.compile(r'\]\(([^)\s]+)\)')
HEADING = re.compile(r'^#{1,6}[ \t]+(.+?)[ \t]*$', re.M)
SPAN = re.compile(r'`([^`]+)`')
COMMAND = re.compile(r'^(?:\./)?berman\s+\S')  # A bare `berman` names the program, not a command.
WORD = re.compile(r'[a-z][a-z-]*')
NESTED = re.compile(r'^positional arguments:\n\s+\{([^}]+)\}', re.M)
ANSI = re.compile(r'\x1b\[[0-9;]*m')
OPTION_LINE = re.compile(r'^  (-\S[^\n]*?)(?: {2,}|$)', re.M)  # The invocation column of an options entry.
OPTION = re.compile(r'(?:^|, )(--?[A-Za-z][\w-]*)')
FROZEN = re.compile(r'capture_session\.py.{0,160}?SHA-256 `?([0-9a-f]{8,64})', re.S)
SCRIPT = re.compile(r'experiments/\S*?\.py\b')


def text(name):
    return (ROOT / name).read_text()


def prose(value):
    """Markdown outside fenced code blocks."""
    return FENCE.sub('', value)


def slug(heading):
    """The anchor a heading receives on GitHub, for the plain headings guidance uses."""
    return re.sub(r'[^\w\- ]', '', heading.strip().lower()).replace(' ', '-')


def anchors(path):
    return {slug(h) for h in HEADING.findall(prose(path.read_text()))}


def links(name):
    """(target path or None for this document, fragment) for each relative link; URLs and runs/ are skipped."""
    found = []
    for target in LINK.findall(prose(text(name))):
        if re.match(r'[a-z][a-z0-9+.-]*:', target):
            continue
        location, _, fragment = target.partition('#')
        path = Path(os.path.normpath((ROOT / name).parent / location)) if location else None
        if path is not None and path.is_relative_to(ROOT / 'runs'):
            continue
        found.append((target, path, fragment))
    return found


def invocations(name):
    """Each `berman ...` command in inline code or at the start of a fenced line, as words."""
    value = text(name)
    lines = [line for block in FENCE.findall(value) for line in block.splitlines()]
    found = []
    for candidate in SPAN.findall(prose(value)) + lines:
        candidate = ' '.join(candidate.split())
        if not COMMAND.match(candidate):
            continue
        try:
            words = shlex.split(candidate, comments=True)
        except ValueError:
            words = candidate.split()
        found.append((candidate, words[1:]))
    return found


@functools.lru_cache(maxsize=None)
def usage(words):
    """berman_cli's help for a command path: (exit code, text)."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        try:
            cli.main([*words, '--help'])
            code = None
        except SystemExit as exc:
            code = exc.code
    return code, ANSI.sub('', out.getvalue())


@functools.lru_cache(maxsize=None)
def options(words):
    """The exact option strings a command path accepts, from the invocation column of its help."""
    return frozenset(o for line in OPTION_LINE.findall(usage(words)[1]) for o in OPTION.findall(line))


class GuidanceTest(unittest.TestCase):
    def test_current_guidance_links_resolve(self):
        for name in GUIDANCE:
            for target, path, fragment in links(name):
                with self.subTest(document=name, link=target):
                    if path is not None:
                        self.assertTrue(path.is_relative_to(ROOT), f'{name} links outside the repository: {target}')
                        self.assertTrue(path.exists(), f'{name} links to a missing path: {target}')
                    if fragment:
                        document = path or ROOT / name
                        self.assertEqual(document.suffix, '.md', f'{name} names an anchor in a non-Markdown file: {target}')
                        self.assertIn(fragment, anchors(document), f'{name} links to a missing anchor: {target}')

    def test_documented_commands_exist(self):
        checked = 0
        for name in COMMANDS:
            for candidate, tokens in invocations(name):
                with self.subTest(document=name, command=candidate):
                    path, rest = (), list(tokens)
                    while rest and rest[0].startswith('-'):
                        flag = rest.pop(0).partition('=')[0]
                        self.assertIn(flag, options(()), f'berman has no option {flag}')
                        if flag == '--root' and rest:
                            rest.pop(0)
                    while rest and WORD.fullmatch(rest[0]):
                        nested = NESTED.search(usage(path)[1])
                        if not nested:
                            break
                        self.assertIn(rest[0], nested.group(1).split(','), f'berman {" ".join(path)} has no command {rest[0]}')
                        path += (rest.pop(0),)
                    self.assertTrue(path, f'{candidate} names no berman command')
                    self.assertEqual(usage(path)[0], 0, f'berman {" ".join(path)} --help failed')
                    for flag in (t.partition('=')[0] for t in rest if t.startswith('--')):
                        self.assertIn(flag, options(path), f'berman {" ".join(path)} has no option {flag}')
                    checked += 1
        self.assertGreater(checked, 0)

    def test_options_are_matched_exactly(self):
        # Help text mentioning an option, or an option's prefix, is not the option itself.
        self.assertIn('--connection', options(('new',)))
        self.assertNotIn('--run', options(('new',)))
        self.assertTrue({'--reason', '--run', '--allow-incomplete'} <= options(('finish',)))  # Consecutive entries.
        self.assertNotIn('--allow', options(('finish',)))
        self.assertTrue({'--root', '--json'} <= options(()))

    def test_frozen_collector_digest_matches_guidance(self):
        digest = hashlib.sha256((ROOT / 'src/berman/_legacy/capture_session.py').read_bytes()).hexdigest()
        for name in DIGESTS:
            self.assertTrue(FROZEN.findall(text(name)), f'{name} quotes no frozen-collector digest')
        for name in (*GUIDANCE, 'docs/capture.md'):
            for quoted in FROZEN.findall(text(name)):
                self.assertTrue(digest.startswith(quoted), f'{name} quotes {quoted}; capture_session.py is {digest}')

    def test_current_guidance_names_no_experiment_scripts(self):
        for name in NO_SCRIPTS:
            self.assertEqual(SCRIPT.findall(text(name)), [], f'{name} names an experiment script as procedure')


if __name__ == '__main__':
    unittest.main()
