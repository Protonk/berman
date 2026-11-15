"""berman verify: separate facts, verdicts and receipts, using fake runners and disposable roots only.

No test here starts Codex or a test suite, or reads the real Codex home or runs/; Git runs only in
disposable repositories, and native access runs against a simulated Codex. The recursion guard
makes a test that forgot its fake runner fail instead of rerunning the suite that contains it.
"""
from contextlib import redirect_stderr, redirect_stdout
from functools import partial
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from berman.files import Decision, digest, digest_json, json_bytes, read, write
from berman.harnesses.codex import COMMON_CHECKS, PROTECTED
from berman.records import Store
from berman.installation import PACKAGE as ROOT
from berman.capture import adapters
from berman import cli
from berman import verify
from berman.presentation import plain
from tests.support.workspace import workspace
from tests.integration.test_connections import Base

BUILT = {'mode': 'verified', 'archive_path': 'archive/stewart-runs.zip', 'zip_members': 12, 'archive_sha256': '0' * 64}
PASSING_ARCHIVE = (0, json.dumps(BUILT, indent=2) + '\n', '')
FROZEN = 'f87241a6b9506590fffa4e2a964f37bb7f7c719e508f4024661b45c57f983a99'
ORDER = [ 'frozen-collector', 'recorded-hashes', 'workspace', 'recordings', 'hook-trust:berman-codex', 'native-capture:berman-codex',
         'hook-trust:berman-claude', 'native-capture:berman-claude', 'archive', 'native-access']


def passing(store, folder):
    (folder / 'report.json').write_text('{"access_passed": true}\n')
    return {'access_passed': True}


class Runner:
    """A fake subprocess.run answering each kind of argv from its configured result."""

    def __init__(self, archive=PASSING_ARCHIVE):
        self.archive = archive
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        if argv[1:] == ['--version']:
            return subprocess.CompletedProcess(argv, 0, 'Python 3.14.0\n', '')
        if 'unittest' in argv:
            raise AssertionError('Product verification must never run developer suites')
        code, stdout, stderr = self.archive
        return subprocess.CompletedProcess(argv, code, stdout, stderr)


class Fixture:
    """The fixture root doubles as the code root, with a recorded hook review written directly."""

    def code(self):
        rel = '_legacy/capture_session.py'
        (self.root / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / rel, self.root / rel)

    def trust(self, captures=('berman-codex',)):
        """Installed router hooks, a review observation and both bases; no Codex binary runs."""
        template = adapters.definition(self.store, 'berman-codex')
        write(self.root / 'hooks/berman-capture.hooks.json', template)
        write(self.home / 'hooks.json', template)
        folder = self.root / 'hooks/verification/observations/fixture'
        write(folder / 'installed-hooks.json', template)
        (folder / 'hooks.terminal').write_text('Fixture /hooks review record\n')
        observed = {event: {'enabled': True, 'trust': 'Trusted', 'source': '~/.codex/hooks.json'}
                    for event in adapters.EVENTS}
        write(folder / 'hook-trust.json', {'schema': 'berman.hook-trust-observation/1', 'evidence': 'hooks.terminal',
                                           'evidence_sha256': digest(b'Fixture /hooks review record\n'),
                                           'observed': observed})
        for name, adapter in adapters.ADAPTERS.items():
            current = adapters.implementation(name)
            successful = None
            if name in captures or name == 'berman-claude':
                path = self.root / f'fixture-captures/{name}.jsonl'
                path.parent.mkdir(exist_ok=True)
                path.write_text(''.join(json.dumps({'event': e}) + '\n' for e in ('UserPromptSubmit', 'Stop', 'SessionEnd')))
                successful = {'path': f'fixture-captures/{name}.jsonl', 'required_events': ['Stop', 'SessionEnd'],
                              'run': 'run-900101', 'session_id': 'fixture-session',
                              'trial_receipt': 'experiments/fixture-trial/trial.json', 'implementation_sha256': current}
            if name == 'berman-claude':
                from berman.harnesses import claude
                write(self.root / adapter['basis'], {
                    'schema': claude.SCHEMA, 'adapter': name, 'implementation_sha256': current,
                    'helpers_sha256': claude.helpers(), 'evidence_sha256': {}, 'hook_review': None,
                    'configuration': {'policy': {}, 'harness': {}, 'context': {}},
                    'successful_capture': successful, 'meaning': 'Fixture Claude capture'})
                continue
            write(self.root / adapter['basis'], {
                'schema': 'berman.capture-verification/1', 'adapter': name, 'definition': adapter['definition'],
                'definition_sha256': digest_json(template), 'implementation_sha256': current,
                'installed_hooks': 'hooks/verification/observations/fixture/installed-hooks.json',
                'trust_observation': 'hooks/verification/observations/fixture/hook-trust.json',
                'successful_capture': successful, 'meaning': 'Fixture basis'})

    def basis(self, name, **changes):
        path = self.root / adapters.ADAPTERS[name]['basis']
        write(path, dict(read(path), **changes))

    def verify(self, runner=None, **kwargs):
        self.runner = runner or Runner()
        kwargs.setdefault('code', self.root)
        return verify.verify(self.root, run=self.runner, **kwargs)


