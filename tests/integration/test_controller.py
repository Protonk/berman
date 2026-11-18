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
