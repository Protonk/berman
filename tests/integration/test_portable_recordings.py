"""Read working copies and detached recording ZIPs through the public CLI."""

import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import stat
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from berman import evidence, files
from berman.archive import package
from tests.support.paths import REPO


class PortableRecordingsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()

    def preserved(self):
        path = self.base / 'recording.zip'
        shutil.copyfile(REPO / 'tests/fixtures/split-0.26.4/recording.zip', path)
        return path

    def detached(self, *arguments, expected=0):
        result = subprocess.run(
            [sys.executable, '-I', '-B', '-m', 'berman', '--json', *map(str, arguments)],
            cwd=self.base,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, expected, result.stderr)
        self.assertNotIn('Traceback', result.stderr)
        return json.loads(result.stdout if expected == 0 else result.stderr)

    def test_package_and_working_copy_verify_without_the_study_or_original_mtimes(self):
        package = self.preserved()
        detached = self.base / 'source.zip'
        shutil.copyfile(package, detached)
        digest = files.digest(detached.read_bytes())
        report = self.detached('recording', 'verify', detached, '--sha256', digest)
        self.assertEqual(report['integrity'], 'passed')
        view = self.base / 'view'
        self.detached('recording', 'materialize', detached, view, '--sha256', digest)
        changed = view / 'runs/run-013/packet/notes/work.md'
        os.utime(changed, ns=(1, 1))
        self.assertEqual(self.detached('recording', 'verify', view)['manifest_sha256'], report['manifest_sha256'])
        changed.write_text('Different interpretation must not replace evidence.\n')
        self.assertIn('bytes differ', self.detached('recording', 'verify', view, expected=2)['message'])
        self.assertIn('must be new', self.detached('recording', 'materialize', detached, view, expected=2)['message'])

    def test_materialization_uses_exactly_the_verified_bytes_when_the_source_path_changes(self):
        package = self.preserved()
        original = package.read_bytes()
        inspect = evidence.inspect_package

        def swap(data, expected=None):
            result = inspect(data, expected)
            package.write_bytes(b'Source path changed after verification')
            return result

        with patch.object(evidence, 'inspect_package', side_effect=swap):
            report = evidence.materialize(package, self.base / 'stable-view', files.digest(original))
        self.assertEqual(report['package_sha256'], files.digest(original))
        self.assertEqual(evidence.verify_directory(self.base / 'stable-view')['integrity'], 'passed')

    def test_occurrences_keep_distinct_identity_even_when_their_content_is_equal(self):
        report = evidence.verify_package(self.preserved())
        changed = dict(report['manifest'], collection_id='d5de26f7-077c-491b-a3ec-a9c43d638eee')
        self.assertNotEqual(files.digest_json(changed), report['manifest_sha256'])
        self.assertEqual(changed['entries'], report['manifest']['entries'])

    def test_package_membership_and_authority_are_checked_without_an_external_hash(self):
        package = self.preserved()
        with zipfile.ZipFile(package, 'a') as archive:
            archive.writestr('unrelated-private.txt', b'Unselected private material')
        self.assertIn('membership differs', self.detached('recording', 'verify', package, expected=2)['message'])

    def test_unreadable_encrypted_manifest_is_a_refusal_without_a_traceback(self):
        package = self.preserved()
        data = bytearray(package.read_bytes())
        with zipfile.ZipFile(package) as archive:
            offset = archive.getinfo('recording.json').header_offset
        flags = struct.unpack_from('<H', data, offset + 6)[0]
        struct.pack_into('<H', data, offset + 6, flags | 1)
        offset = data.index(b'PK\x01\x02')
        while True:
            name_length, extra_length, comment_length = struct.unpack_from('<HHH', data, offset + 28)
            if data[offset + 46 : offset + 46 + name_length] == b'recording.json':
                flags = struct.unpack_from('<H', data, offset + 8)[0]
                struct.pack_into('<H', data, offset + 8, flags | 1)
                break
            offset += 46 + name_length + extra_length + comment_length
        package.write_bytes(data)
        self.assertIn('encrypted', self.detached('recording', 'verify', package, expected=2)['message'])

    def test_materialization_confines_links(self):
        source = self.preserved()
        with zipfile.ZipFile(source) as archive:
            payload = {i.filename: (archive.read(i), i.external_attr >> 16) for i in archive.infolist()}
        original = json.loads(payload['recording.json'][0])
        dest = self.base / 'view'
        for target in ('../outside', '/outside', 'work.md'):
            manifest = json.loads(json.dumps(original))
            link = {'type': 'symlink', 'mode': '0o777', 'target': target}
            relative = 'packet/notes/link'
            full = f'runs/{manifest["run"]}/{relative}'
            manifest['entries'].append(dict(link, path=full))
            manifest['custody']['inventory']['entries'].append(dict(link, path=relative))
            payload['recording.json'] = (files.json_bytes(manifest), stat.S_IFREG | 0o644)
            payload[full] = (target.encode(), stat.S_IFLNK | 0o777)
            source.write_bytes(package.make_zip(payload, ''))
            if target == 'work.md':
                evidence.materialize(source, dest)
                self.assertEqual(os.readlink(dest / full), target)
                self.assertEqual(evidence.verify_directory(dest)['integrity'], 'passed')
            else:
                with self.assertRaisesRegex(ValueError, 'refuses external or parent-traversing links'):
                    evidence.materialize(source, dest)
                self.assertFalse(dest.exists())