class Simulated(verify.Disposable):
    """Answers the access check's Codex invocations as a native sandbox would; no binary runs.

    failing names probe checks reported false; during(harness, project) runs inside the sandbox call.
    """

    def __init__(self, store, failing=(), during=None):
        super().__init__(store)
        self.failing, self.during = set(failing), during

    def fingerprint(self, run, project, adapter, checks):
        return {'simulated': True, 'run': run, 'project': str(project), 'capture_adapter': adapter}

    def invoke(self, argv, cwd, **kwargs):
        if argv[1:] == ['--version']:
            return subprocess.CompletedProcess(argv, 0, 'codex-cli 0.0.0-fixture\n', '')
        project = Path(argv[argv.index('-C') + 1])
        packet = self.store.field / argv[argv.index('-p') + 1].removeprefix(f'berman-{self.store.tag}-')
        if 'sandbox' in argv:
            if self.during:
                self.during(self, project)
            spec = read(Path(argv[-1]))
            checks = {name: name not in self.failing for name in COMMON_CHECKS | {c['id'] for c in spec['project_checks']}}
            result = {'schema': 'berman.access-probe/1', 'token': spec['token'], 'checks': checks}
            return subprocess.CompletedProcess(argv, 0 if all(checks.values()) else 1, json.dumps(result) + '\n', '')
        entries = [f'<entry access="deny" escalatable="false"><path>{path}</path></entry>'
                   for path in (self.store.root, self.store.field)]
        entries.append(f'<entry access="write"><path>{packet}</path></entry>')
        entries += [f'<entry access="read"><path>{project / name}</path></entry>' for name in PROTECTED]
        return subprocess.CompletedProcess(argv, 0, json.dumps({'input': ['<permissions instructions>' + ''.join(entries)]}), '')


