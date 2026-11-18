"""The standalone command works from any directory, with or without a study."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class StandaloneTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()

    def berman(self, *args):
        # An empty home: no connector settings, and nothing of the operator's own is read.
        env = {**os.environ, 'HOME': str(self.base)}
        env.pop('BERMAN_CONNECTOR', None)
        return subprocess.run(
            [sys.executable, '-I', '-B', '-m', 'berman', *args], cwd=self.base, env=env, capture_output=True, text=True
        )

    def test_software_verification_runs_from_an_empty_directory(self):
        done = self.berman('--json', 'verify')
        self.assertEqual(0, done.returncode, done.stderr)
        result = json.loads(done.stdout)
        self.assertTrue(result['passed'])
        self.assertEqual(set(result['checks']), {'installation', 'dependencies', 'schemas'})
        self.assertEqual(list(self.base.iterdir()), [])

    def test_cheatsheet_without_a_study_says_how_to_check_connect_and_read(self):
        done = self.berman('cheatsheet')
        self.assertEqual((0, ''), (done.returncode, done.stderr))
        self.assertIn('no study selected', done.stdout.splitlines()[0])
        for expected in (
            'No connector settings at ~/.config/berman/connector.json',
            '-m berman verify',
            'guest-deployment.md',
            '-m berman recording verify FILE --sha256 DIGEST',
            'This cheatsheet changed nothing.',
        ):
            self.assertIn(expected, done.stdout)
        self.assertNotIn('repeated after an interruption', done.stdout)
        self.assertEqual(list(self.base.iterdir()), [])

    def test_cheatsheet_offers_the_settings_it_finds_and_reports_an_unreachable_guest(self):
        settings = {
            'collection_id': '29525f31-3e9d-4c3d-9078-cedd71216be9',
            'study': 'example',
            'host': 'berman-test.invalid',
            'spool': str(self.base / 'spool'),
            'bindings': {
                'example': {'claude': {'project': '/work/example', 'home': '~/.claude', 'executable': 'claude'}}
            },
        }
        folder = self.base / '.config/berman'
        folder.mkdir(parents=True)
        (folder / 'example.json').write_text(json.dumps(settings))
        (folder / 'notes.json').write_text('{"unrelated": true}')
        offered = self.berman('cheatsheet')
        self.assertIn('-m berman cheatsheet --config ~/.config/berman/example.json', offered.stdout)
        self.assertNotIn('notes.json', offered.stdout)

        # The operator program takes the settings on either side of the word.
        for words in (
            ['remote', '--config', str(folder / 'example.json'), 'cheatsheet'],
            ['remote', 'cheatsheet', '--config', str(folder / 'example.json')],
        ):
            self.assertIn('study example in guest berman-test.invalid', self.berman(*words).stdout.splitlines()[0])

        done = self.berman('cheatsheet', '--config', str(folder / 'example.json'), '--json')
        self.assertEqual(0, done.returncode, done.stderr)
        sheet = json.loads(done.stdout)
        self.assertEqual(('berman.cheatsheet/1', []), (sheet['schema'], sheet['engagements']))
        self.assertTrue(any('did not answer' in note for note in sheet['notes']))
        commission = sheet['sections'][0]['steps'][0]['command']
        self.assertTrue(
            commission.endswith(
                'remote --config ~/.config/berman/example.json commission --request LABEL --connection example --harness claude'
            ),
            commission,
        )
        self.assertFalse((self.base / 'spool').exists())
