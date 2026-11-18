"""Exercise the guest command boundary and lost-ack recovery in a synthetic study."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

from berman import files, protocol
from berman.connector import stable_id
from berman.controller import Controller


class ControllerTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.ident = str(uuid4())
        files.write(self.root / 'collection.json', {'id': self.ident, 'reserved_through': 18})
        files.write(
            self.root / 'connections/a/connection.json',
            {
                'schema': 'berman.connection/1',
                'id': 'a',
                'default_recipe': 'baseline',
                'description': 'Private interpretation',
                'documents': ['README.md'],
            },
        )
        files.write(
            self.root / 'recipes/baseline/recipe.json',
            {
                'schema': 'berman.recipe/2',
                'id': 'baseline',
                'instructions': 'packet.md',
                'connections': [],
                'coverage': ['notes'],
                'recording_protocol': 'work-accounts/1',
                'meaning': 'Private recipe rationale',
            },
        )
        (self.root / 'recipes/baseline/packet.md').write_text('Write an account in {{notes_directory}}.')
        self.guest = Controller(self.root)
        self.commission = self.request('commission', connection='a', harness='codex')

    def request(self, command, **fields):
        return dict(
            protocol=protocol.VERSION, collection_id=self.ident, command=command, operation=str(uuid4()), **fields
        )

    def call(self, request, body=b''):
        return self.guest.execute(request, BytesIO(body))

    def test_ack_loss_concurrent_commission_and_worker_projection(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            receipts = list(pool.map(self.call, [self.commission] * 6))
        self.assertEqual(receipts, [receipts[0]] * 6)
        self.assertEqual(receipts[0]['run'], 'run-019')
        self.assertNotIn('Private', str(receipts[0]))
        with self.assertRaisesRegex(ValueError, 'reused'):
            self.call(dict(self.commission, harness='claude'))
        another = self.call(self.request('commission', connection='a', harness='claude'))
        self.assertEqual(another['run'], 'run-020')
        request = self.request('status')
        with self.assertRaisesRegex(ValueError, 'identity mismatch'):
            self.call(dict(request, collection_id=str(uuid4())))
        self.assertEqual(stable_id(self.ident, 'commission', 'opening'), stable_id(self.ident, 'commission', 'opening'))

    def test_cut_transfer_retry_attribution_and_independent_custody(self):
        a = self.call(self.commission)['result']
        run = a['run']
        raw = b'one retained note'
        row = dict(path='packet/notes/first.md', size=len(raw), sha256=files.digest(raw))
        ending = self.request('ending', run=run, reason='Explicit test ending')
        manifest = {k: a[k] for k in ('run', 'collection_id', 'operation', 'harness', 'connection')}
        manifest.update(
            schema='berman.collection/1',
            assignment_sha256=files.digest_json(a),
            ending_id=ending['operation'],
            limitations=['No native source'],
            members=[row],
        )
        capture = self.request('capture', run=run, observation=manifest)
        with self.assertRaisesRegex(ValueError, 'explicit ending'):
            self.call(capture)
        self.call(ending)
        self.assertNotIn('Explicit test ending', str(self.call(self.request('closeout', run=run))))
        for k in ('ending_id', 'operation', 'assignment_sha256', 'collection_id'):
            wrong = deepcopy(capture)
            wrong['observation'][k] = str(uuid4())
            with self.assertRaises(ValueError):
                self.call(wrong)
        self.call(capture)
        transfer = self.request('transfer', run=run, **row)
        accept = self.request('accept', run=run)
        for partial in (raw[:-1], raw + b'x', b'x' * len(raw)):
            with self.assertRaises(ValueError):
                self.call(transfer, partial)
            with self.assertRaisesRegex(ValueError, 'missing transfer'):
                self.call(accept)
        receipt = self.call(transfer, raw)
        self.assertEqual(self.call(transfer, raw), receipt)
        self.assertIsNone(self.call(self.request('status', run=run))['result']['accepted'])
        self.call(accept)
        preserve = self.request('preserve', run=run)
        self.assertEqual(self.call(preserve), self.call(preserve))
        self.call(self.request('publish', run=run))
        verified = self.call(self.request('verify', run=run))['result']
        self.assertEqual(verified['edition']['mode'], 'verified')
        self.assertIn('No native source', verified['recording']['coverage']['unknown'])

    def observed(self, a, schema, **fields):
        value = {k: a[k] for k in ('run', 'collection_id', 'operation', 'harness', 'connection')}
        value.update(schema=f'berman.{schema}/1', assignment_sha256=files.digest_json(a), **fields)
        return value

    def collected(self, a, declaration, rows, **fields):
        return self.observed(a, 'collection', limitations=['No native source'], members=rows, **declaration, **fields)

    def frozen_run(self, payloads):
        """A commissioned, ended run with its inventory recorded and nothing transferred."""
        a = self.call(self.commission)['result']
        rows = [dict(path=name, size=len(raw), sha256=files.digest(raw)) for name, raw in payloads.items()]
        ending = self.request('ending', run=a['run'], reason='Explicit test ending')
        self.call(ending)
        self.call(
            self.request(
                'capture', run=a['run'], observation=self.collected(a, {'ending_id': ending['operation']}, rows)
            )
        )
        return a, rows, ending

    def test_missing_evidence_is_named_and_accepted_only_with_a_reason(self):
        payloads = {'packet/notes/kept.md': b'kept', 'packet/notes/lost.md': b'lost on the host'}
        a, rows, _ = self.frozen_run(payloads)
        run = a['run']
        kept = self.request('transfer', run=run, **rows[0])
        self.call(kept, payloads['packet/notes/kept.md'])
        status = self.call(self.request('status', run=run))['result']
        self.assertEqual(['packet/notes/lost.md'], status['outstanding'])
        with self.assertRaisesRegex(ValueError, r'missing transfer: packet/notes/lost.md \(never transferred\)'):
            self.call(self.request('accept', run=run))
        with self.assertRaisesRegex(ValueError, 'requires a reason'):
            self.call(self.request('accept', run=run, missing_reason='  '))
        accept = self.request('accept', run=run, missing_reason='The staged note was damaged on the host')
        accepted = self.call(accept)['result']
        self.assertEqual(1, accepted['accepted_members'])
        self.assertEqual(['packet/notes/lost.md'], [row['path'] for row in accepted['missing']])
        self.assertEqual(accepted, self.call(accept)['result'])
        recording = self.call(self.request('preserve', run=run))['result']
        self.assertTrue(
            any('not accepted into custody: packet/notes/lost.md' in text for text in recording['coverage']['unknown'])
        )
        self.call(self.request('publish', run=run))
        verified = self.call(self.request('verify', run=run))['result']
        self.assertEqual('passed', verified['recording']['integrity'])
        self.assertEqual('verified', verified['edition']['mode'])

    def test_complete_evidence_is_not_accepted_as_incomplete(self):
        a, rows, _ = self.frozen_run({'packet/notes/only.md': b'only'})
        self.call(self.request('transfer', run=a['run'], **rows[0]), b'only')
        with self.assertRaisesRegex(ValueError, 'Every observed member is verified'):
            self.call(self.request('accept', run=a['run'], missing_reason='Nothing is missing'))

    def test_lost_or_damaged_guest_object_is_resent_and_the_repair_recorded(self):
        raw = b'a note the store will lose'
        a, rows, _ = self.frozen_run({'packet/notes/first.md': raw})
        run = a['run']
        transfer = self.request('transfer', run=run, **rows[0])
        receipt = self.call(transfer, raw)
        stored = self.root / 'controller' / 'objects' / rows[0]['sha256']
        stored.unlink()
        self.assertEqual(['packet/notes/first.md'], self.call(self.request('status', run=run))['result']['outstanding'])
        with self.assertRaisesRegex(ValueError, 'absent from the guest store'):
            self.call(self.request('accept', run=run))
        self.assertEqual(receipt, self.call(transfer, raw))
        self.assertEqual(raw, stored.read_bytes())
        stored.chmod(0o600)
        stored.write_bytes(b'x' * len(raw))
        self.assertEqual([], self.call(self.request('status', run=run))['result']['outstanding'])
        with self.assertRaisesRegex(ValueError, 'damaged in the guest store'):
            self.call(self.request('accept', run=run))
        self.assertEqual(receipt, self.call(transfer, raw))
        self.assertEqual(raw, stored.read_bytes())
        self.assertEqual(1, len(list(stored.parent.glob('.damaged-*'))))
        repairs = self.call(self.request('status', run=run))['result']['repairs']
        self.assertEqual(['stored', 'replaced damaged bytes'], [row['outcome'] for row in repairs])
        self.call(self.request('accept', run=run))

    def test_later_sittings_and_a_supplement_reach_custody_beside_the_first_recording(self):
        raw, later = b'first account', b'account written after the ending'
        a, rows, ending = self.frozen_run({'packet/notes/first.md': raw})
        run = a['run']
        with self.assertRaisesRegex(ValueError, 'numbered from 2'):
            self.call(self.request('sitting', run=run, number=1, observation=self.observed(a, 'launch')))
        sitting = self.request('sitting', run=run, number=2, observation=self.observed(a, 'launch', sitting=2))
        self.assertEqual(self.call(sitting), self.call(sitting))
        with self.assertRaisesRegex(ValueError, 'already recorded'):
            self.call(self.request('sitting', run=run, number=2, observation=self.observed(a, 'launch', sitting=2)))
        self.call(self.request('transfer', run=run, **rows[0]), raw)
        self.call(self.request('accept', run=run))
        declare = self.request('supplement', run=run, reason='The worker returned after the ending')
        declaration = self.call(declare)['result']
        self.assertEqual((1, declaration), (declaration['sequence'], self.call(declare)['result']))
        self.assertEqual(
            declare['operation'], self.call(self.request('closeout', run=run, supplement=1))['result']['operation']
        )
        row = dict(path='packet/notes/later.md', size=len(later), sha256=files.digest(later))
        ids = {'ending_id': ending['operation'], 'supplement_id': declare['operation'], 'sequence': 1}
        # Sent without its number it can only be taken for the first collection, which exists.
        with self.assertRaisesRegex(ValueError, 'already recorded'):
            self.call(self.request('capture', run=run, observation=self.collected(a, ids, [row])))
        with self.assertRaisesRegex(ValueError, 'different supplement'):
            wrong = dict(ids, supplement_id=str(uuid4()))
            self.call(self.request('capture', run=run, supplement=1, observation=self.collected(a, wrong, [row])))
        self.call(self.request('capture', run=run, supplement=1, observation=self.collected(a, ids, [row])))
        self.call(self.request('transfer', run=run, supplement=1, **row), later)
        self.call(self.request('accept', run=run, supplement=1))
        with self.assertRaisesRegex(ValueError, 'first collection before its supplement'):
            self.call(self.request('preserve', run=run, supplement=1))
        first = self.call(self.request('preserve', run=run))['result']
        preserve = self.request('preserve', run=run, supplement=1)
        second = self.call(preserve)['result']
        self.assertEqual(second, self.call(preserve)['result'])
        self.assertEqual(
            (1, f'custody/{run}/supplements/0001/recording.zip'), (second['supplement'], second['relative_path'])
        )
        self.assertNotEqual(first['package_sha256'], second['package_sha256'])
        self.assertTrue(any('Supplement 1 to' in text for text in second['coverage']['unknown']))
        one = self.call(self.request('publish', run=run))['result']
        two = self.call(self.request('publish', run=run, supplement=1))['result']
        self.assertNotEqual(one['selection_pointer'], two['selection_pointer'])
        verified = self.call(self.request('verify', run=run))['result']
        self.assertEqual('verified', verified['edition']['mode'])
        self.assertEqual(
            [(1, 'passed', 'verified')],
            [(s['sequence'], s['recording']['integrity'], s['edition']['mode']) for s in verified['supplements']],
        )
        status = self.call(self.request('status', run=run))['result']
        self.assertEqual([['capture', 'accept', 'preserve', 'publish']], [s['facts'] for s in status['supplements']])
        # The first recording is the bytes it was before the supplement existed.
        from berman import custody

        self.assertEqual(first['package_sha256'], custody.verify_recording(self.root, first)['package_sha256'])