class VerifyTests(Fixture, Base):
    """Stewart and orchard on berman-codex, so that adapter is required and berman-claude is optional."""

    def setUp(self):
        super().setUp()
        self.code()
        self.trust()

    def test_default_checks_pass_and_unselected_native_access_is_incomplete(self):
        result = self.verify()
        self.assertEqual(list(result['checks']), ORDER)
        self.assertEqual((result['verdict'], result['failed'], result['not_run'], result['not_established']),
                         ('incomplete', [], ['native-access'], []))
        self.assertEqual(verify.EXIT[result['verdict']], 3)
        checks = result['checks']
        self.assertEqual(checks['hook-trust:berman-codex']['details']['used_by'], ['orchard', 'stewart'])
        self.assertEqual(checks['hook-trust:berman-claude']['details']['used_by'], [])
        self.assertEqual(checks['native-capture:berman-codex']['details']['run'], 'run-900101')
        self.assertEqual(checks['native-capture:berman-codex']['details']['events'], ['SessionEnd', 'Stop'])
        self.assertEqual(checks['archive']['details']['builder'], BUILT)
        self.assertEqual(checks['native-access']['next'], verify.NATIVE_NEXT)
        self.assertEqual(verify.NATIVE_NEXT, 'berman verify --native-access')
        self.assertTrue(all(c['meaning'] for c in checks.values()))
        self.assertTrue(all(c['required'] for name, c in checks.items() if not name.endswith(':berman-claude')))
        self.assertFalse(checks['hook-trust:berman-claude']['required'])
        self.assertEqual((result['python'], result['python_version']), (str(self.store.python), 'Python 3.14.0'))
        self.assertEqual(len(result['not_covered']), 4)
        self.assertIsNone(result['record'])
        self.assertFalse(any('unittest' in argv for argv, _ in self.runner.calls))
        self.assertFalse((self.root / '.berman-locks').exists())
        text = plain.verification(result)
        self.assertTrue(text.startswith('Berman verification: INCOMPLETE'))
        self.assertIn('Not run: native-access', text)
        self.assertIn('next: ' + verify.NATIVE_NEXT, text)

    def test_native_access_completes_verification_and_any_other_outcome_fails(self):
        seen = []
        result = self.verify(native=lambda store, folder: seen.append(folder) or passing(store, folder))
        self.assertEqual((result['verdict'], verify.EXIT[result['verdict']]), ('passed', 0))
        self.assertFalse(seen[0].is_relative_to(self.root))
        self.assertFalse(seen[0].exists())
        self.assertEqual(result['checks']['native-access']['details']['retained'], False)
        for report in ({'access_passed': False}, {'access_passed': 'yes'}, None):
            check = self.verify(native=lambda store, folder, report=report: report)['checks']['native-access']
            self.assertEqual(check['status'], 'failed', report)

        def refused(store, folder):
            raise Decision('fixture setup refused')
        result = self.verify(native=refused)
        self.assertEqual((result['verdict'], result['failed']), ('failed', ['native-access']))
        self.assertIn('fixture setup refused', result['checks']['native-access']['summary'])

    def test_trust_without_native_capture_is_distinguished(self):
        self.basis('berman-codex', successful_capture=None)
        result = self.verify(native=passing)
        trust, native = result['checks']['hook-trust:berman-codex'], result['checks']['native-capture:berman-codex']
        self.assertEqual((trust['status'], native['status'], native['required']), ('passed', 'not-established', True))
        self.assertIn('no native capture', native['summary'])
        self.assertEqual(native['next'], verify.TRIAL_NEXT.format(name='berman-codex', root=self.root))
        steps = ['berman support-trial prepare --evidence experiments/NAME --adapter berman-codex;',
                 'berman support-trial launch --evidence experiments/NAME ',
                 'berman support-trial collect --evidence experiments/NAME;',
                 # record-trust reads --evidence from the working directory, so its path is absolute.
                 f'if the basis predates these bytes, berman capture record-trust --evidence '
                 f'{self.root}/experiments/NAME/native.terminal --attest;',
                 'berman capture record-capture --trial experiments/NAME;',
                 'berman support-trial cleanup --evidence experiments/NAME --reason TEXT']
        found = [native['next'].find(step) for step in steps]
        self.assertNotIn(-1, found)
        self.assertEqual(found, sorted(found))
        self.assertEqual((result['verdict'], result['not_established']), ('incomplete', ['native-capture:berman-codex']))

    def test_implementation_change_names_files_and_invalidates_both_facts(self):
        recorded = dict(adapters.implementation('berman-codex'), **{'capture/runtime.py': '0' * 64})
        self.basis('berman-codex', implementation_sha256=recorded)
        result = self.verify(native=passing)
        trust, native = result['checks']['hook-trust:berman-codex'], result['checks']['native-capture:berman-codex']
        self.assertEqual(trust['status'], 'not-established')
        self.assertIn('implementation changed', trust['summary'])
        self.assertEqual(trust['details']['changed_implementation'], ['capture/runtime.py'])
        self.assertIn('berman capture record-trust', trust['next'])
        self.assertEqual((native['status'], native['summary']), ('not-established', 'Depends on hook-trust:berman-codex'))
        self.assertEqual((result['verdict'], result['failed']), ('incomplete', []))
        # The other adapter's basis still names the current bytes.
        self.assertEqual(result['checks']['hook-trust:berman-claude']['status'], 'passed')
        self.assertIn('changed: capture/runtime.py', plain.verification(result))

    def test_unreviewed_adapter_names_no_changed_files(self):
        (self.root / adapters.ADAPTERS['berman-codex']['basis']).unlink()
        self.assertIsNone(adapters.changed_implementation(self.store, 'berman-codex'))
        result = self.verify(native=passing)
        trust = result['checks']['hook-trust:berman-codex']
        self.assertEqual(trust['status'], 'not-established')
        self.assertIn('no recorded CLI trust review', trust['summary'])
        self.assertIsNone(trust['details']['changed_implementation'])
        self.assertNotIn('changed: ', plain.verification(result))

    def test_recorded_capture_must_name_these_bytes_and_keep_its_events(self):
        successful = read(self.root / adapters.ADAPTERS['berman-codex']['basis'])['successful_capture']
        other = dict(successful['implementation_sha256'], **{'_legacy/capture_session.py': '0' * 64})
        unpinned = {k: v for k, v in successful.items() if k != 'implementation_sha256'}
        for capture in (dict(successful, implementation_sha256=other), unpinned):
            self.basis('berman-codex', successful_capture=capture)
            checks = self.verify(native=passing)['checks']
            self.assertEqual(checks['hook-trust:berman-codex']['status'], 'passed')
            self.assertEqual(checks['native-capture:berman-codex']['status'], 'not-established')
        # A recorded capture lacking a terminal event still fails readiness itself, as before.
        self.basis('berman-codex', successful_capture=successful)
        (self.root / successful['path']).write_text('{"event": "Stop"}\n')
        trust = self.verify(native=passing)['checks']['hook-trust:berman-codex']
        self.assertEqual(trust['status'], 'not-established')
        self.assertIn('lacks its required terminal events', trust['summary'])


    def test_frozen_collector_change_fails(self):
        copy = self.root / '_legacy/capture_session.py'
        copy.write_bytes(copy.read_bytes() + b'# changed\n')
        check = self.verify(native=passing)['checks']['frozen-collector']
        self.assertEqual(check['status'], 'failed')
        self.assertIn(digest(copy.read_bytes()), check['summary'])
        self.assertIn(FROZEN, check['summary'])
        self.assertEqual(digest((ROOT / '_legacy/capture_session.py').read_bytes()), FROZEN)
        shutil.copy(ROOT / '_legacy/capture_session.py', copy)
        self.assertEqual(self.verify(native=passing)['checks']['frozen-collector']['status'], 'passed')

    def test_recorded_hashes_cover_experiment_checksums_and_preparation_manifests(self):
        one, two, prep = self.root / 'experiments/one', self.root / 'experiments/two', self.root / 'inquiry/2026-prep'
        for path, text in ((one / 'a.txt', 'a\n'), (one / 'sub/b.txt', 'b\n'), (two / 'c.json', '{}\n'),
                           (prep / 'ASSIGNMENT.md', 'Assignment\n')):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        write(one / 'checksums.json', {'a.txt': digest(b'a\n'), 'sub/b.txt': digest(b'b\n')})
        (two / 'experiment-checksums.sha256').write_text(digest(b'{}\n') + '  c.json\n\n')
        write(prep / 'MANIFEST.json', {'files': [{'path': 'ASSIGNMENT.md', 'bytes': 11, 'sha256': digest(b'Assignment\n')}]})
        # A revision manifest describes files packed in a ZIP elsewhere; it is not checked against this folder.
        write(prep / 'revisions/001/MANIFEST.json', {'files': [{'path': 'packed-only.txt', 'sha256': '0' * 64}]})
        skip = verify.SKIPPABLE
        check = self.verify(skip=skip)['checks']['recorded-hashes']
        self.assertEqual((check['status'], check['summary'], check['details']['hashes']), ('passed', '4 recorded hashes match', 4))
        (one / 'a.txt').write_text('changed\n')
        (two / 'c.json').unlink()
        check = self.verify(skip=skip)['checks']['recorded-hashes']
        self.assertEqual((check['status'], check['details']['mismatched'], check['details']['missing']),
                         ('failed', ['experiments/one/a.txt'], ['experiments/two/c.json']))
        self.assertEqual(check['summary'], '2 of 4 recorded hashes do not hold')
        (one / 'checksums.json').write_text('{broken')
        check = self.verify(skip=skip)['checks']['recorded-hashes']
        self.assertEqual(check['status'], 'failed')
        self.assertIn('cannot be read', check['summary'])

    def test_archive_and_capture_checks_accept_an_external_workspace(self):
        result = self.verify(code=ROOT, native=passing)
        self.assertEqual(result['checks']['archive']['status'], 'passed')
        self.assertTrue(any('--root' in argv and str(self.root) in argv for argv, _ in self.runner.calls))
        self.assertEqual(result['verdict'], 'passed')
        for name in ORDER[3:7]:
            self.assertEqual(result['checks'][name]['status'], 'passed', name)
        stale = 'Traceback (most recent call last):\n  File "build_archive.py"\nValueError: Generated file is missing or stale: review/x\n'
        result = self.verify(Runner(archive=(1, '', stale)), native=passing,
                             record='experiments/stale')
        archive = result['checks']['archive']
        self.assertEqual((archive['status'], archive['summary']),
                         ('failed', 'ValueError: Generated file is missing or stale: review/x'))
        self.assertIn('rebuild once', archive['next'])
        # The builder's whole message is kept; an empty stdout is not.
        self.assertEqual((self.root / 'experiments/stale/archive-verify.stderr.txt').read_text(), stale)
        self.assertEqual(result['record']['files'], ['verification.json', 'archive-verify.stderr.txt', 'native-access/'])
        argv, kwargs = next(call for call in self.runner.calls if 'archive' in call[0])
        self.assertEqual((argv, kwargs['cwd']),([sys.executable, '-I', '-B', '-m', 'berman', '--root', str(self.root), 'archive', '--verify'], self.root))
        self.assertEqual(result['verdict'], 'failed')
        archive = self.verify(Runner(archive=(0, 'not json', '')))['checks']['archive']
        self.assertEqual((archive['status'], archive['summary']), ('failed', 'The archive builder printed no JSON summary'))

    def test_skip_leaves_checks_unrun_without_subprocesses(self):
        result = self.verify(skip=verify.SKIPPABLE, native=passing)
        self.assertEqual([argv[1:] for argv, _ in self.runner.calls], [['--version']])
        self.assertEqual((result['verdict'], result['not_run']), ('incomplete', ['recordings', 'archive']))
        self.assertEqual(result['checks']['archive']['next'], 'berman verify without --skip archive')
        with self.assertRaisesRegex(Decision, 'Only these checks can be skipped'):
            self.verify(skip=['workspace'])

    def test_record_writes_receipts_once_and_refuses_preserved_locations(self):
        result = self.verify(record='experiments/check-1', native=passing)
        folder = self.root / 'experiments/check-1'
        receipts = ['verification.json', 'archive-verify.json']
        self.assertEqual(result['record'], {'directory': str(folder), 'files': receipts + ['native-access/']})
        self.assertEqual(sorted(p.name for p in folder.iterdir()), sorted(receipts + ['native-access']))
        self.assertEqual((folder / 'verification.json').read_bytes(), json_bytes(result))
        self.assertEqual(read(folder / 'archive-verify.json'), BUILT)
        native = result['checks']['native-access']['details']
        self.assertEqual((native['evidence'], native['retained']), (str(folder / 'native-access'), True))
        self.assertTrue((folder / 'native-access/report.json').is_file())
        (self.root / 'experiments/link').symlink_to(self.root / 'runs', target_is_directory=True)
        refused = [folder, 'experiments/check-1', 'runs/x', 'operations/x', 'experiments', 'experiments/x/collection/y',
                   'experiments/x/Collection/y', 'experiments/x/COLLECTION', 'experiments/collection',
                   'experiments/../runs/x', 'experiments/link/x', self.store.field / 'x', self.home / 'x', self.base / 'x']
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
        # The shared policy admits an existing folder only through the resumable marker its caller names.
        trial = self.root / 'experiments/trial'
        trial.mkdir()
        with self.assertRaisesRegex(Decision, 'already exists'):
            verify.evidence_folder(self.root, 'experiments/trial', resume='trial.json')
        (trial / 'trial.json').write_text('{}\n')
        self.assertEqual(verify.evidence_folder(self.root, 'experiments/trial', resume='trial.json'), trial)
        self.assertEqual(verify.evidence_folder(self.root, trial / 'later'), trial / 'later')

    def test_verify_without_record_writes_nothing(self):
        self.workflow.new()
        self.workflow.new(connection='orchard')

        def snapshot():
            return sorted((str(p), p.lstat().st_mtime_ns, p.lstat().st_size) for p in [self.base, *self.base.rglob('*')])
        before = snapshot()
        result = self.verify(native=passing)
        self.assertEqual(snapshot(), before)
        self.assertEqual(result['checks']['workspace']['details']['outstanding_runs'], ['run-001', 'run-002'])
        self.assertEqual(result['checks']['workspace']['status'], 'passed')

    def test_a_problem_run_without_a_connection_fails_the_workspace_check(self):
        # A damaged support marker has no connection, so no connection's blocked_by lists it, yet berman new
        # refuses for every connection.
        marker = self.store.folder('run-900101') / 'operation.json'
        marker.parent.mkdir(parents=True)
        marker.write_text('{broken')
        result = self.verify(native=passing)
        workspace = result['checks']['workspace']
        self.assertEqual((result['verdict'], result['failed'], workspace['status']),
                         ('failed', ['workspace', 'recordings'], 'failed'))
        self.assertEqual(workspace['details']['outstanding_runs'], ['run-900101'])
        self.assertEqual(len(workspace['details']['problems']), 1)
        self.assertTrue(workspace['details']['problems'][0].startswith('every connection: Invalid operation record'))
        for connection in ('stewart', 'orchard'):
            self.assertEqual(self.workflow.status()['connections'][connection]['blocked_by'], [])
            with self.assertRaisesRegex(Decision, 'Invalid operation record'):
                self.workflow.new(connection=connection)
        self.assertIn('every connection: Invalid operation record', plain.verification(result))

    def test_broken_workspace_still_checks_the_installed_collector(self):
        (self.root / 'berman.json').write_text('{broken')
        result = self.verify(native=passing)
        checks = result['checks']
        self.assertEqual(checks['workspace']['status'], 'failed')
        self.assertTrue(all(checks[name]['status'] == 'not-run' for name in ORDER[3:7] + ['native-access']))
        self.assertEqual(sum('unittest' in argv for argv, _ in self.runner.calls), 0)
        self.assertEqual((result['python'], result['verdict']), (sys.executable, 'failed'))
        self.assertEqual(checks['frozen-collector']['status'], 'passed')

    def test_optional_plans_and_selected_plan_errors_have_distinct_verification_results(self):
        config = read(self.root / 'berman.json')
        del config['connections']['orchard']['plan']
        write(self.root / 'berman.json', config)
        self.reload()
        self.assertEqual(self.verify()['checks']['workspace']['status'], 'passed')
        (self.root / 'plans/stewart-fieldwork-roles/plan.json').unlink()
        self.reload()
        check = self.verify()['checks']['workspace']
        self.assertEqual(check['status'], 'failed')
        self.assertEqual(len(check['details']['problems']), 1)
        problem = self.workflow.status()['connections']['stewart']['plan_error']
        self.assertEqual(check['details']['problems'], ['stewart: ' + problem])
        # Reporting a broken selected plan does not block an explicitly selected assignment.
        prepared = self.workflow.new(connection='stewart', recipe_name='work-accounts')
        self.assertEqual(prepared['recipe'], 'work-accounts')


    def test_cli_verify_json_text_and_exit_codes(self):
        def main(*argv):
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = cli.main(['--root', str(self.root), *argv])
            return code, out.getvalue(), err.getvalue()
        with mock.patch.object(verify, 'verify', partial(verify.verify, run=Runner(), code=self.root)):
            code, out, _ = main('--json', 'verify', '--skip', 'archive')
            self.assertEqual((code, json.loads(out)['not_run']), (3, ['archive', 'native-access']))
            code, out, _ = main('verify')
            self.assertEqual(code, 3)
            self.assertIn('Berman verification: INCOMPLETE', out)
            self.assertIn('Not run: native-access', out)
            code, out, err = main('--json', 'verify', '--record', 'runs/x')
            self.assertEqual((code, out, json.loads(err)['state']), (2, '', 'needs-attention'))
            self.assertFalse((self.root / 'runs/x').exists())
            # verify runs before the workspace loads, so a broken berman.json is a failed check, not a refusal.
            (self.root / 'berman.json').write_text('{broken')
            code, out, _ = main('--json', 'verify')
            self.assertEqual((code, json.loads(out)['checks']['workspace']['status']), (1, 'failed'))
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cli.main(['verify', '--skip', 'workspace'])


