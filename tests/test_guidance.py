"""Keep the maintained operating instructions usable from a source checkout."""

from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import re
import unittest

from berman.cli import main, parser

ROOT = Path(__file__).resolve().parents[1]
# The person who opens and closes episodes is the producer. Release notes keep their history.
FORMER_ROLE_NAMES = re.compile(r'\b(manager|operator|researcher)s?\b', re.IGNORECASE)


class GuidanceTests(unittest.TestCase):
    def test_help_aliases_match_the_parser_output(self):
        commands = parser()._subparsers._group_actions[0].choices
        for topic in (None, *commands):
            with self.subTest(topic=topic):
                arguments = [topic] if topic else []
                with redirect_stdout(StringIO()) as original, self.assertRaises(SystemExit) as result:
                    main([*arguments, '--help'])
                self.assertEqual(result.exception.code, 0)
                with redirect_stdout(StringIO()) as alias:
                    self.assertEqual(main(['help', *arguments]), 0)
                self.assertEqual(alias.getvalue(), original.getvalue())

    def test_local_links_and_documented_commands(self):
        documents = [
            ROOT / 'README.md',
            ROOT / 'AGENTS.md',
            *(ROOT / 'docs').rglob('*.md'),
            *(ROOT / 'development').rglob('*.md'),
        ]
        commands = set(parser()._subparsers._group_actions[0].choices)
        for path in documents:
            body = path.read_text()
            for target in re.findall(r'\[[^\]]*\]\(([^ )]+)\)', body):
                if '://' not in target:
                    with self.subTest(source=path.name, target=target):
                        self.assertTrue((path.parent / target.split('#')[0]).exists())
            for command in re.findall(r'^(?:uv run )?berman ([a-z-]+)\b', body, re.M):
                with self.subTest(source=path.name, command=command):
                    self.assertIn(command, commands)

    def test_the_role_is_named_producer(self):
        sources = [
            ROOT / 'README.md',
            ROOT / 'AGENTS.md',
            ROOT / 'pyproject.toml',
            *(p for p in (ROOT / 'docs').rglob('*.md') if 'releases' not in p.parts),
            *(ROOT / 'development').rglob('*.md'),
            *(ROOT / 'src' / 'berman').rglob('*.py'),
            *(ROOT / 'src' / 'berman' / 'templates').glob('*.md'),
            *(ROOT / 'tests').rglob('*.py'),
        ]
        texts = {str(p.relative_to(ROOT)): p.read_text() for p in sources if p != Path(__file__).resolve()}
        commands = parser()._subparsers._group_actions[0].choices
        for topic in (None, *commands):
            with redirect_stdout(StringIO()) as output:
                main(['help', *([topic] if topic else [])])
            texts[f'berman help {topic or ""}'.strip()] = output.getvalue()
        for source, text in texts.items():
            with self.subTest(source=source):
                self.assertEqual(FORMER_ROLE_NAMES.findall(text), [])
