"""Portable custody and edition verification after both the host and the controller are gone."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import io
import json
from pathlib import Path
import shutil
import stat
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4
import zipfile

from berman import custody, evidence, files, protocol, snapshots
from berman.archive import package

FIXTURE = Path(__file__).parents[1] / 'fixtures/split-0.26.4'
RECORDING_DIGEST = '8dfc7d0291e718324aa0fec2a0ca68ba389b327e7603a022d047cbaa8d0f2251'
EDITION_DIGEST = 'eea495a2cd2cbfe995b6cb7c28b9aa86117e6b0c10aff19ccb45cdb737a4b06e'


class CustodyTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.root = self.base / 'guest'
        self.ident = '7fafc98c-11e2-4acd-9035-408d9199fddd'
        files.write(self.root / 'collection.json', {'id': self.ident})

    def inputs(self):
        blob = self.base / 'file'
        blob.write_bytes(b'Native observation in /Users/example/checkout; no guest checkout exists.\n')
        key = 'packet/notes/work.md'
        digest = files.digest(blob.read_bytes())
        observed = [{'path': key, 'size': blob.stat().st_size, 'sha256': digest}]
        occurrence = {'collection_id': self.ident, 'run': 'run-013', 'connection': 'project'}
        ending_id = str(uuid4())
        assignment = dict(occurrence, harness='codex', operation=str(uuid4()))
        observation = dict(assignment, assignment_sha256=files.digest_json(assignment))
        state = dict(
            occurrence,
            assignment=assignment,
            decision={'plan': {'id': 'plan-on-guest'}},
            transferred={key: digest},
            facts={
                'ending': {'at': '2026-11-17T12:00:00Z', 'reason': 'Explicit ending', 'operation': ending_id},
                'capture': dict(
                    observation, members=observed, limitations=['Missing parent transcript'], ending_id=ending_id
                ),
                'accept': {'accepted_members': 1, 'inventory_sha256': protocol.fingerprint(observed)},
                'preparation': observation,
                'launch': observation,
            },
        )
        return state, {key: blob}

    def test_existing_split_recording_edition_and_selection_reopen_without_rebuilding(self):
        recording = FIXTURE / 'recording.zip'
        receipt = custody.import_recording(self.root, recording, expected_sha256=RECORDING_DIGEST)
        verified = custody.verify_recording(self.root, receipt)
        self.assertIsNone(verified['manifest']['evidence']['capture'])
        self.assertEqual(verified['integrity'], 'passed')
        edition = FIXTURE / 'edition.zip'
        self.assertEqual(package.verify_edition(edition, EDITION_DIGEST)['mode'], 'verified')
        pointer = json.loads((FIXTURE / 'selection.json').read_bytes())
        archive = self.root / pointer['path']
        archive.parent.mkdir(parents=True)
        shutil.copyfile(edition, archive)
        target = self.root / package.pointer_path(pointer['selection'])
        target.parent.mkdir()
        shutil.copyfile(FIXTURE / 'selection.json', target)
        with patch.object(custody.builder, 'render', side_effect=AssertionError('republished')):
            self.assertEqual(custody.publish_edition(self.root, receipt)['archive_sha256'], EDITION_DIGEST)
        self.assertEqual(target.read_bytes(), (FIXTURE / 'selection.json').read_bytes())

    def test_preservation_retries_an_earlier_committed_recording_from_the_same_facts(self):
        recording = FIXTURE / 'recording.zip'
        receipt = custody.import_recording(self.root, recording, expected_sha256=RECORDING_DIGEST)
        with zipfile.ZipFile(recording) as archive:
            state = json.loads(archive.read('evidence/controller.json'))
            blobs = {}
            for name in state['transferred']:
                path = self.base / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(archive.read(f'runs/{state["run"]}/{name}'))
                blobs[name] = path
        self.assertEqual(custody.preserve_split_run(self.root, state, blobs), receipt)
        state['facts']['ending']['reason'] = 'A different ending'
        with self.assertRaisesRegex(ValueError, 'differs from requested preservation'):
            custody.preserve_split_run(self.root, state, blobs)
        self.assertEqual((self.root / receipt['relative_path']).read_bytes(), recording.read_bytes())

    def test_old_recording_profiles_are_refused_before_writing(self):
        with zipfile.ZipFile(FIXTURE / 'recording.zip') as z:
            payload = {i.filename: (z.read(i), i.external_attr >> 16) for i in z.infolist()}
        manifest = json.loads(payload['recording.json'][0])
        for profile in ('native', 'historical-import'):
            with self.subTest(profile=profile):
                payload['recording.json'] = (files.json_bytes(dict(manifest, profile=profile)), stat.S_IFREG | 0o644)
                old = self.base / 'old.zip'
                old.write_bytes(package.make_zip(payload, ''))
                dest = self.base / 'view'
                with self.assertRaisesRegex(ValueError, profile):
                    evidence.verify_package(old)
                with self.assertRaisesRegex(ValueError, profile):
                    evidence.materialize(old, dest)
                with self.assertRaisesRegex(ValueError, profile):
                    custody.import_recording(self.root, old, expected_sha256=files.digest(old.read_bytes()))
                self.assertFalse(dest.exists())
                self.assertFalse((self.root / 'custody').exists())
        # Recreate a previously accepted occurrence with matching hashes and its original receipt.
        digest = files.digest(old.read_bytes())
        receipt = {
            'schema': 'berman.custody-recording/1',
            'collection_id': self.ident,
            'run': manifest['run'],
            'package_sha256': digest,
            'manifest_sha256': files.digest_json(dict(manifest, profile=profile)),
            'bytes': old.stat().st_size,
            'coverage': manifest['coverage'],
            'relative_path': f'custody/{manifest["run"]}/recording.zip',
        }
        stored = self.root / receipt['relative_path']
        files.write(stored.parent / 'receipt.json', receipt)
        shutil.copyfile(old, stored)
        with self.assertRaisesRegex(ValueError, profile):
            custody.publish_edition(self.root, receipt)
        self.assertFalse((self.root / 'archive').exists())

    def test_detached_edition_refuses_inventory_authority_and_embedded_old_profiles(self):
        with zipfile.ZipFile(FIXTURE / 'edition.zip') as z:
            payload = {i.filename: (z.read(i), i.external_attr >> 16) for i in z.infolist()}
        name = package.PREFIX + package.MANIFEST
        original = json.loads(payload[name][0])
        item = original['inputs']['recordings'][0]
        for profile in ('historical-import', 'native', None):
            value = deepcopy(original)
            changed = dict(payload)
            if profile is None:
                value['inputs']['recordings'][0]['basis'] = 'historical inventory entry'
                reason = 'requires a split recording manifest'
            else:
                ref = package.PREFIX + item['path']
                data = files.json_bytes(dict(json.loads(payload[ref][0]), profile=profile))
                changed[ref] = (data, payload[ref][1])
                row = next(r for r in value['members'] if r['path'] == item['path'])
                row.update(size=len(data), sha256=files.digest(data))
                value['inputs']['recordings'][0]['sha256'] = row['sha256']
                reason = profile
            changed[name] = (files.json_bytes(value), payload[name][1])
            with self.subTest(profile=profile), self.assertRaisesRegex(ValueError, reason):
                package.inspect_edition(package.make_zip(changed, ''))

    def test_detached_edition_checks_original_bytes_and_modes_without_a_supplied_digest(self):
        with zipfile.ZipFile(FIXTURE / 'edition.zip') as archive:
            payload = {i.filename: (archive.read(i), i.external_attr >> 16) for i in archive.infolist()}
        name = package.PREFIX + 'runs/run-013/packet/notes/work.md'
        data, mode = payload[name]
        for member in [(data + b'x', mode), (data, mode ^ 0o100)]:
            with self.subTest(member=member), self.assertRaisesRegex(ValueError, 'member bytes or mode differ'):
                package.inspect_edition(package.make_zip(dict(payload, **{name: member}), ''))

    def test_split_retry_after_publication_interruption_and_detached_verification(self):
        state, blobs = self.inputs()
        install = package.install

        def stop_before_pointer(root, name, data):
            if name.startswith('archive/editions/selections/'):
                raise OSError('interrupted')
            install(root, name, data)

        with (
            patch.object(package, 'install', side_effect=stop_before_pointer),
            self.assertRaisesRegex(OSError, 'interrupted'),
        ):
            custody.preserve_and_publish(self.root, state, blobs)
        (installed,) = (self.root / 'archive/editions').glob('*.zip')
        first_digest = files.digest(installed.read_bytes())
        receipt = custody.preserve_split_run(self.root, state, blobs)
        state['facts']['preserve'] = receipt
        self.assertEqual(receipt, custody.preserve_split_run(self.root, state, blobs))
        self.assertEqual(receipt['coverage']['unknown'], ['Missing parent transcript'])
        next(iter(blobs.values())).unlink()
        edition = custody.publish_edition(self.root, receipt)
        self.assertEqual(edition['archive_sha256'], first_digest)
        self.assertEqual(edition, custody.publish_edition(self.root, receipt))
        copied = self.base / 'detached.zip'
        shutil.copyfile(self.root / edition['archive_path'], copied)
        standalone = self.base / 'recording.zip'
        shutil.copyfile(self.root / receipt['relative_path'], standalone)
        shutil.rmtree(self.root)
        self.assertEqual(evidence.verify_package(standalone, receipt['package_sha256'])['integrity'], 'passed')
        self.assertEqual(package.verify_edition(copied, edition['archive_sha256'])['original_files'], 1)
        with zipfile.ZipFile(copied) as z:
            prefix = 'berman-analysis/recordings/run-013/'
            saved = json.loads(z.read(prefix + 'evidence/controller.json'))
            self.assertEqual(saved['decision'], state['decision'])
            self.assertEqual(saved['facts']['capture'], state['facts']['capture'])
            self.assertEqual(json.loads(z.read(prefix + 'manifest.json'))['coverage'], receipt['coverage'])

    def test_reject_mismatched_ending_acceptance_or_blob_before_commit(self):
        state, blobs = self.inputs()
        for fact, key, bad in [
            ('capture', 'ending_id', str(uuid4())),
            ('accept', 'accepted_members', 0),
            ('accept', 'inventory_sha256', '0' * 64),
            ('ending', 'reason', ''),
        ]:
            with self.subTest(fact=fact, key=key):
                changed = deepcopy(state)
                changed['facts'][fact][key] = bad
                with self.assertRaises(ValueError):
                    custody.preserve_split_run(self.root, changed, blobs)
                self.assertFalse((self.root / 'custody/run-013').exists())
        path = next(iter(blobs.values()))
        path.write_bytes(b'x' * path.stat().st_size)
        with self.assertRaisesRegex(ValueError, 'Guest blob differs'):
            custody.preserve_split_run(self.root, state, blobs)

    def test_conflicting_concurrent_preservations_never_replace_the_winner(self):
        state, blobs = self.inputs()
        other = deepcopy(state)
        other['facts']['ending']['reason'] = 'Different declaration'
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(custody.preserve_split_run, self.root, candidate, blobs) for candidate in (state, other)
            ]
        self.assertEqual(sum(isinstance(call.exception(), ValueError) for call in futures), 1)
        receipt = next(call.result() for call in futures if not call.exception())
        self.assertEqual(custody.verify_recording(self.root, receipt)['integrity'], 'passed')
        (self.root / receipt['relative_path']).write_bytes(b'damaged')
        with self.assertRaisesRegex(ValueError, 'SHA-256'):
            custody.publish_edition(self.root, receipt)

    def test_verified_primary_generation_is_decoded_from_accepted_blobs(self):
        state, blobs = self.inputs()
        meta = {'run': state['run'], 'harness': 'codex', 'format': 'codex-jsonl/1', 'session_id': 'parent'}
        raw = b'{"type":"response_item","payload":{"type":"message","role":"assistant","content":[]}}\n'
        reader = b'Worker response\n'
        meta.update(sha256=files.digest(raw), saved_bytes=len(raw), reader_sha256=files.digest(reader))
        session = self.base / 'session'
        snapshots.publish(session, meta, raw, reader, b'', self.base / 'scratch')
        blobs.update({p.relative_to(self.base).as_posix(): p for p in session.rglob('*') if p.is_file()})
        members = [
            {'path': key, 'sha256': files.digest(p.read_bytes()), 'size': p.stat().st_size} for key, p in blobs.items()
        ]
        state['transferred'] = {row['path']: row['sha256'] for row in members}
        capture = state['facts']['capture']
        capture.update(members=members, session_id='parent', parent_sha256=meta['sha256'], limitations=[])
        state['facts']['accept'] = {'accepted_members': len(blobs), 'inventory_sha256': protocol.fingerprint(members)}
        capture['parent_sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'parent transcript'):
            custody.preserve_split_run(self.root, state, blobs)
        capture['parent_sha256'] = meta['sha256']
        result = custody.preserve_and_publish(self.root, state, blobs)
        manifest = custody.verify_recording(self.root, result['recording'])['manifest']
        self.assertIn(files.digest_json(meta), manifest['evidence']['capture'])
        # Re-open the initial split format, which retained the committed native
        # pointer but omitted the redundant top-level capture reference.
        with zipfile.ZipFile(self.root / result['recording']['relative_path']) as original:
            early = deepcopy(manifest)
            early['evidence']['capture'] = None
            evidence.check_authority(early, original.read)
            early['evidence']['capture'] = early['evidence']['controller']
            with self.assertRaisesRegex(ValueError, 'different captures'):
                evidence.check_authority(early, original.read)
        self.assertTrue(any(s['format'] == 'codex-jsonl/1' for s in manifest['streams']))
        with zipfile.ZipFile(self.root / result['edition']['archive_path']) as z:
            self.assertIn(b'packet/notes/work.md', z.read('berman-analysis/review/run-013.txt'))

    def test_stream_length_is_bounded_before_acceptance(self):
        data = (FIXTURE / 'recording.zip').read_bytes()
        digest = files.digest(data)
        for raw, size in [(data[:-3], len(data)), (data + b'x', len(data)), (data, custody.MAX_PACKAGE + 1)]:
            with self.subTest(size=size), self.assertRaises(ValueError):
                custody.import_recording(self.root, io.BytesIO(raw), expected_sha256=digest, size=size)
        receipt = custody.import_recording(self.root, io.BytesIO(data), expected_sha256=digest, size=len(data))
        self.assertEqual(receipt['package_sha256'], digest)

    def test_import_bounds_paths_partial_bytes_and_foreign_collection(self):
        source = self.base / 'candidate.zip'
        shutil.copyfile(FIXTURE / 'recording.zip', source)
        digest = files.digest(source.read_bytes())
        for name, maximum in [('MAX_PACKAGE', 10), ('MAX_MEMBERS', 2), ('MAX_DIRECTORY', 20), ('MAX_MANIFEST', 30)]:
            with self.subTest(name=name), patch.object(custody, name, maximum), self.assertRaises(ValueError):
                custody.import_recording(self.root, source, expected_sha256=digest)
        files.write(self.root / 'collection.json', {'id': str(uuid4())})
        with self.assertRaisesRegex(ValueError, 'collection'):
            custody.import_recording(self.root, source, expected_sha256=digest)
        with zipfile.ZipFile(source, 'a') as z:
            z.writestr('../escape', 'outside')
        with self.assertRaisesRegex(ValueError, 'Unsafe recording member'):
            custody.import_recording(self.root, source, expected_sha256=files.digest(source.read_bytes()))
        source.write_bytes(source.read_bytes()[:13])
        with self.assertRaisesRegex(ValueError, 'SHA-256'):
            custody.import_recording(self.root, source, expected_sha256=digest)
        self.assertFalse((self.base / 'escape').exists())
        self.assertFalse((self.root / 'custody').exists())