class NativeAccessTests(Fixture, Base):
    """Stewart, orchard and the synthetic fixture prepared in disposable roots, with no hook trust recorded."""

    def setUp(self):
        super().setUp()
        config = read(self.root / 'berman.json')
        config['python'] = sys.executable
        write(self.root / 'berman.json', config)
        self.reload()
        (self.home / 'config.toml').write_text('model = "fixture"\n')
        write(self.home / 'hooks.json', {'hooks': {}})
        (self.home / 'auth.json').write_text('{"token": "never copied"}\n')
        (self.home / 'sessions').mkdir()
        (self.home / 'sessions/old.jsonl').write_text('{}\n')
        which = mock.patch('shutil.which', return_value='/fixture/bin/tool')
        which.start()
        self.addCleanup(which.stop)
        self.seen = []

    def observe(self, harness, project):
        root, home = harness.store.root, harness.store.home
        self.seen.append({'root': root, 'project': project, 'home': sorted(p.name for p in home.iterdir()),
                          'config': (home / 'config.toml').read_text(), 'hooks': (root / 'hooks').exists(),
                          'runs': (root / 'runs').exists(), 'layout': read(root / 'source-layout.json'),
                          'catalogue': read(root / 'connections/catalogue.json')['runs'],
                          'plans': (root / 'plans').exists(),
                          'recipes': sorted(p.name for p in (root / 'recipes').iterdir()),
                          'definitions': {name: conn.definition for name, conn in harness.store.connections.items()},
                          'selected_plans': [conn.entry.get('plan') for conn in harness.store.connections.values()]})

    def check(self, folder='experiments/native-1', **kwargs):
        kwargs.setdefault('harness', partial(Simulated, during=self.observe))
        return verify.native_access(self.store, self.root / folder, **kwargs)

    def tree(self, excluded):
        """Every path beneath the disposable base outside the evidence folder; files with size and time."""
        return sorted((str(p), None if p.is_dir() else (p.lstat().st_size, p.lstat().st_mtime_ns))
                      for p in self.base.rglob('*') if not p.is_relative_to(excluded))

    def main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(['--root', str(self.root), *argv])
        return code, out.getvalue(), err.getvalue()

    def test_disposable_workspace_keeps_the_two_interpreters(self):
        config = read(self.root / 'berman.json')
        config['runtime_python'] = '/package-environment/bin/python'
        write(self.root / 'berman.json', config)
        self.reload()
        base = self.base / 'disposable'
        base.mkdir()
        fixture = Store(verify.disposable_workspace(self.store, base, ['stewart'], False))
        self.assertEqual(fixture.python, self.store.python)
        self.assertEqual(fixture.runtime_python, self.store.runtime_python)
        self.assertEqual(fixture.connections['stewart'].definition, self.store.connections['stewart'].definition)
        self.assertEqual(fixture.connections['stewart'].checkout, self.store.connections['stewart'].checkout)

    def test_access_passes_for_each_connection_and_the_synthetic_fixture(self):
        with self.assertRaises((OSError, ValueError)):
            adapters.readiness(self.store, 'berman-codex')  # No hook trust is recorded in this workspace.
        folder = self.root / 'experiments/native-1'
        folder.parent.mkdir()
        before = self.tree(folder)
        report = self.check()
        self.assertEqual(self.tree(folder), before)
        self.assertFalse((self.root / 'operations').exists())
        self.assertEqual((report['schema'], report['access_passed'], report['disposable_roots_removed']),
                         ('berman.native-access-check/1', True, True))
        connections = report['connections']
        self.assertEqual([(name, c['source'], c['capture_adapter'], c['run'], c['recipe']) for name, c in connections.items()],
                         [('stewart', 'real', 'berman-codex', 'run-001', verify.DIAGNOSTIC_RECIPE),
                          ('orchard', 'real', 'berman-codex', 'run-002', verify.DIAGNOSTIC_RECIPE),
                          ('native-check-fixture', 'synthetic', 'berman-codex', 'run-003', verify.DIAGNOSTIC_RECIPE)])
        self.assertEqual((connections['stewart']['checkout'], connections['stewart']['project_checks']),
                         (str(self.base / 'stewart'), ['stewart_cargo_metadata', 'stewart_rustc_startup']))
        for name, entry in connections.items():
            self.assertEqual((entry['access_passed'], entry['checkout_unchanged'], entry['evidence']), (True, True, name + '/'))
            self.assertNotIn('error', entry)
            self.assertEqual(sorted(p.name for p in (folder / name).iterdir()), sorted(verify.ACCESS_EVIDENCE))
            receipt = read(folder / name / 'readiness.json')
            self.assertEqual((receipt['passed'], receipt['hooks']['established'], receipt['hooks']['meaning']),
                             (True, False, verify.ACCESS_ONLY))
            (result,) = (folder / name / 'checks').glob('*/result.json')
            self.assertTrue(read(result)['passed'])
            spec = read(result.parent / 'spec.json')
            if name in self.store.connections:
                conn = self.store.connections[name]
                self.assertEqual(spec['repo'], str(conn.checkout))
                self.assertEqual(spec['reads'], [d for d in conn.documents() if (conn.checkout / d).is_file()])
                self.assertEqual([c['id'] for c in spec['project_checks']], [c['id'] for c in conn.checks()])
            instructions = (folder / name / 'INSTRUCTIONS.md').read_text()
            self.assertIn('Native access diagnostic', instructions)
            self.assertIn('no fieldworker is launched', instructions)
        self.assertTrue((folder / 'native-check-fixture/ASSIGNMENT.md').read_text().startswith('Berman-Fieldwork-Run: run-003\n'))
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ['native-check-fixture', 'orchard', 'report.json', 'stewart'])
        self.assertEqual(read(folder / 'report.json'), report)
        self.assertEqual((report['codex_version'], report['codex'], report['python']),
                         ('codex-cli 0.0.0-fixture', str(self.store.codex), str(self.store.python)))
        self.assertEqual(report['harness_sha256'], {rel: digest((ROOT / rel).read_bytes())
                                                    for rel in ('harnesses/codex.py', 'harnesses/probe.py')})
        self.assertEqual(report['not_established'], verify.ACCESS_NOT_ESTABLISHED)
        self.assertEqual(report['diagnostic_recipe'], verify.DIAGNOSTIC_RECIPE)
        self.assertNotIn('fulfilled_commitments', report)
        self.assertIn('diagnostic instructions', report['scope'])
        # The disposable workspace held researcher inputs and Codex configuration only, and is gone.
        self.assertEqual([s['project'] for s in self.seen][:2], [self.base / 'stewart', self.base / 'orchard'])
        self.assertEqual(self.seen[2]['project'], self.seen[2]['root'].parent / 'native-check-fixture')
        for seen in self.seen:
            self.assertFalse(seen['root'].is_relative_to(self.base) or seen['root'].exists())
            self.assertEqual(seen['config'], 'model = "fixture"\n')
            self.assertIn('hooks.json', seen['home'])
            self.assertFalse({'auth.json', 'sessions'} & set(seen['home']))
            self.assertFalse(seen['hooks'] or seen['runs'])
            self.assertEqual((seen['layout'], seen['catalogue'], seen['plans']), ({'runs': []}, [], False))
            self.assertEqual(seen['recipes'], [verify.DIAGNOSTIC_RECIPE])
            self.assertTrue(all(plan is None for plan in seen['selected_plans']))
            for name in ('stewart', 'orchard'):
                self.assertEqual(seen['definitions'][name], self.store.connections[name].definition)

    def test_failing_sandbox_is_reported_with_its_evidence(self):
        folder = self.root / 'experiments/native-2'
        report = self.check('experiments/native-2', connections=['orchard'], synthetic=False,
                            harness=partial(Simulated, failing={'python_read_denied_berman'}))
        self.assertEqual(list(report['connections']), ['orchard'])
        entry = report['connections']['orchard']
        self.assertEqual((entry['access_passed'], entry['checkout_unchanged'], entry['run'], entry['recipe'], entry['evidence']),
                         (False, True, 'run-001', verify.DIAGNOSTIC_RECIPE, 'orchard/'))
        # The error names the kept result, not its copy in the removed disposable roots.
        (result,) = (folder / 'orchard/checks').glob('*/result.json')
        self.assertEqual(entry['error'], f'Decision: Native access check did not pass. Details: {result}')
        receipt = read(folder / 'orchard/readiness.json')
        self.assertEqual((receipt['passed'], receipt['checks']['python_read_denied_berman']), (False, False))
        self.assertEqual((report['access_passed'], read(folder / 'report.json')), (False, report))
        self.assertTrue(report['disposable_roots_removed'])
        with self.assertRaisesRegex(Decision, 'Unknown connection nowhere'):
            self.check('experiments/native-3', connections=['nowhere'])
        with self.assertRaisesRegex(Decision, 'empty folder'):
            self.check('experiments/native-2')
        self.assertFalse((self.root / 'experiments/native-3').exists())

    def test_diagnostic_checks_plan_free_and_broken_plan_connections_without_study_recipes(self):
        plan = read(self.root / 'plans/orchard-study/plan.json')
        plan['commitment'] = 'An orchard commitment'
        plan['relationships'] = []
        write(self.root / 'plans/orchard-study/plan.json', plan)
        planned = self.check('experiments/native-planned')
        self.assertTrue(planned['access_passed'])
        config = read(self.root / 'berman.json')
        del config['connections']['stewart']['plan']
        write(self.root / 'berman.json', config)
        self.reload()
        shutil.rmtree(self.root / 'recipes')
        report = self.check('experiments/native-plan-free')
        self.assertTrue(report['access_passed'])
        self.assertEqual(list(report['connections']), ['stewart', 'orchard', verify.FIXTURE])
        self.assertTrue(all(c['recipe'] == verify.DIAGNOSTIC_RECIPE for c in report['connections'].values()))
        for name in ('stewart', 'orchard'):
            self.assertEqual(report['connections'][name]['project_checks'], planned['connections'][name]['project_checks'])
            before = read(self.root / 'experiments/native-planned' / name / 'readiness.json')['checks']
            after = read(self.root / 'experiments/native-plan-free' / name / 'readiness.json')['checks']
            self.assertEqual(after, before)
        self.assertEqual(plan, read(self.root / 'plans/orchard-study/plan.json'))
        (self.root / 'plans/orchard-study/plan.json').write_text('{broken')
        self.reload()
        report = self.check('experiments/native-broken-plan', connections=['orchard'], synthetic=False)
        self.assertTrue(report['access_passed'])
        self.assertEqual(list(report['connections']), ['orchard'])
        self.assertEqual((self.root / 'plans/orchard-study/plan.json').read_text(), '{broken')
        self.assertFalse((self.root / 'operations').exists())

    def test_diagnostic_namespace_collisions_preserve_researcher_inputs(self):
        for number, relative in enumerate(('recipes/' + verify.DIAGNOSTIC_RECIPE,
                                           'connections/' + verify.FIXTURE)):
            path = self.root / relative
            path.mkdir()
            source = path / 'researcher.txt'
            source.write_text('Retained researcher input.\n')
            with self.assertRaisesRegex(Decision, 'namespace collides with researcher inputs'):
                self.check(f'experiments/native-collision-{number}')
            self.assertEqual(source.read_text(), 'Retained researcher input.\n')
            shutil.rmtree(path)
        path = self.root / 'recipes' / verify.DIAGNOSTIC_RECIPE
        path.symlink_to(self.base / 'absent-researcher-recipe')
        with self.assertRaisesRegex(Decision, 'namespace collides with researcher inputs'):
            self.check('experiments/native-symlink-collision')
        self.assertTrue(path.is_symlink())

    def test_claude_connections_still_use_the_separate_support_trial(self):
        config = read(self.root / 'berman.json')
        config['connections']['orchard']['capture'] = 'berman-claude'
        del config['connections']['orchard']['plan']
        write(self.root / 'berman.json', config)
        self.reload()
        report = self.check('experiments/native-codex-only')
        self.assertEqual(list(report['connections']), ['stewart', verify.FIXTURE])
        self.assertIn('orchard: Claude access is qualified by its separate bounded support trial, not this Codex probe',
                      report['not_established'])
        with self.assertRaisesRegex(Decision, 'Claude native access uses an explicit bounded'):
            self.check('experiments/native-explicit-claude', connections=['orchard'])

    def test_checkout_changed_during_the_check_fails_access(self):
        def stray(harness, project):
            if project == self.base / 'stewart':
                (project / 'stray.txt').write_text('written during the check\n')
        report = self.check(harness=partial(Simulated, during=stray))
        stewart, orchard = report['connections']['stewart'], report['connections']['orchard']
        self.assertEqual((stewart['access_passed'], stewart['checkout_unchanged']), (True, False))
        self.assertEqual((orchard['access_passed'], orchard['checkout_unchanged']), (True, True))
        self.assertFalse(report['access_passed'])

    def test_verify_keeps_native_evidence_only_with_record(self):
        self.code()
        native = partial(verify.native_access, harness=Simulated)
        result = self.verify(native=native, record='experiments/verify-native', skip=verify.SKIPPABLE)
        check = result['checks']['native-access']
        folder = self.root / 'experiments/verify-native/native-access'
        self.assertEqual((check['status'], check['details']['evidence'], check['details']['retained']),
                         ('passed', str(folder), True))
        self.assertEqual(read(folder / 'report.json'), check['details']['report'])
        self.assertTrue((folder / 'stewart/readiness.json').is_file())
        self.assertIn('native-access/', result['record']['files'])
        # Hook trust is not recorded here; native access is still its own passing fact.
        self.assertEqual(result['checks']['hook-trust:berman-codex']['status'], 'not-established')
        check = self.verify(native=native, skip=verify.SKIPPABLE)['checks']['native-access']
        self.assertEqual((check['status'], check['details']['evidence'], check['details']['retained']), ('passed', None, False))
        self.assertIn('evidence not retained', check['summary'])
        failing = partial(verify.native_access, harness=partial(Simulated, failing={'git_read'}))
        result = self.verify(native=failing, skip=verify.SKIPPABLE)
        check = result['checks']['native-access']
        self.assertEqual((check['status'], len(check['details']['problems'])), ('failed', 3))
        self.assertTrue(check['details']['problems'][0].startswith('stewart: Decision: Native access check did not pass'))
        self.assertEqual(check['next'], 'Keep the evidence with berman verify --native-access --record experiments/NAME')
        self.assertIn('stewart: Decision: Native access check did not pass', plain.verification(result))
        problems = self.verify(native=failing, record='experiments/verify-failing',
                               skip=verify.SKIPPABLE)['checks']['native-access']['details']['problems']
        self.assertTrue(all(Path(p.rpartition('Details: ')[2]).is_file() for p in problems))

    def test_cli_native_access_flag_runs_the_maintained_check(self):
        self.code()
        self.trust()
        with mock.patch.object(verify, 'verify', partial(verify.verify, run=Runner(), code=self.root)), \
             mock.patch.object(verify, 'native_access', partial(verify.native_access, harness=Simulated)):
            code, out, _ = self.main('--json', 'verify', '--native-access', '--record', 'experiments/cli-native')
            result = json.loads(out)
            native = result['checks']['native-access']
            self.assertEqual((code, result['verdict'], native['status']), (0, 'passed', 'passed'))
            self.assertEqual(native['details']['evidence'], str(self.root / 'experiments/cli-native/native-access'))
            self.assertEqual(sorted(native['details']['report']['connections']), ['native-check-fixture', 'orchard', 'stewart'])
            code, out, _ = self.main('--json', 'verify', '--native-access')
            native = json.loads(out)['checks']['native-access']
            self.assertEqual((code, native['status'], native['details']['retained']), (0, 'passed', False))
            code, out, _ = self.main('verify')
            self.assertEqual(code, 3)
            self.assertIn('next: berman verify --native-access', out)


