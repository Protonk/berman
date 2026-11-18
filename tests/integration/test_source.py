"""The standalone command checks software independently of the working directory."""

import json
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
