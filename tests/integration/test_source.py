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

    def test_software_verification_runs_from_an_empty_directory(self):
        done = subprocess.run(
            [sys.executable, '-I', '-B', '-m', 'berman', '--json', 'verify'],
            cwd=self.base,
            capture_output=True,
            check=True,
        )
        result = json.loads(done.stdout)
        self.assertTrue(result['passed'])
        self.assertEqual(set(result['checks']), {'installation', 'dependencies', 'schemas'})
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
