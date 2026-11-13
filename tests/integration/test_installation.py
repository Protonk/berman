"""The documented example works with the installed public interface."""
from pathlib import Path
import tempfile
import unittest

from tests.support.paths import REPO
from tests.support.wheel_smoke import smoke


class InstallationTests(unittest.TestCase):
    def test_example_prepares_captures_preserves_and_archives(self):
        with tempfile.TemporaryDirectory() as folder:
            report = smoke(REPO / 'examples/minimal-study', Path(folder).resolve())
        self.assertEqual(report['software_verification'], 'passed')
        self.assertEqual(report['workspace_verification'], 'incomplete')
        self.assertTrue(report['archive_verified'])