class UnusedAdapterTests(Fixture, unittest.TestCase):
    """Stewart alone on berman-codex: berman-claude serves no configured connection, so its gaps are optional."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name).resolve()
        self.root, self.home = self.base / 'berman', self.base / 'codex'
        self.home.mkdir()
        (self.base / 'stewart').mkdir()
        workspace(self.root, self.base, self.base / 'stewart')
        self.store = Store(self.root)
        self.code()
        self.trust()
        self.basis('berman-claude', successful_capture=None)

    def test_capture_gap_for_unused_adapter_is_optional(self):
        result = self.verify(native=passing)
        trust = result['checks']['hook-trust:berman-claude']
        self.assertEqual((trust['status'], trust['required'], trust['details']['used_by']), ('not-established', False, []))
        self.assertEqual(result['checks']['native-capture:berman-claude']['required'], False)
        self.assertEqual(result['checks']['hook-trust:berman-codex']['required'], True)
        self.assertEqual((result['verdict'], result['not_established']),
                         ('passed', ['hook-trust:berman-claude', 'native-capture:berman-claude']))
        text = plain.verification(result)
        self.assertIn('not established (optional)', text)
        self.assertIn('Not established: hook-trust:berman-claude (optional), native-capture:berman-claude (optional)', text)
        self.assertEqual(self.verify()['verdict'], 'incomplete')


class MaintainedAdapterTests(Fixture, unittest.TestCase):
    """One adapter per harness, and the settings an adopted installation uses."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name).resolve()
        self.root, self.home = self.base / 'berman', self.base / 'codex'
        self.home.mkdir()
        (self.base / 'stewart').mkdir()
        workspace(self.root, self.base, self.base / 'stewart')
        self.store = Store(self.root)
        self.code()
        self.trust(captures=('berman-codex',))

    def configure(self, change):
        config = read(self.root / 'berman.json')
        change(config)
        write(self.root / 'berman.json', config)
        self.store = Store(self.root)

    def test_only_the_two_adapters_are_reported_and_renewed(self):
        self.assertEqual(list(adapters.ADAPTERS), ['berman-codex', 'berman-claude'])
        checks = self.verify(native=passing)['checks']
        self.assertEqual([name for name in checks if ':' in name],
                         ['hook-trust:berman-codex', 'native-capture:berman-codex',
                          'hook-trust:berman-claude', 'native-capture:berman-claude'])
        self.assertEqual([checks[n]['required'] for n in ('hook-trust:berman-codex', 'hook-trust:berman-claude')],
                         [True, False])
        evidence = self.base / 'hooks.terminal'
        evidence.write_text('Fixture /hooks review record\n')
        written = adapters.record_trust(self.store, evidence, 'fixture statement')['bases']
        self.assertEqual(written, [str(self.root / 'hooks/verification/berman-codex.json')])
        # The removed adapter's basis, where an older installation left one, is never written.
        self.assertFalse((self.root / 'hooks/verification/routed-legacy-codex.json').exists())

    def test_declared_adapters_are_required_while_no_connection_selects_them(self):
        self.configure(lambda c: c.update(qualified_adapters=['berman-claude']))
        self.basis('berman-claude', successful_capture=None)
        result = self.verify(native=passing)
        trust = result['checks']['hook-trust:berman-claude']
        self.assertEqual((trust['status'], trust['required'], trust['details']['kept_qualified']),
                         ('not-established', True, True))
        self.assertEqual(result['verdict'], 'incomplete')
        with self.assertRaisesRegex(ValueError, 'qualified_adapters'):
            self.configure(lambda c: c.update(qualified_adapters=['no-such-adapter']))

    def test_hooks_name_the_runtime_interpreter_and_probes_keep_the_general_one(self):
        runtime = self.base / 'environment/bin/python'
        runtime.parent.mkdir(parents=True)
        runtime.symlink_to(sys.executable)
        self.configure(lambda c: c.update(runtime_python=str(runtime)))
        self.assertEqual((self.store.python, self.store.runtime_python), (Path(sys.executable), runtime))
        generated = adapters.definition(self.store, 'berman-codex')
        commands = {h['command'] for groups in generated['hooks'].values() for g in groups for h in g['hooks']}
        self.assertEqual(len(commands), 1)
        self.assertTrue(commands.pop().startswith(str(runtime) + ' -I -B -m berman.capture.router hook '))
        identity = self.store.harness_identity('berman-codex', 'run-101')
        self.assertEqual((identity['python'], identity['runtime_python']), (sys.executable, str(runtime)))
        record = {'run': 'run-101', 'harness': identity}
        self.store.check_installation(record)
        self.store.check_installation({'run': 'run-101', 'harness': {k: v for k, v in identity.items()
                                                                     if k != 'runtime_python'}})
        self.configure(lambda c: c.pop('runtime_python'))
        self.assertNotIn('runtime_python', self.store.harness_identity('berman-codex', 'run-101'))
        with self.assertRaisesRegex(Decision, 'runtime_python'):
            self.store.check_installation(record)

    def test_the_workspace_template_is_generated_without_installing(self):
        template = self.root / 'hooks/berman-capture.hooks.json'
        installed = (self.home / 'hooks.json').read_bytes()
        template.unlink()
        self.assertFalse(adapters.template(self.store, 'berman-codex')['matches'])
        self.assertFalse(template.exists())
        result = adapters.template(self.store, 'berman-codex', True)
        self.assertEqual((result['matches'], result['written']), (True, True))
        self.assertEqual(read(template), adapters.definition(self.store, 'berman-codex'))
        self.assertEqual((self.home / 'hooks.json').read_bytes(), installed)

    def test_the_workspace_names_the_edition_it_maintains(self):
        from berman import workspace as selection
        self.assertEqual((self.store.edition, selection.edition(self.root)), ('connections', 'connections'))
        self.configure(lambda c: c.update(edition='stewart'))
        self.assertEqual((self.store.edition, selection.edition(self.root)), ('stewart', 'stewart'))
        with self.assertRaisesRegex(ValueError, 'reading edition'):
            self.configure(lambda c: c.update(edition='another'))
        self.assertEqual(selection.edition(self.base / 'missing'), 'connections')


if __name__ == '__main__':
    unittest.main()
