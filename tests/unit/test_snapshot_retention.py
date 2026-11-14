"""Compaction must establish every retained-byte obligation before deleting a generation."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from berman import files, snapshots


class SnapshotRetentionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.session, self.staging = self.root / 'session', self.root / 'staging'
        self.number = 0
        self.previous = None

    def publish(self, raw, tail=b'', interrupt=False, prefix=None):
        self.number += 1
        reader = b'Reading: ' + raw
        metadata = {
            'run': 'run-001',
            'session_id': 'synthetic-session',
            'event': 'Stop',
            'captured_at_utc': str(self.number),
            'sha256': files.digest(raw),
            'saved_bytes': len(raw),
            'reader_sha256': files.digest(reader),
            'previous_snapshot_is_prefix': raw.startswith(self.previous) if self.previous is not None else None,
            'omitted_partial_tail_bytes': len(tail),
            'partial_tail_file': 'partial-tail-' + files.digest(tail) + '.bin' if tail else None,
            'partial_tail_sha256': files.digest(tail) if tail else None,
        }
        if prefix is not None:
            metadata['previous_snapshot_is_prefix'] = prefix
        if interrupt:
            recover = snapshots.recover
            calls = []

            def stop_after_pointer(session, staging):
                calls.append(True)
                if len(calls) == 2:
                    raise OSError('stopped after commit')
                return recover(session, staging)

            with patch.object(snapshots, 'recover', side_effect=stop_after_pointer):
                with self.assertRaisesRegex(OSError, 'stopped after commit'):
                    snapshots.publish(self.session, metadata, raw, reader, tail, self.staging)
        else:
            snapshots.publish(self.session, metadata, raw, reader, tail, self.staging)
        self.previous = raw
        return metadata

    def tree(self):
        return {str(p.relative_to(self.session)): p.read_bytes() for p in self.session.rglob('*') if p.is_file()}

    def test_lost_displaced_bytes_cannot_authorize_deleting_the_last_old_generation(self):
        old = self.publish(b'original\n')
        original = snapshots.resolve(self.session)
        self.publish(b'replacement\n', interrupt=True)
        (self.session / 'displaced' / (old['sha256'] + '.jsonl')).unlink()
        before = self.tree()
        for action in (lambda: snapshots.read(self.session), lambda: snapshots.recover(self.session, self.staging)):
            with self.assertRaises(FileNotFoundError):
                action()
            self.assertEqual(self.tree(), before)
        self.assertEqual((original / 'transcript.jsonl').read_bytes(), b'original\n')

    def test_displaced_raw_and_reader_remain_required_after_old_directory_is_gone(self):
        old = self.publish(b'original\n')
        self.publish(b'replacement\n')
        self.assertEqual(len(list((self.session / 'snapshots').iterdir())), 1)
        for digest, extension in ((old['sha256'], '.jsonl'), (old['reader_sha256'], '.txt')):
            with self.subTest(extension=extension):
                path = self.session / 'displaced' / (digest + extension)
                data = path.read_bytes()
                path.write_bytes(b'corrupted retained bytes')
                with self.assertRaisesRegex(ValueError, 'Retained capture bytes differ'):
                    snapshots.read(self.session)
                path.write_bytes(data)
        self.assertEqual(snapshots.read(self.session)['raw'], b'replacement\n')

    def test_prior_partial_tail_is_required_after_prefix_compaction(self):
        old = self.publish(b'original\n', tail=b'partial')
        self.publish(b'original\ncompleted\n')
        path = self.session / 'tails' / (old['partial_tail_sha256'] + '.bin')
        path.unlink()
        before = self.tree()
        with self.assertRaises(FileNotFoundError):
            snapshots.recover(self.session, self.staging)
        self.assertEqual(self.tree(), before)

    def test_current_tail_can_satisfy_a_repeated_prior_tail_obligation(self):
        old = self.publish(b'original\n', tail=b'partial')
        self.publish(b'original\nmore\n', tail=b'partial')
        (self.session / 'tails' / (old['partial_tail_sha256'] + '.bin')).unlink()
        self.assertEqual(snapshots.read(self.session)['tail'], b'partial')

    def test_partially_retired_directory_uses_committed_history_to_finish_cleanup(self):
        self.publish(b'original\n')
        original = snapshots.resolve(self.session)
        self.publish(b'original\ncontinued\n', interrupt=True)
        (original / 'capture.json').unlink()
        (original / 'transcript.jsonl').unlink()
        snapshots.recover(self.session, self.staging)
        self.assertFalse(original.exists())
        self.assertEqual(snapshots.read(self.session)['raw'], b'original\ncontinued\n')

    def test_prefix_claim_is_checked_against_bytes_before_publication(self):
        self.publish(b'original\n')
        before = self.tree()
        with self.assertRaisesRegex(ValueError, 'prefix relationship differs'):
            self.publish(b'replacement\n', prefix=True)
        self.assertEqual(self.tree(), before)

    def test_repeated_nonprefix_obligations_hash_each_retained_file_once_per_read(self):
        first = self.publish(b'first\n')
        self.publish(b'second\n')
        self.publish(b'first\n')
        self.publish(b'second\n')
        path = self.session / 'displaced' / (first['sha256'] + '.jsonl')
        with patch.object(files, 'regular', wraps=files.regular) as regular:
            snapshots.read(self.session)
        self.assertEqual(sum(call.args == (path,) for call in regular.call_args_list), 1)


if __name__ == '__main__':
    unittest.main()
