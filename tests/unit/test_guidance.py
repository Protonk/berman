"""Keep the maintained operating instructions usable from a source checkout."""

import re
from pathlib import Path
import unittest

from berman.cli import parser

ROOT = Path(__file__).resolve().parents[2]


class GuidanceTests(unittest.TestCase):
    def test_local_links_and_documented_commands(self):
        documents = [
            ROOT / 'README.md',
            ROOT / 'AGENTS.md',
            *(ROOT / 'docs').rglob('*.md'),
            *(ROOT / 'apparatus').rglob('*.md'),
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
