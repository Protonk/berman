"""Release numbering and local release artifacts follow docs/development.md.

Reads pyproject.toml and the names of files in dist/, which Git ignores and which may be absent.
"""

import re
import tomllib
import unittest

from tests.support.paths import REPO

VERSION = tomllib.loads((REPO / 'pyproject.toml').read_text())['project']['version']
ARTIFACT = re.compile(r'^berman-(?P<version>.+?)(?:-py3-none-any\.whl|\.tar\.gz)$')


class ReleaseArtifactTests(unittest.TestCase):
    def test_the_version_is_a_release_year_and_a_serial(self):
        self.assertRegex(VERSION, r'^0\.\d{2}\.[1-9]\d*$', 'versions are 0.YY.N, with no pre-release stage')

    def test_dist_holds_only_the_current_version(self):
        dist = REPO / 'dist'
        names = sorted(p.name for p in dist.iterdir()) if dist.is_dir() else []
        stale = [n for n in names if (m := ARTIFACT.match(n)) and m['version'] != VERSION]
        self.assertEqual(stale, [], f'clear dist/ before building {VERSION}; older releases are kept on GitHub')

    def test_the_artifact_pattern_tells_versions_apart(self):
        self.assertEqual(ARTIFACT.match('berman-0.26.1-py3-none-any.whl')['version'], '0.26.1')
        self.assertEqual(ARTIFACT.match('berman-0.26.1.tar.gz')['version'], '0.26.1')
        self.assertIsNone(ARTIFACT.match('SHA256SUMS'))


if __name__ == '__main__':
    unittest.main()
