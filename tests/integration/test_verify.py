"""berman verify on a co-located workspace: separate facts, verdicts and receipts, with a fake archive runner.

No test here starts a harness or a test suite, or reads a real harness home or runs/; Git runs only
in disposable repositories. The workspace and its runs are synthetic, written by
tests/support/legacy.py as release 0.26.3 wrote them. Verification reads them and settles nothing.
"""

from contextlib import redirect_stderr, redirect_stdout
from functools import partial
import io
import json
import shutil
import subprocess
import sys
import unittest
from unittest import mock

from berman import cli
from berman import verify
from berman import workspace as selection
from berman.files import Decision, digest, json_bytes, read, write
from berman.installation import PACKAGE as ROOT
from berman.presentation import plain
from tests.integration.test_connections import Base
from tests.support import legacy

BUILT = {'mode': 'verified', 'archive_path': 'archive/stewart-runs.zip', 'zip_members': 12, 'archive_sha256': '0' * 64}
PASSING_ARCHIVE = (0, json.dumps(BUILT, indent=2) + '\n', '')
FROZEN = 'f87241a6b9506590fffa4e2a964f37bb7f7c719e508f4024661b45c57f983a99'
ORDER = ['frozen-collector', 'recorded-hashes', 'workspace', 'settled', 'recordings', 'archive']
CLOSE = 'It has to be closed with the release that opened it (Berman 0.26.3); this version does not close'


class Runner:
    """A fake subprocess.run answering the archive builder's argv from its configured result."""

    def __init__(self, archive=PASSING_ARCHIVE):
        self.archive = archive
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        if 'unittest' in argv:
            raise AssertionError('Product verification must never run developer suites')
        code, stdout, stderr = self.archive
        return subprocess.CompletedProcess(argv, code, stdout, stderr)


class Fixture:
    """The fixture root doubles as the code root, holding a copy of the frozen collector."""

    def code(self):
        rel = '_legacy/capture_session.py'
        (self.root / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / rel, self.root / rel)

    def verify(self, runner=None, **kwargs):
        self.runner = runner or Runner()
        kwargs.setdefault('code', self.root)
        return verify.verify(self.root, run=self.runner, **kwargs)

    def snapshot(self):
        return sorted((str(p), p.lstat().st_mtime_ns, p.lstat().st_size) for p in [self.base, *self.base.rglob('*')])


