"""Portable occurrence identity, retained authority and detached CLI use."""

import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import zipfile
from unittest.mock import patch

from berman import evidence, files, recordings, schemas, snapshots, verify
from tests.integration.test_records import RecordTests
from tests.support import legacy
from tests.support.workspace import state


class PortableRecordingsTests(RecordTests):
    def preserved(self):
        legacy.prepare(self.store)
        legacy.start(self.store, 'run-001', end=True)
        result = legacy.finish(self.store, 'run-001', 'Researcher explicitly ended the synthetic engagement')
        manifest = evidence.verify_package(result['package']['path'])['manifest']
        schemas.validate('berman.recording/2', manifest)
        schemas.validate('berman.capture-current/1', snapshots.pointer(self.root / 'runs/run-001/session'))
        return Path(result['package']['path'])

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

    def test_package_and_working_copy_verify_without_the_workspace_or_original_mtimes(self):
        package = self.preserved()
        detached = self.base / 'source.zip'
        shutil.copyfile(package, detached)
        digest = files.digest(detached.read_bytes())
        shutil.rmtree(self.root)
        report = self.detached('recording', 'verify', detached, '--sha256', digest)
        self.assertEqual(report['integrity'], 'passed')
        view = self.base / 'view'
        self.detached('recording', 'materialize', detached, view, '--sha256', digest)
        changed = view / 'runs/run-001/run.json'
        os.utime(changed, ns=(1, 1))
        self.assertEqual(self.detached('recording', 'verify', view)['manifest_sha256'], report['manifest_sha256'])
        changed.write_text('Different interpretation must not replace evidence.\n')
        self.assertIn('bytes differ', self.detached('recording', 'verify', view, expected=2)['message'])
        self.assertIn('must be new', self.detached('recording', 'materialize', detached, view, expected=2)['message'])

    def test_mutable_indexes_cannot_change_retained_ending_or_connection(self):
        package = self.preserved()
        expected = evidence.verify_package(package)
        ending = self.store.record('run-001')['ending']
        self.edit('operations/run-001/operation.json', ending=dict(ending, reason='Later unsupported claim'))
        (self.root / 'source-layout.json').unlink()
        (self.root / 'migrations/run-001.json').unlink()
        self.assertEqual(verify.recording_check(self.store)['status'], 'passed')
        rows = self.store.discover()
        self.assertEqual(rows[0]['problems'], [])
        self.assertEqual(rows[0]['record']['ending'], ending)
        (self.store.folder('run-001') / 'operation.json').unlink()
        self.assertEqual(self.store.discover()[0]['record']['ending'], ending)
        self.assertEqual(evidence.verify_package(package), expected)
        self.assertEqual(self.status()['archived'], 1)

    def test_missing_promised_package_and_retained_authority_are_damage(self):
        package = self.preserved()
        package.unlink()
        self.assertEqual(verify.recording_check(self.store)['status'], 'failed')
        (self.root / 'recordings/run-001/evidence/operation.json').unlink()
        rows = self.store.discover()
        self.assertTrue(rows[0]['problems'])
        self.assertIn('ending', rows[0]['lifecycle']['unknown'])

    def test_discovery_does_not_present_a_changed_ending_as_retained_authority(self):
        self.preserved()
        path = self.root / 'runs/run-001/context/berman/ending.json'
        files.write(path, dict(files.read(path), reason='Unsupported later claim'))
        row = self.store.discover()[0]
        self.assertTrue(row['problems'])
        self.assertIn('ending', row['lifecycle']['unknown'])

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

    def test_historical_export_records_absence_and_leaves_originals_and_indexes_untouched(self):
        self.preserved()
        shutil.rmtree(self.root / 'recordings')
        (self.store.folder('run-001') / 'operation.json').unlink()
        (self.root / 'migrations/run-001.json').unlink()
        source = self.root / 'runs/run-001'
        (source / 'context/berman/ending.json').unlink()
        entry = legacy.migration_entry(source, 'run-001')
        files.write(self.root / 'source-layout.json', {'runs': [entry]})
        before = state(self.root), files.inventory(source)
        exported = self.base / 'historical.zip'
        code, out, error = self.command(
            '--json', 'recording', 'export', '--run', 'run-001', '--output', str(exported), '--historical'
        )
        self.assertEqual((code, error), (0, ''))
        report = json.loads(out)
        self.assertEqual(report['integrity'], 'passed')
        self.assertIsNone(report['manifest']['evidence']['ending'])
        self.assertTrue(any('ending' in item for item in report['coverage']['unknown']))
        self.assertEqual((state(self.root), files.inventory(source)), before)
        self.assertEqual(report['manifest']['attribution']['basis'], 'retained packet metadata')

    def test_occurrences_keep_distinct_identity_even_when_their_content_is_equal(self):
        report = evidence.verify_package(self.preserved())
        changed = dict(report['manifest'], collection_id='d5de26f7-077c-491b-a3ec-a9c43d638eee')
        self.assertNotEqual(evidence.identity(changed), report['manifest_sha256'])
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

    def test_safe_internal_symlink_materializes_without_following_external_links(self):
        legacy.prepare(self.store)
        packet = self.store.field / 'run-001'
        (packet / 'notes/alias').symlink_to('../narrative.md')
        legacy.start(self.store, 'run-001', end=True)
        result = legacy.finish(self.store, 'run-001', 'Explicit ending')
        package = Path(result['package']['path'])
        with self.assertRaisesRegex(ValueError, 'parent-traversing'):
            evidence.materialize(package, self.base / 'unsafe-view')
        self.assertFalse((self.base / 'unsafe-view').exists())
        # A second run contains the historical same-directory alias convention.
        legacy.prepare(self.store)
        packet = self.store.field / 'run-002'
        (packet / 'notes/source.txt').write_text('Preserved bytes')
        (packet / 'notes/alias').symlink_to('source.txt')
        legacy.start(self.store, 'run-002', end=True)
        result = legacy.finish(self.store, 'run-002', 'Explicit ending')
        view = self.base / 'safe-view'
        evidence.materialize(result['package']['path'], view)
        self.assertTrue((view / 'runs/run-002/notes/alias').is_symlink())
        self.assertEqual(evidence.verify_directory(view)['integrity'], 'passed')

    def test_all_capture_generations_supplements_are_explicitly_typed(self):
        self.preserved()
        manifest = files.read(recordings.manifest_path(self.root, 'run-001'))
        stream = next(row for row in manifest['streams'] if row['format'] == 'codex-jsonl/1')
        self.assertEqual(stream['format'], 'codex-jsonl/1')
        self.assertIn('/session/snapshots/', stream['path'])
        self.assertTrue((self.root / stream['path']).is_file())


