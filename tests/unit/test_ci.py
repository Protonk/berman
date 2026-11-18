"""Regressions for the three CI claims that normal fieldwork does not adjudicate."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from berman import connector, files
from scripts.ci import run as ci


class QualificationTests(unittest.TestCase):
    def test_access_requires_the_tested_edit_and_both_codes_in_the_note(self):
        project = Path(self.enterContext(tempfile.TemporaryDirectory()))
        seed = '12' * 16
        members = {}
        for name in ('answer.py', 'check.py'):
            initial = (ci.FIXTURE / 'project' / name).read_bytes()
            (project / name).write_bytes(initial)
            members['initial-project/' + name] = initial
        challenge = (seed + '\n').encode()
        (project / 'challenge.txt').write_bytes(challenge)
        check = [sys.executable, '-B', 'check.py']
        self.assertNotEqual(0, subprocess.run(check, cwd=project, capture_output=True).returncode)
        self.assertFalse((project / 'test-result.json').exists())
        (project / 'answer.py').write_text(f'VALUE = "{seed}"\n')
        output = subprocess.check_output(check, cwd=project, text=True)
        members['initial-project/challenge.txt'] = challenge
        for name in ('challenge.txt', 'answer.py', 'check.py', 'test-result.json'):
            members['final-project/' + name] = (project / name).read_bytes()
        note_path = 'packet/notes/account.md'
        members[note_path] = f'{seed}\n{output}'.encode()
        digest = files.digest(challenge)
        self.assertEqual(ci.access(members.__getitem__, digest), members[note_path])
        for key, value in [
            ('final-project/answer.py', b'VALUE = "another edit"'),
            ('final-project/check.py', b'print("passed")'),
            ('final-project/challenge.txt', b'other challenge'),
            ('final-project/test-result.json', json.dumps({'answer_sha256': 'bad', 'code': seed}).encode()),
            (note_path, seed.encode()),
            (note_path, output.encode()),
        ]:
            with self.subTest(key=key), patch.dict(members, {key: value}), self.assertRaises(ValueError):
                ci.access(members.__getitem__, digest)

    def test_codex_initial_registration_and_conflict_in_the_ci_home(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        home = root / 'codex-home'
        spool = root / 'spool'
        ci.codex_hooks(home, spool, root)
        installed = home / 'hooks.json'
        self.assertEqual(files.read(installed), files.read(root / 'codex-hooks.json'))
        ci.codex_hooks(home, spool, root)
        installed.write_text('{"hooks": {}}')
        with self.assertRaisesRegex(ValueError, 'Review'):
            ci.codex_hooks(home, spool, root)
        self.assertEqual(installed.read_text(), '{"hooks": {}}')

    def test_candidate_interpreter_is_specific_to_the_ci_connection(self):
        settings = {'study': 'fixture', 'host': 'guest', 'collection_id': '27cc51dd-90e2-449d-b62d-0e645b04b6d8'}
        self.assertIn('.local/berman/bin/python', connector.Remote(settings).argv)
        candidate = connector.Remote(settings, controller_python='ci runtime/bin/python')
        self.assertIn("'ci runtime/bin/python'", candidate.argv)
        self.assertNotIn('.local/berman/bin/python', candidate.argv)
