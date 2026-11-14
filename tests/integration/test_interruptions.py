"""Real process death and contention at the workflow's persistence boundaries."""

import json
import select
import signal
import subprocess
import sys
import zipfile

from berman import files, snapshots, verify
from tests.integration.test_records import RecordTests
from tests.support.paths import REPO
from tests.support.sessions import start


def state(root):
    return {
        str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mode, p.stat().st_mtime_ns)
        for p in root.rglob('*')
        if p.is_file() and not p.is_symlink()
    }


class InterruptionTests(RecordTests):
    def child(self, mode, connection='stewart'):
        return subprocess.run(
            [sys.executable, '-B', '-m', 'tests.support.interruption_worker', str(self.root), mode, connection],
            cwd=REPO,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def receipts(self, run='run-001'):
        return sorted((self.root / '.state/recovery' / run).glob('*/receipt.json'))

    def test_death_during_preparation_reuses_identity_and_quarantines_unpublished_writes(self):
        for boundary in ('reserve-before', 'reserve-after', 'snapshot-before', 'packet-after'):
            with self.subTest(boundary=boundary):
                self.setUp()
                result = self.child(boundary)
                self.assertEqual(result.returncode, -signal.SIGKILL, result.stderr)
                before = state(self.root)
                self.assertEqual(self.command('--json', 'status')[0], 0)
                self.assertEqual(state(self.root), before)
                self.load()
                result = self.workflow.new()
                self.assertEqual(result['run'], 'run-001')
                self.assertEqual(self.workflow.new()['run'], 'run-001')
                self.assertEqual([row['run'] for row in self.store.discover()], ['run-001'])
                self.assertEqual(list(self.store.folder('run-001').rglob('.capture-*')), [])
                self.assertEqual(list(self.store.folder('run-001').rglob('.snapshot-*')), [])
                if boundary in ('reserve-before', 'snapshot-before'):
                    self.assertTrue(self.receipts())
                    for receipt in self.receipts():
                        self.assertTrue((receipt.parent / 'material').exists())

    def test_death_during_preservation_retries_the_declared_ending_while_another_run_is_open(self):
        for boundary in ('closing-copy', 'journal-after', 'migration-after', 'after-index'):
            with self.subTest(boundary=boundary):
                self.setUp()
                self.workflow.new()
                start(self.store, 'run-001', end=True)
                self.workflow.new(keep_open=True)
                start(self.store, 'run-002')
                result = self.child(boundary)
                self.assertEqual(result.returncode, -signal.SIGKILL, result.stderr)
                declared = self.store.record('run-001')['ending']
                before = state(self.root)
                self.assertEqual(self.command('--json', 'status')[0], 0)
                self.assertEqual(state(self.root), before)
                if boundary in ('migration-after', 'after-index'):
                    pending = verify.recording_check(self.store)
                    self.assertEqual(pending['status'], 'not-established', pending)
                    self.assertEqual(state(self.root), before)
                code, output, error = self.command(
                    'finish', '--run', 'run-001', '--reason', 'Explicit synthetic ending'
                )
                self.assertEqual((code, error), (0, ''))
                result = json.loads(output)
                self.assertEqual(result['state'], 'archived')
                self.assertEqual(self.store.record('run-001')['ending'], declared)
                self.assertNotIn('ending', self.store.record('run-002'))
                self.assertEqual(verify.recording_check(self.store)['status'], 'passed')
                with zipfile.ZipFile(result['package']['path']) as archive:
                    self.assertFalse(any('/.capture-' in name or '/.snapshot-' in name for name in archive.namelist()))
                if boundary == 'closing-copy':
                    self.assertTrue(result['recovered_writes'])
                    material = [receipt.parent / 'material' for receipt in self.receipts()]
                    self.assertTrue(
                        any(
                            path.read_bytes()
                            == (self.store.folder('run-001') / 'preparation/INSTRUCTIONS.md').read_bytes()
                            for path in material
                            if path.is_file()
                        )
                    )

    def test_death_during_quarantine_loses_no_bytes_and_does_not_consume_a_run(self):
        for boundary in ('quarantine-before', 'quarantine-after'):
            with self.subTest(boundary=boundary):
                self.setUp()
                folder = self.store.folder('run-001')
                folder.mkdir(parents=True)
                orphan = folder / '.capture-unpublished'
                orphan.write_bytes(b'Unpublished bytes retained for investigation')
                result = self.child(boundary)
                self.assertEqual(result.returncode, -signal.SIGKILL, result.stderr)
                self.assertEqual(self.workflow.new()['run'], 'run-001')
                self.assertEqual(self.workflow.new()['run'], 'run-001')
                self.assertFalse(orphan.exists())
                self.assertEqual(len(self.receipts()), 1)
                self.assertEqual(
                    (self.receipts()[0].parent / 'material').read_bytes(),
                    b'Unpublished bytes retained for investigation',
                )

    def test_capture_death_leaves_a_coherent_committed_snapshot_and_no_ending(self):
        for boundary in ('capture-staged', 'capture-write', 'capture-committed'):
            with self.subTest(boundary=boundary):
                self.setUp()
                self.workflow.new()
                start(self.store, 'run-001')
                session = self.store.field / 'run-001/session'
                old = snapshots.read(session)['raw']
                source = self.store.home / 'sessions/fixture-run-001.jsonl'
                with source.open('a') as stream:
                    stream.write(
                        json.dumps({'type': 'event_msg', 'payload': {'message': 'durable later activity'}}) + '\n'
                    )
                expected = source.read_bytes()
                result = self.child(boundary)
                self.assertEqual(result.returncode, -signal.SIGKILL, result.stderr)
                before = state(self.root), state(self.store.field)
                self.assertEqual(snapshots.read(session)['raw'], expected if boundary == 'capture-committed' else old)
                self.assertTrue(self.store.collector.status('run-001')['snapshot_consistent'])
                self.assertFalse(self.workflow.status()['runs'][0]['ending_declared'])
                self.assertEqual((state(self.root), state(self.store.field)), before)
                self.store.collector.hook(
                    {
                        'session_id': 'fixture-run-001',
                        'turn_id': 'two',
                        'hook_event_name': 'Stop',
                        'transcript_path': str(source),
                    }
                )
                self.assertEqual(snapshots.read(session)['raw'], expected)
                self.assertEqual(len(list((session / 'snapshots').iterdir())), 1)
                self.assertFalse(self.workflow.status()['runs'][0]['ending_declared'])
                retained = self.root / '.state/capture-staging/run-001'
                if boundary != 'capture-committed':
                    self.assertTrue(any(p.read_bytes() == expected for p in retained.rglob('transcript.jsonl')))
                result = self.workflow.finish('Explicit synthetic ending', 'run-001')
                self.assertEqual(list((self.root / 'runs/run-001/session').glob('.capture-*')), [])
                self.assertEqual(verify.recording_check(self.store)['status'], 'passed')


class ProcessContentionTests(RecordTests):
    def held(self, connection):
        process = subprocess.Popen(
            [sys.executable, '-B', '-m', 'tests.support.interruption_worker', str(self.root), 'hold', connection],
            cwd=REPO,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        def cleanup():
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=10)

        self.addCleanup(cleanup)
        self.assertTrue(select.select([process.stdout], [], [], 10)[0], 'child did not acquire the connection lock')
        self.assertEqual(process.stdout.readline().strip(), 'locked')
        return process

    def second(self, connection):
        return subprocess.run(
            [sys.executable, '-B', '-m', 'tests.support.interruption_worker', str(self.root), 'new', connection],
            cwd=REPO,
            capture_output=True,
            text=True,
            timeout=20,
        )

    def test_same_connection_contends_then_reuses_the_prepared_identity(self):
        first = self.held('stewart')
        second = self.second('stewart')
        self.assertEqual(second.returncode, 2)
        self.assertIn('Another Berman operation is using connection stewart', second.stderr)
        out, error = first.communicate('continue\n', timeout=20)
        self.assertEqual((first.returncode, error), (0, ''))
        self.assertEqual(json.loads(out)['run'], 'run-001')
        self.assertEqual(json.loads(self.second('stewart').stdout)['run'], 'run-001')

    def test_two_connections_progress_concurrently_with_distinct_global_identities(self):
        config = files.read(self.root / 'berman.json')
        config['connections']['other'] = dict(
            config['connections']['stewart'], definition='connections/other.json', plan='plans/other.json'
        )
        files.write(self.root / 'berman.json', config)
        definition = files.read(self.root / 'connections/stewart/connection.json')
        files.write(
            self.root / 'connections/other.json', dict(definition, id='other', commission='stewart/COMMISSION.md')
        )
        plan = files.read(self.root / 'plans/stewart-fieldwork-roles/plan.json')
        files.write(self.root / 'plans/other.json', dict(plan, id='other', connection='other', commitment=None))
        self.edit('plans/stewart-fieldwork-roles/plan.json', commitment=None)
        for path in (self.root / 'recipes').glob('*/recipe.json'):
            data = files.read(path)
            data['connections'].append('other')
            files.write(path, data)
        self.load()
        first = self.held('stewart')
        second = self.second('other')
        self.assertEqual(second.returncode, 0, second.stderr)
        out, error = first.communicate('continue\n', timeout=20)
        self.assertEqual((first.returncode, error), (0, ''))
        results = [json.loads(out), json.loads(second.stdout)]
        self.assertEqual({row['run'] for row in results}, {'run-001', 'run-002'})
        self.assertEqual({row['connection'] for row in results}, {'stewart', 'other'})
        self.assertEqual(verify.workspace(self.store, None)['status'], 'passed')
