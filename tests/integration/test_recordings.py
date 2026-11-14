"""Evidence failures through the release CLI, on synthetic preserved recordings only."""

import json
from pathlib import Path
import shutil
import struct
import zipfile

from berman import files, preserve, recordings, recovery, snapshots, verify
from berman.archive import builder
from tests.integration.test_records import RecordTests
from tests.integration.test_interruptions import state
from tests.support.sessions import start


class RecordingAuditTests(RecordTests):
    def setUp(self):
        super().setUp()
        self.workflow.new()
        start(self.store, 'run-001', end=True)
        result = self.workflow.finish('Explicit synthetic ending', 'run-001')
        # Exercise the bounded historical reader with a 0.4.2-format package.
        original_package = Path(result['package']['path'])
        original_package.unlink()
        original_package.with_suffix('.zip.sha256').unlink()
        shutil.rmtree(self.root / 'recordings')
        entry = files.read(self.root / 'source-layout.json')['runs'][0]
        payload = preserve.recording_payload(self.root / 'runs/run-001', entry)
        prefix = preserve.namespace(self.store, self.store.record('run-001'))
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
        entry = recovery.migration_entry(self.preserved, 'run-001', self.preserved)
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
        self.workflow.new()
        start(self.store, 'run-001')
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
        row = self.workflow.status()['runs'][0]
        self.assertIn('missing capture metadata', str(row['problems']))
        self.assertIn('capture', row['unknown'])


class RecoveryCustodyTests(RecordTests):
    def test_changed_recovered_material_refuses_retry_without_replacing_it(self):
        self.workflow.new()
        source = self.store.folder('run-001') / '.capture-orphan'
        source.write_bytes(b'Original abandoned write')
        receipt = recovery.quarantine(self.root, 'run-001', [source], 'Synthetic interrupted write')[0]
        material = Path(receipt).parent / 'material'
        material.write_bytes(b'Damaged retained material')
        before = state(material.parent)
        with self.assertRaisesRegex(files.Decision, 'differs from its receipt'):
            recovery.generated_residue(self.store, 'run-001')
        self.assertEqual(state(material.parent), before)

    def test_recovery_refuses_both_inventoried_active_and_preserved_material(self):
        self.workflow.new()
        source = self.store.field / 'run-001/.capture-unknown'
        source.write_bytes(b'Already inventoried original')
        files.write(self.root / 'migrations/run-001.json', {'entry': 'boundary exists'})
        before = state(self.store.field)
        with self.assertRaisesRegex(files.Decision, 'after migration inventory'):
            recovery.quarantine(self.root, 'run-001', [source], 'must not happen')
        self.assertEqual(state(self.store.field), before)
        (self.root / 'migrations/run-001.json').unlink()
        preserved = self.root / 'runs/run-001'
        preserved.mkdir(parents=True)
        original = preserved / '.capture-original'
        original.write_bytes(b'Preserved original')
        before = state(preserved)
        with self.assertRaisesRegex(files.Decision, 'from preserved runs'):
            recovery.quarantine(self.root, 'run-001', [original], 'must not happen')
        self.assertEqual(state(preserved), before)

    def test_participant_and_selected_project_files_are_not_temporary_writer_material(self):
        selected = self.project / '.capture-selected'
        selected.write_text('Selected project document\n')
        self.edit('connections/stewart/connection.json', documents=['README.md', '.capture-selected'])
        self.load()
        self.workflow.new()
        note = self.store.field / 'run-001/notes/.capture-participant'
        note.write_text('Participant notes\n')
        # A genuine abandoned atomic write beside the operation record is retained separately.
        orphan = self.store.folder('run-001') / '.capture-abandoned'
        orphan.write_bytes(b'Unpublished operation bytes')
        start(self.store, 'run-001')
        result = self.workflow.finish('Explicit synthetic ending', 'run-001')
        root = self.root / 'runs/run-001'
        self.assertEqual((root / 'notes/.capture-participant').read_text(), 'Participant notes\n')
        self.assertEqual((root / 'context/berman/closing-project/.capture-selected').read_text(), selected.read_text())
        self.assertEqual(
            (root / 'context/berman/preparation/initial-project/.capture-selected').read_text(), selected.read_text()
        )
        self.assertTrue(result['recovered_writes'])
        material = [Path(path).parent / 'material' for path in result['recovered_writes']]
        self.assertTrue(any(path.read_bytes() == b'Unpublished operation bytes' for path in material if path.is_file()))
        with zipfile.ZipFile(result['package']['path']) as archive:
            self.assertTrue(any(name.endswith('/notes/.capture-participant') for name in archive.namelist()))
        before = state(root)
        self.workflow.finish('Repeated explicit ending', 'run-001')
        self.assertEqual(state(root), before)
        self.assertEqual(verify.recording_check(self.store)['status'], 'passed')