class VerifyTests(Fixture, Base):
    def setUp(self):
        super().setUp()
        self.code()

    def test_a_workspace_at_rest_passes_every_check_without_touching_a_harness(self):
        self.finished()
        result = self.verify()
        self.assertEqual(list(result['checks']), ORDER)
        self.assertEqual(
            (result['verdict'], result['failed'], result['not_run'], result['not_established']), ('passed', [], [], [])
        )
        self.assertEqual(verify.EXIT[result['verdict']], 0)
        checks = result['checks']
        self.assertEqual(checks['archive']['details']['builder'], BUILT)
        self.assertEqual(checks['settled']['details']['unsettled'], [])
        self.assertTrue(all(c['meaning'] and c['required'] for c in checks.values()))
        self.assertEqual(result['python'], sys.executable)
        self.assertRegex(result['python_version'], r'^Python 3\.\d+\.\d+')
        # Counted here, not read back from the module: a dropped statement must fail this test.
        self.assertEqual(3, len(result['not_covered']))
        self.assertIn('Hook trust, native capture and native access', ' '.join(result['not_covered']))
        self.assertIsNone(result['record'])
        self.assertFalse(any('unittest' in argv for argv, _ in self.runner.calls))
        self.assertFalse((self.root / '.berman-locks').exists() or (self.root / '.berman.lock').exists())
        self.assertTrue(plain.verification(result).startswith('Berman verification: PASSED'))

    def test_unsettled_work_is_named_with_the_release_that_has_to_close_it_and_is_never_closed(self):
        self.finished()
        opened = legacy.prepare(self.store)
        legacy.start(self.store, opened)
        waiting = legacy.prepare(self.store, 'orchard')
        before = self.snapshot()
        result = self.verify()
        self.assertEqual(self.snapshot(), before, 'verification changed an unsettled workspace')
        settled = result['checks']['settled']
        self.assertEqual((settled['status'], settled['required']), ('not-established', True))
        self.assertEqual(
            settled['summary'], '2 unsettled; this version reads a co-located workspace and closes nothing in it'
        )
        first, second = settled['details']['unsettled']
        self.assertTrue(first.startswith(f'{opened}: its engagement is open: the packet is still in the packet root'))
        self.assertTrue(second.startswith(f'{waiting}: its prepared packet was never started or retired'))
        for line in (first, second):
            self.assertIn(CLOSE, line)
        self.assertEqual(settled['next'], verify.SETTLE)
        # An open engagement is not damage: the workspace itself reads cleanly, and the verdict is incomplete.
        self.assertEqual(result['checks']['workspace']['status'], 'passed')
        self.assertEqual(
            (result['verdict'], result['failed'], result['not_established'], verify.EXIT[result['verdict']]),
            ('incomplete', [], ['settled'], 3),
        )
        text = plain.verification(result)
        self.assertIn('Not established: settled', text)
        self.assertIn(first, text)
        # Neither run gained an ending, a closure or a package by being verified.
        for run in (opened, waiting):
            self.assertNotIn('ending', self.store.record(run))
            self.assertFalse((self.store.field / run / 'session/finalized.json').exists())
            self.assertFalse((self.root / 'runs' / run).exists())

    def test_frozen_collector_change_fails(self):
        copy = self.root / '_legacy/capture_session.py'
        copy.write_bytes(copy.read_bytes() + b'# changed\n')
        check = self.verify()['checks']['frozen-collector']
        self.assertEqual(check['status'], 'failed')
        self.assertIn(digest(copy.read_bytes()), check['summary'])
        self.assertIn(FROZEN, check['summary'])
        self.assertEqual(digest((ROOT / '_legacy/capture_session.py').read_bytes()), FROZEN)
        shutil.copy(ROOT / '_legacy/capture_session.py', copy)
        self.assertEqual(self.verify()['checks']['frozen-collector']['status'], 'passed')

    def test_recorded_hashes_cover_experiment_checksums_and_preparation_manifests(self):
        one, two, prep = self.root / 'experiments/one', self.root / 'experiments/two', self.root / 'inquiry/2026-prep'
        for path, text in (
            (one / 'a.txt', 'a\n'),
            (one / 'sub/b.txt', 'b\n'),
            (two / 'c.json', '{}\n'),
            (prep / 'ASSIGNMENT.md', 'Assignment\n'),
        ):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        write(one / 'checksums.json', {'a.txt': digest(b'a\n'), 'sub/b.txt': digest(b'b\n')})
        (two / 'experiment-checksums.sha256').write_text(digest(b'{}\n') + '  c.json\n\n')
        write(
            prep / 'MANIFEST.json',
            {'files': [{'path': 'ASSIGNMENT.md', 'bytes': 11, 'sha256': digest(b'Assignment\n')}]},
        )
        # A revision manifest describes files packed in a ZIP elsewhere; it is not checked against this folder.
        write(prep / 'revisions/001/MANIFEST.json', {'files': [{'path': 'packed-only.txt', 'sha256': '0' * 64}]})
        skip = verify.SKIPPABLE
        check = self.verify(skip=skip)['checks']['recorded-hashes']
        self.assertEqual(
            (check['status'], check['summary'], check['details']['hashes']), ('passed', '4 recorded hashes match', 4)
        )
        (one / 'a.txt').write_text('changed\n')
        (two / 'c.json').unlink()
        check = self.verify(skip=skip)['checks']['recorded-hashes']
        self.assertEqual(
            (check['status'], check['details']['mismatched'], check['details']['missing']),
            ('failed', ['experiments/one/a.txt'], ['experiments/two/c.json']),
        )
        self.assertEqual(check['summary'], '2 of 4 recorded hashes do not hold')
        (one / 'checksums.json').write_text('{broken')
        check = self.verify(skip=skip)['checks']['recorded-hashes']
        self.assertEqual(check['status'], 'failed')
        self.assertIn('cannot be read', check['summary'])

    def test_the_archive_check_accepts_an_external_workspace(self):
        result = self.verify(code=ROOT)
        self.assertEqual(result['checks']['archive']['status'], 'passed')
        self.assertTrue(any('--root' in argv and str(self.root) in argv for argv, _ in self.runner.calls))
        self.assertEqual(result['verdict'], 'passed')
        stale = 'Traceback (most recent call last):\n  File "build_archive.py"\nValueError: Generated file is missing or stale: review/x\n'
        result = self.verify(Runner(archive=(1, '', stale)), record='experiments/stale')
        archive = result['checks']['archive']
        self.assertEqual(
            (archive['status'], archive['summary']),
            ('failed', 'ValueError: Generated file is missing or stale: review/x'),
        )
        self.assertIn('rebuild once', archive['next'])
        # The builder's whole message is kept; an empty stdout is not.
        self.assertEqual((self.root / 'experiments/stale/archive-verify.stderr.txt').read_text(), stale)
        self.assertEqual(result['record']['files'], ['verification.json', 'archive-verify.stderr.txt'])
        argv, kwargs = next(call for call in self.runner.calls if 'archive' in call[0])
        self.assertEqual(
            (argv, kwargs['cwd']),
            ([sys.executable, '-I', '-B', '-m', 'berman', '--root', str(self.root), 'archive', '--verify'], self.root),
        )
        self.assertEqual(result['verdict'], 'failed')
        archive = self.verify(Runner(archive=(0, 'not json', '')))['checks']['archive']
        self.assertEqual(
            (archive['status'], archive['summary']), ('failed', 'The archive builder printed no JSON summary')
        )

    def test_skip_leaves_checks_unrun_without_subprocesses(self):
        result = self.verify(skip=verify.SKIPPABLE)
        self.assertEqual(self.runner.calls, [])
        self.assertEqual((result['verdict'], result['not_run']), ('incomplete', ['recordings', 'archive']))
        self.assertEqual(result['checks']['archive']['next'], 'berman verify without --skip archive')
        with self.assertRaisesRegex(Decision, 'Only these checks can be skipped'):
            self.verify(skip=['workspace'])

    def test_record_writes_receipts_once_and_refuses_preserved_locations(self):
        result = self.verify(record='experiments/check-1')
        folder = self.root / 'experiments/check-1'
        receipts = ['verification.json', 'archive-verify.json']
        self.assertEqual(result['record'], {'directory': str(folder), 'files': receipts})
        self.assertEqual(sorted(p.name for p in folder.iterdir()), sorted(receipts))
        self.assertEqual((folder / 'verification.json').read_bytes(), json_bytes(result))
        self.assertEqual(read(folder / 'archive-verify.json'), BUILT)
        (self.root / 'experiments/link').symlink_to(self.root / 'runs', target_is_directory=True)
        refused = [
            folder,
            'experiments/check-1',
            'runs/x',
            'operations/x',
            'experiments',
            'experiments/x/collection/y',
            'experiments/x/Collection/y',
            'experiments/x/COLLECTION',
            'experiments/collection',
            'experiments/../runs/x',
            'experiments/link/x',
            self.store.field / 'x',
            self.home / 'x',
            self.base / 'x',
        ]
        for path in refused:
            runner = Runner()
            with self.assertRaises(Decision, msg=str(path)):
                verify.verify(self.root, record=path, run=runner, code=self.root)
            self.assertEqual(runner.calls, [], path)
        # A case variant names the preserved collection itself on a case-insensitive volume.
        (self.root / 'experiments/kept/collection').mkdir(parents=True)
        for variant in ('collection', 'Collection', 'cOLLECTION'):
            with self.assertRaisesRegex(Decision, 'preserved collection'):
                verify.evidence_folder(self.root, f'experiments/kept/{variant}/verify-new')
        self.assertEqual(list((self.root / 'experiments/kept/collection').iterdir()), [])
        self.assertEqual(
            verify.evidence_folder(self.root, self.root / 'experiments/kept/later'),
            self.root / 'experiments/kept/later',
        )

    def test_verify_without_record_writes_nothing(self):
        legacy.prepare(self.store)
        legacy.prepare(self.store, 'orchard')
        before = self.snapshot()
        result = self.verify()
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(result['checks']['workspace']['details']['outstanding_runs'], ['run-001', 'run-002'])
        self.assertEqual(result['checks']['workspace']['status'], 'passed')

    def test_a_problem_run_without_a_connection_fails_the_workspace_check(self):
        # A damaged support marker has no connection, so no connection's own problems list it.
        marker = self.store.folder('run-900101') / 'operation.json'
        marker.parent.mkdir(parents=True)
        marker.write_text('{broken')
        result = self.verify()
        workspace = result['checks']['workspace']
        self.assertEqual(
            (result['verdict'], result['failed'], workspace['status']),
            ('failed', ['workspace', 'recordings'], 'failed'),
        )
        self.assertEqual(workspace['details']['outstanding_runs'], ['run-900101'])
        self.assertEqual(len(workspace['details']['problems']), 1)
        self.assertTrue(workspace['details']['problems'][0].startswith('no connection: Invalid operation record'))
        for connection in ('stewart', 'orchard'):
            self.assertEqual(self.status()['connections'][connection]['problems'], [])
        # What a damaged record awaits is unknown, which is not the same as nothing.
        settled = result['checks']['settled']
        self.assertEqual(
            (settled['status'], settled['summary']),
            ('not-established', 'Unknown while the workspace check reports problems'),
        )
        self.assertIn('no connection: Invalid operation record', plain.verification(result))

    def test_broken_workspace_still_checks_the_installed_collector(self):
        (self.root / 'berman.json').write_text('{broken')
        result = self.verify()
        checks = result['checks']
        self.assertEqual(checks['workspace']['status'], 'failed')
        self.assertTrue(all(checks[name]['status'] == 'not-run' for name in ('settled', 'recordings')))
        self.assertEqual(sum('unittest' in argv for argv, _ in self.runner.calls), 0)
        self.assertEqual((result['python'], result['verdict']), (sys.executable, 'failed'))
        self.assertEqual(checks['frozen-collector']['status'], 'passed')

    def test_optional_plans_and_selected_plan_errors_have_distinct_verification_results(self):
        config = read(self.root / 'berman.json')
        del config['connections']['orchard']['plan']
        write(self.root / 'berman.json', config)
        self.reload()
        check = self.verify()['checks']['workspace']
        self.assertEqual(check['status'], 'passed')
        self.assertEqual(check['details']['plans']['orchard'], {'plan': None, 'next_step': None, 'next_recipe': None})
        self.assertEqual(check['details']['plans']['stewart']['next_recipe'], 'work-accounts')
        (self.root / 'plans/stewart-fieldwork-roles/plan.json').unlink()
        self.reload()
        check = self.verify()['checks']['workspace']
        self.assertEqual(check['status'], 'failed')
        self.assertEqual(len(check['details']['problems']), 1)
        problem = self.status()['connections']['stewart']['plan_error']
        self.assertEqual(check['details']['problems'], ['stewart: ' + problem])

    def test_cli_verify_json_text_and_exit_codes(self):
        def main(*argv):
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = cli.main(['--root', str(self.root), *argv])
            return code, out.getvalue(), err.getvalue()

        with mock.patch.object(verify, 'verify', partial(verify.verify, run=Runner(), code=self.root)):
            code, out, _ = main('--json', 'verify', '--skip', 'archive')
            self.assertEqual((code, json.loads(out)['not_run']), (3, ['archive']))
            code, out, _ = main('verify')
            self.assertEqual(code, 0)
            self.assertIn('Berman verification: PASSED', out)
            legacy.prepare(self.store)
            code, out, _ = main('verify')
            self.assertEqual(code, 3)
            self.assertIn('Berman verification: INCOMPLETE', out)
            self.assertIn('Not established: settled', out)
            self.assertIn('next: ' + verify.SETTLE, out)
            code, out, err = main('--json', 'verify', '--record', 'runs/x')
            self.assertEqual((code, out, json.loads(err)['state']), (2, '', 'needs-attention'))
            self.assertFalse((self.root / 'runs/x').exists())
            # verify runs before the workspace loads, so a broken berman.json is a failed check, not a refusal.
            (self.root / 'berman.json').write_text('{broken')
            code, out, _ = main('--json', 'verify')
            self.assertEqual((code, json.loads(out)['checks']['workspace']['status']), (1, 'failed'))
        for removed in (['verify', '--skip', 'workspace'], ['verify', '--native-access']):
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                cli.main(removed)


class EditionSettingTests(Base):
    def configure(self, change):
        config = read(self.root / 'berman.json')
        change(config)
        write(self.root / 'berman.json', config)
        self.reload()

    def test_the_workspace_names_the_edition_it_maintains(self):
        self.assertEqual((self.store.edition, selection.edition(self.root)), ('connections', 'connections'))
        self.configure(lambda c: c.update(edition='stewart'))
        self.assertEqual((self.store.edition, selection.edition(self.root)), ('stewart', 'stewart'))
        with self.assertRaisesRegex(ValueError, 'reading edition'):
            self.configure(lambda c: c.update(edition='another'))
        self.assertEqual(selection.edition(self.base / 'missing'), 'connections')


if __name__ == '__main__':
    unittest.main()
