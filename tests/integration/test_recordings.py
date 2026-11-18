"""Evidence failures through the release CLI, on synthetic preserved recordings only."""

import json
from pathlib import Path
import shutil
import struct
import zipfile

from berman import files, recordings, snapshots, verify
from berman.archive import builder
from tests.integration.test_records import RecordTests
from tests.support import legacy
from tests.support.workspace import state


class RecordingAuditTests(RecordTests):
    def setUp(self):
        super().setUp()
        legacy.prepare(self.store)
        legacy.start(self.store, 'run-001', end=True)
        result = legacy.finish(self.store, 'run-001', reading=False)
        # Exercise the bounded historical reader with a 0.4.2-format package.
        original_package = Path(result['package']['path'])
        original_package.unlink()
        original_package.with_suffix('.zip.sha256').unlink()
        shutil.rmtree(self.root / 'recordings')
        entry = files.read(self.root / 'source-layout.json')['runs'][0]
        payload = recordings.recording_payload(self.root / 'runs/run-001', entry)
        prefix = recordings.namespace(self.store, self.store.record('run-001'))
        self.package = self.root / 'archive/recordings/run-001.zip'
        data = builder.make_zip(payload, prefix)
        self.package.write_bytes(data)
        self.package.with_suffix('.zip.sha256').write_text(f'{files.digest(data)}  {self.package.name}\n')
        self.edit(
            'operations/run-001/operation.json',
            package={
                'path': str(self.package),
                'sha256': files.digest(data),
                'bytes': len(data),
                'members': len(payload),
                'namespace': prefix,
            },
        )
        self.preserved = self.root / 'runs/run-001'
        self.assertEqual(verify.recording_check(self.store)['status'], 'passed')

    def audit(self):
        before = state(self.root)
        code, out, error = self.command('--json', 'verify', '--skip', 'archive')
        self.assertEqual(error, '')
        result = json.loads(out)
        self.assertEqual(state(self.root), before, 'verification changed evidence')
        self.assertEqual(code, verify.EXIT[result['verdict']])
        return result

    def failed(self, phrase):
        result = self.audit()
        self.assertEqual(result['verdict'], 'failed')
        self.assertIn('recordings', result['failed'])
        self.assertIn(phrase, str(result['checks']['recordings']['details']))

    def test_healthy_recordings_pass_read_only_with_each_fact_named(self):
        result = self.audit()
        row = result['checks']['recordings']
        self.assertEqual(row['status'], 'passed')
        self.assertEqual(
            row['details']['runs'][0]['checked'], ['originals', 'capture', 'migration', 'ending', 'recording']
        )

    def test_absent_capture_closure_is_unknown_even_when_the_snapshot_is_intact(self):
        for name in ('finalized.json', 'ended.json'):
            (self.preserved / 'session' / name).unlink(missing_ok=True)
        before = state(self.preserved)
        self.assertEqual(recordings.session(self.preserved, 'run-001'), ['no capture closure record was retained'])
        self.assertEqual(state(self.preserved), before)

    def test_capture_closure_cannot_belong_to_a_different_run_with_the_same_transcript(self):
        path = self.preserved / 'session/finalized.json'
        value = files.read(path)
        files.write(path, dict(value, run='run-999'))
        before = state(self.preserved)
        with self.assertRaisesRegex(ValueError, 'finalized.json and capture run disagree'):
            recordings.session(self.preserved, 'run-001')
        self.assertEqual(state(self.preserved), before)

    def test_a_missing_zip_fails_even_when_the_operation_records_one(self):
        self.package.unlink()
        self.failed('recording:')

    def test_a_missing_ending_cannot_be_hidden_by_a_recorded_package(self):
        self.edit('operations/run-001/operation.json', ending=...)
        self.failed('no researcher ending declaration')

    def test_contradictory_ending_records_fail(self):
        ending = dict(self.store.record('run-001')['ending'], reason='A different claim')
        self.edit('operations/run-001/operation.json', ending=ending)
        self.failed('ending declarations disagree')

    def test_changed_originals_and_missing_inventory_are_reported(self):
        (self.preserved / 'notes/new-unrecorded.txt').write_text('Unrecorded material\n')
        self.failed('originals:')
        files.write(self.root / 'source-layout.json', {'runs': []})
        self.failed('no source-layout.json inventory')

    def test_removing_inventory_and_packet_does_not_hide_an_archived_operation(self):
        shutil.move(self.preserved, self.base / 'retained-test-copy')
        files.write(self.root / 'source-layout.json', {'runs': []})
        self.failed('no source-layout.json inventory')

    def test_forged_zip_hashes_cannot_hide_wrong_members(self):
        with zipfile.ZipFile(self.package, 'a') as archive:
            archive.writestr('unrelated-private.txt', 'Synthetic sentinel\n')
        data = self.package.read_bytes()
        package = dict(self.store.record('run-001')['package'], sha256=files.digest(data), bytes=len(data))
        self.edit('operations/run-001/operation.json', package=package)
        self.package.with_suffix('.zip.sha256').write_text(f'{files.digest(data)}  {self.package.name}\n')
        self.failed('ZIP membership does not match')

    def test_invalid_compressed_bytes_are_a_failed_check_without_a_traceback(self):
        with zipfile.ZipFile(self.package) as archive:
            member = next(info for info in archive.infolist() if info.compress_size > 1)
        data = bytearray(self.package.read_bytes())
        offset = member.header_offset
        name_bytes, extra_bytes = struct.unpack_from('<HH', data, offset + 26)
        # BFINAL=1 and the reserved DEFLATE block type: zlib must reject the stream.
        data[offset + 30 + name_bytes + extra_bytes] = 7
        self.package.write_bytes(data)
        package = dict(self.store.record('run-001')['package'], sha256=files.digest(data), bytes=len(data))
        self.edit('operations/run-001/operation.json', package=package)
        self.package.with_suffix('.zip.sha256').write_text(f'{files.digest(data)}  {self.package.name}\n')
        self.failed('invalid block type')

    def test_missing_operation_is_not_treated_as_unrecorded_historical_completion(self):
        (self.store.folder('run-001') / 'operation.json').unlink()
        self.failed('preserved ending exists but its operation record is missing')

    def test_historical_missing_authority_is_unestablished_and_not_backfilled(self):
        # Construct a pre-operation historical specimen: no ending, journal or package assertion.
        (self.store.folder('run-001') / 'operation.json').unlink()
        (self.root / 'migrations/run-001.json').unlink()
        (self.preserved / 'context/berman/ending.json').unlink()
        entry = legacy.migration_entry(self.preserved, 'run-001')
        files.write(self.root / 'source-layout.json', {'runs': [entry]})
        result = self.audit()
        row = result['checks']['recordings']
        self.assertEqual(row['status'], 'passed')
        self.assertTrue(row['details']['coverage']['unknown'])
        self.assertEqual(row['details']['problems'], [])
        self.assertIn('no operation record establishes an ending', str(row['details']['missing']))

    def test_skipping_recordings_makes_the_verdict_incomplete(self):
        result = verify.verify(self.root, skip=('archive', 'recordings'))
        self.assertEqual(result['checks']['recordings']['status'], 'not-run')
        self.assertEqual(result['verdict'], 'incomplete')


