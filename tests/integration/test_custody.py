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
        target = (
            self.root
            / 'archive/editions/selections'
            / (files.digest(package.json_bytes(pointer['selection'])) + '.json')
        )
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
        self.assertEqual(custody.preserve_recording(self.root, state, blobs), receipt)
        state['facts']['ending']['reason'] = 'A different ending'
        with self.assertRaisesRegex(ValueError, 'differs from requested preservation'):
            custody.preserve_recording(self.root, state, blobs)
        self.assertEqual((self.root / receipt['relative_path']).read_bytes(), recording.read_bytes())

    def test_new_edition_pins_one_recording_and_each_reading_aid(self):
        state, blobs = self.inputs()
        recording = custody.preserve_recording(self.root, state, blobs)
        published = custody.publish_edition(self.root, recording)
        with zipfile.ZipFile(self.root / published['archive_path']) as edition:
            payload = {
                i.filename.removeprefix(package.PREFIX): (edition.read(i), i.external_attr >> 16)
                for i in edition.infolist()
            }
        with zipfile.ZipFile(self.root / recording['relative_path']) as original:
            for i in original.infolist():
                self.assertEqual(payload[i.filename], (original.read(i), i.external_attr >> 16))
            source = json.loads(original.read(evidence.MANIFEST))
            self.assertEqual(source['schema'], 'berman.recording/3')
            self.assertEqual(source['connection'], state['connection'])
            manifest = json.loads(payload[package.MANIFEST][0])
            self.assertEqual(manifest['schema'], 'berman.reading-edition/3')
            self.assertEqual(manifest['recording_manifest_sha256'], files.digest(original.read(evidence.MANIFEST)))
            self.assertEqual(manifest['reader_sha256'], custody.builder.reader_identity())
            self.assertEqual(
                set(original.namelist()) | {package.MANIFEST} | set(manifest['reading_aids']), set(payload)
            )
        self.assertEqual(
            files.read(self.root / published['selection_pointer']),
            {'schema': 'berman.edition-selection/2', 'sha256': published['archive_sha256']},
        )
        name = next(iter(blobs))
        original_path = f'runs/{state["run"]}/{name}'
        for key, replacement, problem in [
            (original_path, (b'other', stat.S_IFREG | 0o600), 'Recording bytes'),
            (original_path, (payload[original_path][0], stat.S_IFREG | 0o644), 'Recording mode'),
            (original_path, None, 'membership'),
            ('review/events.txt', (b'other', package.FILE_MODE), 'Reading aid bytes'),
            ('review/unlisted.txt', (b'new member', package.FILE_MODE), 'membership'),
            (evidence.MANIFEST, (files.json_bytes(source), package.FILE_MODE), 'different recording manifest'),
            (
                package.MANIFEST,
                (files.json_bytes(dict(manifest, reader_sha256={})), package.FILE_MODE),
                'reader source digests',
            ),
        ]:
            with self.subTest(key=key, problem=problem):
                changed = dict(payload)
                if replacement is None:
                    del changed[key]
                else:
                    changed[key] = replacement
                with self.assertRaisesRegex(ValueError, problem):
                    package.inspect_edition(package.make_zip(changed, package.PREFIX))

    def test_published_pointer_must_match_guest_custody_and_the_ledger(self):
        state, blobs = self.inputs()
        receipt = custody.preserve_recording(self.root, state, blobs)
        published = custody.publish_edition(self.root, receipt)
        facts = {'preserve': receipt, 'publish': dict(published, archive_sha256='0' * 64)}
        with self.assertRaisesRegex(ValueError, 'publication receipt'):
            custody.verify_occurrence(self.root, state['run'], facts)

        # The other edition is valid, even for this run and collection, but has a different ending.
        other = self.base / 'other'
        files.write(other / 'collection.json', {'id': self.ident})
        state['facts']['ending']['reason'] = 'Another declaration'
        foreign = custody.publish_edition(other, custody.preserve_recording(other, state, blobs))
        for key in ('archive_path', 'selection_pointer'):
            shutil.copyfile(other / foreign[key], self.root / foreign[key])
        with self.assertRaisesRegex(ValueError, 'different recording'):
            custody.publish_edition(self.root, receipt)
        with self.assertRaisesRegex(ValueError, 'different recording'):
            custody.verify_occurrence(self.root, state['run'], {'preserve': receipt, 'publish': published})

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
            receipt = custody.preserve_recording(self.root, state, blobs)
            custody.publish_edition(self.root, receipt)
        (installed,) = (self.root / 'archive/editions').glob('*.zip')
        first_digest = files.digest(installed.read_bytes())
        state['facts']['preserve'] = receipt
        self.assertEqual(receipt, custody.preserve_recording(self.root, state, blobs))
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
            prefix = package.PREFIX
            saved = json.loads(z.read(prefix + 'evidence/controller.json'))
            self.assertEqual(saved['decision'], state['decision'])
            self.assertEqual(saved['facts']['capture'], state['facts']['capture'])
            self.assertEqual(json.loads(z.read(prefix + evidence.MANIFEST))['coverage'], receipt['coverage'])

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
                    custody.preserve_recording(self.root, changed, blobs)
                self.assertFalse((self.root / 'custody/run-013').exists())
        path = next(iter(blobs.values()))
        path.write_bytes(b'x' * path.stat().st_size)
        with self.assertRaisesRegex(ValueError, 'Guest blob differs'):
            custody.preserve_recording(self.root, state, blobs)

    def test_conflicting_concurrent_preservations_never_replace_the_winner(self):
        state, blobs = self.inputs()
        other = deepcopy(state)
        other['facts']['ending']['reason'] = 'Different declaration'
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(custody.preserve_recording, self.root, candidate, blobs) for candidate in (state, other)
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
            custody.preserve_recording(self.root, state, blobs)
        capture['parent_sha256'] = meta['sha256']
        receipt = custody.preserve_recording(self.root, state, blobs)
        edition = custody.publish_edition(self.root, receipt)
        manifest = custody.verify_recording(self.root, receipt)['manifest']
        self.assertIn(files.digest_json(meta), manifest['evidence']['capture'])
        with zipfile.ZipFile(self.root / receipt['relative_path']) as original:
            forged = deepcopy(manifest)
            forged['streams'][0]['basis'] = forged['evidence']['controller']
            with self.assertRaisesRegex(ValueError, 'stream declarations'):
                evidence.check_authority(forged, original.read)
            wrong = deepcopy(manifest)
            wrong['evidence']['capture'] = wrong['evidence']['controller']
            with self.assertRaisesRegex(ValueError, 'different captures'):
                evidence.check_authority(wrong, original.read)
        self.assertTrue(any(s['format'] == 'codex-jsonl/1' for s in manifest['streams']))
        with zipfile.ZipFile(self.root / edition['archive_path']) as z:
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
