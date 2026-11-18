"""The standalone command checks software independently of the working directory."""

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

    def test_software_verification_ignores_workspace_files(self):
        marker = self.base / 'berman.json'
        marker.write_bytes(b'broken json')
        for flags in ([], ['--software']):
            done = subprocess.run(
                [sys.executable, '-I', '-B', '-m', 'berman', '--json', 'verify', *flags],
                capture_output=True,
                cwd=self.base,
                check=True,
            )
            result = json.loads(done.stdout)
            self.assertEqual((result['scope'], result['verdict']), ('software', 'passed'))
            self.assertEqual(set(result['checks']), {'installation', 'dependencies', 'schemas'})
        self.assertEqual(list(self.base.iterdir()), [marker])
        self.assertEqual(marker.read_bytes(), b'broken json')

    def test_removed_workspace_options_and_commands_are_refused(self):
        for argv in (
            ['--root', '.', 'verify'],
            ['status'],
            ['verify', '--record', 'experiments/x'],
            ['verify', '--skip', 'archive'],
            ['archive'],
            ['archive', '--verify'],
            ['archive', '--freshness'],
            ['recording', 'export'],
        ):
            with self.subTest(argv=argv):
                done = subprocess.run(
                    [sys.executable, '-I', '-B', '-m', 'berman', *argv], cwd=self.base, capture_output=True
                )
                self.assertEqual(done.returncode, 2, done.stderr)
                self.assertEqual(list(self.base.iterdir()), [])

    def test_entrypoint_ignores_pythonpath_startup_code(self):
        hostile = self.base / 'hostile'
        hostile.mkdir()
        marker = self.base / 'startup-ran'
        (hostile / 'sitecustomize.py').write_text(f'open({str(marker)!r}, "w").write("loaded")\n')
        result = subprocess.run(
            [sys.executable, '-I', '-B', '-m', 'berman', '--help'],
            env=dict(os.environ, PYTHONPATH=str(hostile)),
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(marker.exists())
