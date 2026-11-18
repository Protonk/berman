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

HISTORICAL = Path(__file__).parents[1] / 'fixtures/records/recording-2/valid/minimal.json'


class CustodyTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.root = self.base / 'guest'
        self.manifest = json.loads(HISTORICAL.read_bytes())
        self.ident = self.manifest['collection_id']
        files.write(self.root / 'collection.json', {'id': self.ident})

    def historical(self):
        # The link deliberately points outside the recording. Reading treats it as original data.
        row = {'path': 'alias', 'type': 'symlink', 'mode': '0o777', 'target': '/missing/on/guest'}
        self.manifest['entries'].append(dict(row, path='runs/run-001/alias'))
        self.manifest['custody']['inventory']['entries'].append(row)
        payload = {
            r['path'] + '/': (b'', evidence.mode(r)) for r in self.manifest['entries'] if r['type'] == 'directory'
        }
        payload['runs/run-001/alias'] = (row['target'].encode(), evidence.mode(row))
        payload['recording.json'] = (files.json_bytes(self.manifest), stat.S_IFREG | 0o644)
        path = self.base / 'old.zip'
        path.write_bytes(package.make_zip(payload, ''))
        return path

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

    def test_import_preserves_historical_bytes_unknowns_and_unsafe_link_as_data(self):
        source = self.historical()
        data = source.read_bytes()
        receipt = custody.import_recording(self.root, source, expected_sha256=files.digest(data))
        self.assertEqual(receipt, custody.import_recording(self.root, source, expected_sha256=files.digest(data)))
        source.unlink()
        verified = custody.verify_recording(self.root, receipt)
        self.assertEqual(verified['manifest'], self.manifest)
        self.assertEqual((self.root / receipt['relative_path']).read_bytes(), data)
        edition = custody.publish_edition(self.root, receipt)
        copy = self.base / 'detached.zip'
        shutil.copyfile(self.root / edition['archive_path'], copy)
        shutil.rmtree(self.root)
        self.assertEqual(package.verify_edition(copy, edition['archive_sha256'])['mode'], 'verified')
        with zipfile.ZipFile(copy) as z:
            self.assertEqual(z.read('berman-analysis/runs/run-001/alias'), b'/missing/on/guest')

    def test_split_retry_after_publication_interruption_and_detached_verification(self):
        state, blobs = self.inputs()
        with patch.object(package, 'publish_locked', side_effect=OSError('interrupted')), self.assertRaises(OSError):
            custody.preserve_and_publish(self.root, state, blobs)
        receipt = custody.preserve_split_run(self.root, state, blobs)
        state['facts']['preserve'] = receipt
        self.assertEqual(receipt, custody.preserve_split_run(self.root, state, blobs))
        self.assertEqual(receipt['coverage']['unknown'], ['Missing parent transcript'])
        next(iter(blobs.values())).unlink()
        edition = custody.publish_edition(self.root, receipt)
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
        data = self.historical().read_bytes()
        digest = files.digest(data)
        for raw, size in [(data[:-3], len(data)), (data + b'x', len(data)), (data, custody.MAX_PACKAGE + 1)]:
            with self.subTest(size=size), self.assertRaises(ValueError):
                custody.import_recording(self.root, io.BytesIO(raw), expected_sha256=digest, size=size)
        receipt = custody.import_recording(self.root, io.BytesIO(data), expected_sha256=digest, size=len(data))
        self.assertEqual(receipt['package_sha256'], digest)

    def test_import_bounds_paths_partial_bytes_and_foreign_collection(self):
        source = self.historical()
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