class CaptureRetentionTests(RecordTests):
    """Committed capture generations, which the executor stages with and preserved packets hold."""

    def test_append_growth_is_bounded_and_displaced_raw_and_partial_tails_survive(self):
        legacy.prepare(self.store)
        legacy.start(self.store, 'run-001')
        session = self.store.field / 'run-001/session'
        source = self.store.home / 'sessions/fixture-run-001.jsonl'
        for number in range(20):
            with source.open('a') as stream:
                stream.write(json.dumps({'type': 'event_msg', 'payload': {'message': str(number) + 'x' * 1024}}) + '\n')
            legacy.capture(self.store, 'run-001', 'Stop')
        original = source.read_bytes()
        self.assertEqual(len(list((session / 'snapshots').iterdir())), 1)
        self.assertLess(sum(p.stat().st_size for p in session.rglob('transcript.jsonl')), len(original) * 2)
        source.write_bytes(original + b'{"unfinished":')
        legacy.capture(self.store, 'run-001', 'Stop')
        source.write_bytes(original.splitlines(keepends=True)[0] + b'{"replacement":true}\n')
        legacy.capture(self.store, 'run-001', 'Stop')
        self.assertEqual((session / 'displaced' / (files.digest(original) + '.jsonl')).read_bytes(), original)
        self.assertEqual(
            (session / 'tails' / (files.digest(b'{"unfinished":') + '.bin')).read_bytes(), b'{"unfinished":'
        )
        self.assertTrue(snapshots.read(session))
        self.assertFalse(self.status()['runs'][0]['ending_declared'])

    def test_damaged_commit_history_cannot_authorize_cleanup(self):
        legacy.prepare(self.store)
        legacy.start(self.store, 'run-001')
        session = self.store.field / 'run-001/session'
        pointer = snapshots.pointer(session)
        orphan = session / 'snapshots' / ('0' * 64)
        orphan.mkdir()
        (orphan / 'original.bin').write_bytes(b'Must survive invalid cleanup authorization')
        files.write(session / 'current.json', dict(pointer, committed=['0' * 64, *pointer['committed']]))
        before = state(session)
        with self.assertRaisesRegex(ValueError, 'history disagree'):
            snapshots.recover(session, self.root / '.state/capture-staging/run-001')
        self.assertEqual(state(session), before)