class CaptureEvidenceTests(RecordTests):
    def test_false_json_capture_metadata_is_damage_and_reading_never_repairs_it(self):
        legacy.prepare(self.store)
        legacy.start(self.store, 'run-001')
        path = snapshots.resolve(self.store.field / 'run-001/session') / 'capture.json'
        for value in ([], None, {}, False, 0, ['nonempty'], {'run': 'run-001'}):
            with self.subTest(value=value):
                files.write(path, value)
                before = state(self.store.field), state(self.root)
                code, output, error = self.command('--json', 'status')
                self.assertEqual((code, error), (0, ''))
                row = json.loads(output)['runs'][0]
                self.assertIn('capture', row['unknown'])
                self.assertIn('damaged capture metadata', str(row['problems']))
                self.assertNotIn('without a snapshot yet', row['summary'])
                self.assertEqual((state(self.store.field), state(self.root)), before)
        path.unlink()
        row = self.status()['runs'][0]
        self.assertIn('missing capture metadata', str(row['problems']))
        self.assertIn('capture', row['unknown'])


class TemporaryLookingNamesTests(RecordTests):
    def test_participant_files_named_like_writer_temporaries_are_preserved_members(self):
        # A fieldworker's note may carry the prefix the old writer used for its own atomic writes.
        legacy.prepare(self.store)
        note = self.store.field / 'run-001/notes/.capture-participant'
        note.write_text('Participant notes\n')
        legacy.start(self.store, 'run-001', end=True)
        result = legacy.finish(self.store, 'run-001', reading=False)
        root = self.root / 'runs/run-001'
        before = state(root)
        self.assertEqual(verify.recording_check(self.store)['status'], 'passed')
        with zipfile.ZipFile(result['package']['path']) as archive:
            self.assertTrue(any(name.endswith('/notes/.capture-participant') for name in archive.namelist()))
        exported = self.base / 'exported.zip'
        code, _, error = self.command('recording', 'export', '--run', 'run-001', '--output', str(exported))
        self.assertEqual((code, error), (0, ''))
        with zipfile.ZipFile(exported) as archive:
            self.assertEqual(archive.read('runs/run-001/notes/.capture-participant'), b'Participant notes\n')
        self.assertEqual(state(root), before)
