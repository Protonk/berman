"""The documented example works with the installed public interface."""

from pathlib import Path
import tempfile
import unittest

from tests.support.paths import REPO
from tests.support.wheel_smoke import smoke


class InstallationTests(unittest.TestCase):
    def test_example_is_commissioned_closed_preserved_and_read_from_copies(self):
        with tempfile.TemporaryDirectory() as folder:
            report = smoke(REPO / 'examples/minimal-study', Path(folder).resolve())
        self.assertEqual(report['software_verification'], 'passed')
        self.assertEqual(report['guest_verification'], 'verified')
        self.assertTrue(report['detached_recording_and_edition_verified'])
        self.assertTrue(report['portable_working_copy_verified'])
        self.assertFalse(report['hooks_load_third_party_modules'])
        # The stand-in collection is carried as what it is: every gap stays named in the recording.
        self.assertIn('Synthetic collection: no native session was run or observed.', report['coverage_unknown'])
        self.assertIn('No launch receipt retained', report['coverage_unknown'])
