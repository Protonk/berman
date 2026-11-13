"""berman verify: separate facts, verdicts and receipts, using fake runners and disposable roots only.

No test here starts Codex or a test suite, or reads the real Codex home or runs/; Git runs only in
disposable repositories, and native access runs against a simulated Codex. The recursion guard
makes a test that forgot its fake runner fail instead of rerunning the suite that contains it.
"""
from contextlib import redirect_stderr, redirect_stdout
from functools import partial
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from berman_files import Decision, digest, digest_json, json_bytes, read, write
from berman_harness import COMMON_CHECKS, PROTECTED
from berman_records import ROOT, Store
import berman_capture
import berman_cli
import berman_verify
from test_berman import workspace, legacy_hooks, legacy_basis
from test_connections import Base

SEPARATOR = '-' * 70
OK = f'..........\n{SEPARATOR}\nRan 130 tests in 14.000s\n\nOK\n'
BUILT = {'mode': 'verified', 'archive_path': 'archive/stewart-runs.zip', 'zip_members': 12, 'archive_sha256': '0' * 64}
FROZEN = 'f87241a6b9506590fffa4e2a964f37bb7f7c719e508f4024661b45c57f983a99'
ORDER = ['tests', 'launcher-tests', 'frozen-collector', 'recorded-hashes', 'workspace', 'hook-trust:legacy-codex',
         'native-capture:legacy-codex', 'hook-trust:berman-codex', 'native-capture:berman-codex',
         'hook-trust:berman-claude', 'native-capture:berman-claude', 'archive', 'native-access']


def passing(store, folder):
    (folder / 'report.json').write_text('{"access_passed": true}\n')
    return {'access_passed': True}


class Runner:
    """A fake subprocess.run answering each kind of argv from its configured result."""

    def __init__(self, tests=(0, OK), launcher=(0, OK.replace('130', '16')),
                 archive=(0, json.dumps(BUILT, indent=2) + '\n', '')):
        self.tests, self.launcher, self.archive = tests, launcher, archive
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        if argv[1:] == ['--version']:
            return subprocess.CompletedProcess(argv, 0, 'Python 3.14.0\n', '')
        if 'unittest' in argv:
            code, stderr = self.tests if argv[argv.index('-s') + 1] == 'tools' else self.launcher
            return subprocess.CompletedProcess(argv, code, '', stderr)
        code, stdout, stderr = self.archive
        return subprocess.CompletedProcess(argv, code, stdout, stderr)


class Fixture:
    """The fixture root doubles as the code root, with a recorded hook review written directly."""

    def code(self):
        for rel in ('tools/capture_session.py', 'hooks/verification/legacy-codex.json', 'hooks/fieldwork.hooks.json'):
            (self.root / rel).parent.mkdir(parents=True, exist_ok=True)
            if rel.startswith('tools/'):
                shutil.copy(ROOT / rel, self.root / rel)
            else:
                write(self.root / rel, legacy_basis() if 'verification/' in rel else legacy_hooks())

    def trust(self, captures=('legacy-codex', 'berman-codex')):
        """Installed router hooks, a review observation and both bases; no Codex binary runs."""
        template = berman_capture.definition(self.store, 'berman-codex')
        write(self.root / 'hooks/berman-capture.hooks.json', template)
        write(self.home / 'hooks.json', template)
        folder = self.root / 'hooks/verification/observations/fixture'
        write(folder / 'installed-hooks.json', template)
        (folder / 'hooks.terminal').write_text('Fixture /hooks review record\n')
        observed = {event: {'enabled': True, 'trust': 'Trusted', 'source': '~/.codex/hooks.json'}
                    for event in berman_capture.EVENTS}
        write(folder / 'hook-trust.json', {'schema': 'berman.hook-trust-observation/1', 'evidence': 'hooks.terminal',
                                           'evidence_sha256': digest(b'Fixture /hooks review record\n'),
                                           'observed': observed})
        for name, adapter in berman_capture.ADAPTERS.items():
            current = berman_capture.implementation(name)
            successful = None
            if name in captures or name == 'berman-claude':
                path = self.root / f'fixture-captures/{name}.jsonl'
                path.parent.mkdir(exist_ok=True)
                path.write_text(''.join(json.dumps({'event': e}) + '\n' for e in ('UserPromptSubmit', 'Stop', 'SessionEnd')))
                successful = {'path': f'fixture-captures/{name}.jsonl', 'required_events': ['Stop', 'SessionEnd'],
                              'run': 'run-900101', 'session_id': 'fixture-session',
                              'trial_receipt': 'experiments/fixture-trial/trial.json', 'implementation_sha256': current}
            if name == 'berman-claude':
                import berman_claude
                write(self.root / adapter['basis'], {
                    'schema': berman_claude.SCHEMA, 'adapter': name, 'implementation_sha256': current,
                    'helpers_sha256': berman_claude.helpers(), 'evidence_sha256': {}, 'hook_review': None,
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
        path = self.root / berman_capture.ADAPTERS[name]['basis']
        write(path, dict(read(path), **changes))

    def verify(self, runner=None, **kwargs):
        self.runner = runner or Runner()
        kwargs.setdefault('code', self.root)
        return berman_verify.verify(self.root, run=self.runner, **kwargs)


class Simulated(berman_verify.Disposable):
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
    """Stewart on legacy-codex and orchard on berman-codex, so both adapters are required."""

    def setUp(self):
        super().setUp()
        self.code()
        self.trust()

    def test_default_checks_pass_and_unselected_native_access_is_incomplete(self):
        result = self.verify()
        self.assertEqual(list(result['checks']), ORDER)
        self.assertEqual((result['verdict'], result['failed'], result['not_run'], result['not_established']),
                         ('incomplete', [], ['native-access'], []))
        self.assertEqual(berman_verify.EXIT[result['verdict']], 3)
        checks = result['checks']
        self.assertEqual((checks['tests']['summary'], checks['tests']['details']['ran']), ('130 tests passed in 14.0s', 130))
        self.assertEqual(checks['launcher-tests']['details']['ran'], 16)
        self.assertEqual(checks['hook-trust:legacy-codex']['details']['used_by'], ['stewart'])
        self.assertEqual(checks['hook-trust:berman-codex']['details']['used_by'], ['orchard'])
        self.assertEqual(checks['native-capture:legacy-codex']['details']['run'], 'run-900101')
        self.assertEqual(checks['native-capture:legacy-codex']['details']['events'], ['SessionEnd', 'Stop'])
        self.assertEqual(checks['archive']['details']['builder'], BUILT)
        self.assertEqual(checks['native-access']['next'], berman_verify.NATIVE_NEXT)
        self.assertEqual(berman_verify.NATIVE_NEXT, 'berman verify --native-access')
        self.assertTrue(all(c['meaning'] for c in checks.values()))
        self.assertTrue(all(c['required'] for name, c in checks.items() if not name.endswith(':berman-claude')))
        self.assertFalse(checks['hook-trust:berman-claude']['required'])
        self.assertEqual((result['python'], result['python_version']), (str(self.store.python), 'Python 3.14.0'))
        self.assertEqual(len(result['not_covered']), 3)
        self.assertIsNone(result['record'])
        suites = [(argv, kwargs) for argv, kwargs in self.runner.calls if 'unittest' in argv]
        self.assertEqual([argv[argv.index('-s') + 1] for argv, _ in suites], ['tools', 'tools/development_launchers'])
        for argv, kwargs in suites:
            self.assertEqual(argv[:3], [str(self.store.python), '-I', '-B'])
            self.assertEqual((kwargs['cwd'], kwargs['env']['BERMAN_VERIFYING']), (self.root, '1'))
        self.assertFalse((self.root / '.berman-locks').exists())
        text = berman_verify.render(result)
        self.assertTrue(text.startswith('Berman verification: INCOMPLETE'))
        self.assertIn('Not run: native-access', text)
        self.assertIn('next: ' + berman_verify.NATIVE_NEXT, text)

    def test_native_access_completes_verification_and_any_other_outcome_fails(self):
        seen = []
        result = self.verify(native=lambda store, folder: seen.append(folder) or passing(store, folder))
        self.assertEqual((result['verdict'], berman_verify.EXIT[result['verdict']]), ('passed', 0))
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
        self.assertEqual(native['next'], berman_verify.TRIAL_NEXT.format(name='berman-codex', root=self.root))
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
        recorded = dict(berman_capture.implementation('legacy-codex'), **{'tools/berman_capture_runtime.py': '0' * 64})
        self.basis('legacy-codex', implementation_sha256=recorded)
        result = self.verify(native=passing)
        trust, native = result['checks']['hook-trust:legacy-codex'], result['checks']['native-capture:legacy-codex']
        self.assertEqual(trust['status'], 'not-established')
        self.assertIn('implementation changed', trust['summary'])
        self.assertEqual(trust['details']['changed_implementation'], ['tools/berman_capture_runtime.py'])
        self.assertIn('berman capture record-trust', trust['next'])
        self.assertEqual((native['status'], native['summary']), ('not-established', 'Depends on hook-trust:legacy-codex'))
        self.assertEqual((result['verdict'], result['failed']), ('incomplete', []))
        self.assertEqual(result['checks']['hook-trust:berman-codex']['status'], 'passed')
        self.assertIn('changed: tools/berman_capture_runtime.py', berman_verify.render(result))

    def test_unreviewed_adapter_names_no_changed_files(self):
        (self.root / berman_capture.ADAPTERS['berman-codex']['basis']).unlink()
        self.assertIsNone(berman_capture.changed_implementation(self.store, 'berman-codex'))
        result = self.verify(native=passing)
        trust = result['checks']['hook-trust:berman-codex']
        self.assertEqual(trust['status'], 'not-established')
        self.assertIn('no recorded CLI trust review', trust['summary'])
        self.assertIsNone(trust['details']['changed_implementation'])
        self.assertNotIn('changed: ', berman_verify.render(result))

    def test_recorded_capture_must_name_these_bytes_and_keep_its_events(self):
        successful = read(self.root / berman_capture.ADAPTERS['legacy-codex']['basis'])['successful_capture']
        other = dict(successful['implementation_sha256'], **{'tools/capture_session.py': '0' * 64})
        unpinned = {k: v for k, v in successful.items() if k != 'implementation_sha256'}
        for capture in (dict(successful, implementation_sha256=other), unpinned):
            self.basis('legacy-codex', successful_capture=capture)
            checks = self.verify(native=passing)['checks']
            self.assertEqual(checks['hook-trust:legacy-codex']['status'], 'passed')
            self.assertEqual(checks['native-capture:legacy-codex']['status'], 'not-established')
        # A recorded capture lacking a terminal event still fails readiness itself, as before.
        self.basis('legacy-codex', successful_capture=successful)
        (self.root / successful['path']).write_text('{"event": "Stop"}\n')
        trust = self.verify(native=passing)['checks']['hook-trust:legacy-codex']
        self.assertEqual(trust['status'], 'not-established')
        self.assertIn('lacks its required terminal events', trust['summary'])

    def test_failing_and_empty_suites_fail(self):
        stderr = (f'F.E\n{"=" * 70}\nFAIL: test_a (test_x.Case.test_a)\n{SEPARATOR}\nAssertionError\n\n'
                  f'{"=" * 70}\nERROR: test_b (test_x.Case.test_b)\n{SEPARATOR}\nKeyError\n\n'
                  f'{SEPARATOR}\nRan 3 tests in 0.010s\n\nFAILED (failures=2, errors=1)\n')
        result = self.verify(Runner(tests=(1, stderr)), native=passing)
        tests = result['checks']['tests']
        self.assertEqual((tests['status'], tests['details']['failures'], tests['details']['errors']), ('failed', 2, 1))
        self.assertEqual(tests['details']['failing'], ['FAIL: test_a (test_x.Case.test_a)', 'ERROR: test_b (test_x.Case.test_b)'])
        self.assertEqual((result['verdict'], berman_verify.EXIT[result['verdict']], result['failed']), ('failed', 1, ['tests']))
        self.assertIn('FAIL: test_a', berman_verify.render(result))
        for code, text in ((5, f'\n{SEPARATOR}\nRan 0 tests in 0.000s\n\nNO TESTS RAN\n'), (0, 'NO TESTS RAN\n'),
                           (0, ''), (0, 'Ran 3 tests in 0.100s\n'), (1, OK)):
            check = self.verify(Runner(launcher=(code, text)))['checks']['launcher-tests']
            self.assertEqual(check['status'], 'failed', (code, text))

        def unavailable(argv, **kwargs):
            raise FileNotFoundError(argv[0])
        result = self.verify(unavailable, native=passing)
        self.assertEqual(result['failed'], ['tests', 'launcher-tests', 'archive'])
        self.assertIsNone(result['python_version'])

    def test_unexpected_success_is_named_as_the_failure(self):
        stderr = f'u\n{SEPARATOR}\nRan 1 test in 0.001s\n\nFAILED (unexpected successes=1)\n'
        tests = self.verify(Runner(tests=(1, stderr)), native=passing)['checks']['tests']
        self.assertEqual((tests['status'], tests['details']['unexpected_successes']), ('failed', 1))
        self.assertEqual(tests['summary'], '1 tests ran: 0 failures, 0 errors, 1 unexpected successes')

    def test_suites_run_uncoloured_and_coloured_summaries_still_parse(self):
        with mock.patch.dict(os.environ, {'FORCE_COLOR': '1'}):
            self.verify(skip=('archive',), native=passing)
        for argv, kwargs in self.runner.calls:
            if 'unittest' in argv:
                self.assertEqual((kwargs['env']['NO_COLOR'], 'FORCE_COLOR' in kwargs['env']), ('1', False))
        green, red, reset = '\x1b[32m', '\x1b[31m', '\x1b[0m'
        coloured = OK.replace('\nOK\n', f'\n{green}OK{reset}\n')
        failing = (f'{red}FAIL{reset}: test_a (test_x.Case.test_a)\n{SEPARATOR}\nRan 3 tests in 0.010s\n\n'
                   f'{red}FAILED{reset} (failures={red}1{reset})\n')
        result = self.verify(Runner(tests=(0, coloured), launcher=(1, failing)), record='experiments/colour', native=passing)
        tests, launcher = result['checks']['tests'], result['checks']['launcher-tests']
        self.assertEqual((tests['status'], tests['details']['ran']), ('passed', 130))
        self.assertEqual((launcher['status'], launcher['details']['failures'], launcher['details']['failing']),
                         ('failed', 1, ['FAIL: test_a (test_x.Case.test_a)']))
        # The receipt keeps what the child wrote.
        self.assertEqual((self.root / 'experiments/colour/tests.txt').read_text(), coloured)

    def test_timed_out_suite_keeps_its_partial_output(self):
        def slow(argv, **kwargs):
            if 'unittest' in argv:
                raise subprocess.TimeoutExpired(argv, kwargs['timeout'], output=b'', stderr=b'..F\nhalfway')
            return Runner()(argv, **kwargs)
        result = self.verify(slow, record='experiments/slow', skip=('archive',), native=passing)
        tests = result['checks']['tests']
        self.assertEqual(tests['status'], 'failed')
        self.assertIn('did not finish', tests['summary'])
        self.assertEqual((self.root / 'experiments/slow/tests.txt').read_text(), '..F\nhalfway')

    def test_frozen_collector_change_fails(self):
        copy = self.root / 'tools/capture_session.py'
        copy.write_bytes(copy.read_bytes() + b'# changed\n')
        check = self.verify(native=passing)['checks']['frozen-collector']
        self.assertEqual(check['status'], 'failed')
        self.assertIn(digest(copy.read_bytes()), check['summary'])
        self.assertIn(FROZEN, check['summary'])
        self.assertEqual(digest((ROOT / 'tools/capture_session.py').read_bytes()), FROZEN)
        shutil.copy(ROOT / 'tools/capture_session.py', copy)
        definition = self.root / 'hooks/fieldwork.hooks.json'
        write(definition, dict(read(definition), description='changed'))
        result = self.verify(native=passing)
        self.assertIn('hooks/fieldwork.hooks.json digest', result['checks']['frozen-collector']['summary'])
        self.assertEqual(result['verdict'], 'failed')

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
        skip = berman_verify.SKIPPABLE
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
        result = self.verify(code=ROOT, skip=('tests', 'launcher-tests'), native=passing)
        self.assertEqual(result['checks']['archive']['status'], 'passed')
        self.assertTrue(any('--root' in argv and str(self.root) in argv for argv, _ in self.runner.calls))
        self.assertEqual(result['verdict'], 'incomplete')  # suites were explicitly skipped
        for name in ORDER[5:9]:
            self.assertEqual(result['checks'][name]['status'], 'passed', name)
        stale = 'Traceback (most recent call last):\n  File "build_archive.py"\nValueError: Generated file is missing or stale: review/x\n'
        result = self.verify(Runner(archive=(1, '', stale)), skip=('tests', 'launcher-tests'), native=passing,
                             record='experiments/stale')
        archive = result['checks']['archive']
        self.assertEqual((archive['status'], archive['summary']),
                         ('failed', 'ValueError: Generated file is missing or stale: review/x'))
        self.assertIn('rebuild once', archive['next'])
        # The builder's whole message is kept; an empty stdout is not.
        self.assertEqual((self.root / 'experiments/stale/archive-verify.stderr.txt').read_text(), stale)
        self.assertEqual(result['record']['files'], ['verification.json', 'archive-verify.stderr.txt', 'native-access/'])
        argv, kwargs = next(call for call in self.runner.calls if 'tools/build_archive.py' in call[0])
        self.assertEqual((argv, kwargs['cwd']),([str(self.store.python), '-I', '-B', 'tools/build_archive.py', '--root', str(self.root), '--verify'], self.root))
        self.assertEqual(result['verdict'], 'failed')
        archive = self.verify(Runner(archive=(0, 'not json', '')), skip=('tests', 'launcher-tests'))['checks']['archive']
        self.assertEqual((archive['status'], archive['summary']), ('failed', 'The archive builder printed no JSON summary'))

    def test_skip_leaves_checks_unrun_without_subprocesses(self):
        result = self.verify(skip=berman_verify.SKIPPABLE, native=passing)
        self.assertEqual([argv[1:] for argv, _ in self.runner.calls], [['--version']])
        self.assertEqual((result['verdict'], result['not_run']), ('incomplete', ['tests', 'launcher-tests', 'archive']))
        self.assertEqual(result['checks']['archive']['next'], 'berman verify without --skip archive')
        with self.assertRaisesRegex(Decision, 'Only these checks can be skipped'):
            self.verify(skip=['workspace'])

    def test_record_writes_receipts_once_and_refuses_preserved_locations(self):
        result = self.verify(record='experiments/check-1', native=passing)
        folder = self.root / 'experiments/check-1'
        receipts = ['verification.json', 'tests.txt', 'development-launcher-tests.txt', 'archive-verify.json']
        self.assertEqual(result['record'], {'directory': str(folder), 'files': receipts + ['native-access/']})
        self.assertEqual(sorted(p.name for p in folder.iterdir()), sorted(receipts + ['native-access']))
        self.assertEqual((folder / 'verification.json').read_bytes(), json_bytes(result))
        self.assertEqual(((folder / 'tests.txt').read_text(), read(folder / 'archive-verify.json')), (OK, BUILT))
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
                berman_verify.verify(self.root, record=path, run=runner, code=self.root)
            self.assertEqual(runner.calls, [], path)
        # A case variant names the preserved collection itself on a case-insensitive volume.
        (self.root / 'experiments/kept/collection').mkdir(parents=True)
        for variant in ('collection', 'Collection', 'cOLLECTION'):
            with self.assertRaisesRegex(Decision, 'preserved collection'):
                berman_verify.evidence_folder(self.root, f'experiments/kept/{variant}/verify-new')
        self.assertEqual(list((self.root / 'experiments/kept/collection').iterdir()), [])
        # The shared policy admits an existing folder only through the resumable marker its caller names.
        trial = self.root / 'experiments/trial'
        trial.mkdir()
        with self.assertRaisesRegex(Decision, 'already exists'):
            berman_verify.evidence_folder(self.root, 'experiments/trial', resume='trial.json')
        (trial / 'trial.json').write_text('{}\n')
        self.assertEqual(berman_verify.evidence_folder(self.root, 'experiments/trial', resume='trial.json'), trial)
        self.assertEqual(berman_verify.evidence_folder(self.root, trial / 'later'), trial / 'later')

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
        self.assertEqual((result['verdict'], result['failed'], workspace['status']), ('failed', ['workspace'], 'failed'))
        self.assertEqual(workspace['details']['outstanding_runs'], ['run-900101'])
        self.assertEqual(len(workspace['details']['problems']), 1)
        self.assertTrue(workspace['details']['problems'][0].startswith('every connection: Invalid operation record'))
        for connection in ('stewart', 'orchard'):
            self.assertEqual(self.workflow.status()['connections'][connection]['blocked_by'], [])
            with self.assertRaisesRegex(Decision, 'Invalid operation record'):
                self.workflow.new(connection=connection)
        self.assertIn('every connection: Invalid operation record', berman_verify.render(result))

    def test_broken_workspace_still_runs_tests(self):
        (self.root / 'berman.json').write_text('{broken')
        result = self.verify(native=passing)
        checks = result['checks']
        self.assertEqual(checks['workspace']['status'], 'failed')
        self.assertTrue(all(checks[name]['status'] == 'not-run' for name in ORDER[5:9] + ['native-access']))
        self.assertEqual(sum('unittest' in argv for argv, _ in self.runner.calls), 2)
        self.assertEqual((result['python'], result['verdict']), (sys.executable, 'failed'))
        self.assertEqual(checks['tests']['status'], 'passed')

    def test_recursion_guard(self):
        with mock.patch.dict(os.environ, {'BERMAN_VERIFYING': '1'}), \
             mock.patch('subprocess.Popen', side_effect=AssertionError('a process was started')):
            with self.assertRaisesRegex(Decision, 'already running'):
                berman_verify.verify(self.root, code=self.root)
            # An injected runner still works, so these tests behave the same inside a verification run.
            self.assertEqual(self.verify(skip=berman_verify.SKIPPABLE)['checks']['workspace']['status'], 'passed')

    def test_cli_verify_json_text_and_exit_codes(self):
        def main(*argv):
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = berman_cli.main(['--root', str(self.root), *argv])
            return code, out.getvalue(), err.getvalue()
        with mock.patch.object(berman_verify, 'verify', partial(berman_verify.verify, run=Runner(), code=self.root)):
            code, out, _ = main('--json', 'verify', '--skip', 'tests')
            self.assertEqual((code, json.loads(out)['not_run']), (3, ['tests', 'native-access']))
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
            berman_cli.main(['verify', '--skip', 'workspace'])


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
        plan = root / 'plans/native-check-fixture/plan.json'
        self.seen.append({'root': root, 'project': project, 'home': sorted(p.name for p in home.iterdir()),
                          'config': (home / 'config.toml').read_text(), 'hooks': (root / 'hooks').exists(),
                          'runs': (root / 'runs').exists(), 'layout': read(root / 'source-layout.json'),
                          'catalogue': read(root / 'connections/catalogue.json')['runs'],
                          'relationships': [r['plan'] for r in read(plan)['relationships']]})

    def check(self, folder='experiments/native-1', **kwargs):
        kwargs.setdefault('harness', partial(Simulated, during=self.observe))
        return berman_verify.native_access(self.store, self.root / folder, **kwargs)

    def tree(self, excluded):
        """Every path beneath the disposable base outside the evidence folder; files with size and time."""
        return sorted((str(p), None if p.is_dir() else (p.lstat().st_size, p.lstat().st_mtime_ns))
                      for p in self.base.rglob('*') if not p.is_relative_to(excluded))

    def main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = berman_cli.main(['--root', str(self.root), *argv])
        return code, out.getvalue(), err.getvalue()

    def test_access_passes_for_each_connection_and_the_synthetic_fixture(self):
        with self.assertRaises((OSError, ValueError)):
            berman_capture.readiness(self.store, 'legacy-codex')  # No hook trust is recorded in this workspace.
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
                         [('stewart', 'real', 'legacy-codex', 'run-001', 'work-accounts'),
                          ('orchard', 'real', 'berman-codex', 'run-002', 'orchard-accounts'),
                          ('native-check-fixture', 'synthetic', 'berman-codex', 'run-003', 'native-check-fixture')])
        self.assertEqual((connections['stewart']['checkout'], connections['stewart']['project_checks']),
                         (str(self.base / 'stewart'), ['stewart_cargo_metadata', 'stewart_rustc_startup']))
        for name, entry in connections.items():
            self.assertEqual((entry['access_passed'], entry['checkout_unchanged'], entry['evidence']), (True, True, name + '/'))
            self.assertNotIn('error', entry)
            self.assertEqual(sorted(p.name for p in (folder / name).iterdir()), sorted(berman_verify.ACCESS_EVIDENCE))
            receipt = read(folder / name / 'readiness.json')
            self.assertEqual((receipt['passed'], receipt['hooks']['established'], receipt['hooks']['meaning']),
                             (True, False, berman_verify.ACCESS_ONLY))
            (result,) = (folder / name / 'checks').glob('*/result.json')
            self.assertTrue(read(result)['passed'])
        self.assertTrue((folder / 'native-check-fixture/ASSIGNMENT.md').read_text().startswith('Berman-Fieldwork-Run: run-003\n'))
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ['native-check-fixture', 'orchard', 'report.json', 'stewart'])
        self.assertEqual(read(folder / 'report.json'), report)
        self.assertEqual((report['codex_version'], report['codex'], report['python']),
                         ('codex-cli 0.0.0-fixture', str(self.store.codex), str(self.store.python)))
        self.assertEqual(report['harness_sha256'], {rel: digest((ROOT / rel).read_bytes())
                                                    for rel in ('tools/berman_harness.py', 'tools/berman_probe.py')})
        self.assertEqual((report['not_established'], report['fulfilled_commitments']), (berman_verify.ACCESS_NOT_ESTABLISHED, []))
        # The disposable workspace held researcher inputs and Codex configuration only, and is gone.
        self.assertEqual([s['project'] for s in self.seen][:2], [self.base / 'stewart', self.base / 'orchard'])
        self.assertEqual(self.seen[2]['project'], self.seen[2]['root'].parent / 'native-check-fixture')
        for seen in self.seen:
            self.assertFalse(seen['root'].is_relative_to(self.base) or seen['root'].exists())
            self.assertEqual(seen['config'], 'model = "fixture"\n')
            self.assertIn('hooks.json', seen['home'])
            self.assertFalse({'auth.json', 'sessions'} & set(seen['home']))
            self.assertFalse(seen['hooks'] or seen['runs'])
            self.assertEqual((seen['layout'], seen['catalogue'], seen['relationships']),
                             ({'runs': []}, [], ['stewart-fieldwork-roles']))

    def test_failing_sandbox_is_reported_with_its_evidence(self):
        folder = self.root / 'experiments/native-2'
        report = self.check('experiments/native-2', connections=['orchard'], synthetic=False,
                            harness=partial(Simulated, failing={'python_read_denied_berman'}))
        self.assertEqual(list(report['connections']), ['orchard'])
        entry = report['connections']['orchard']
        self.assertEqual((entry['access_passed'], entry['checkout_unchanged'], entry['run'], entry['recipe'], entry['evidence']),
                         (False, True, 'run-001', 'orchard-accounts', 'orchard/'))
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

    def test_commitments_are_fulfilled_as_in_the_real_workspace(self):
        plan = read(self.root / 'plans/orchard-study/plan.json')
        plan['commitment'] = 'An orchard commitment'
        write(self.root / 'plans/orchard-study/plan.json', plan)
        self.reload()
        unfulfilled = 'Plan orchard-study for orchard carries an unfulfilled commitment'
        report = self.check('experiments/native-unfulfilled', synthetic=False, harness=Simulated)
        stewart, orchard = report['connections']['stewart'], report['connections']['orchard']
        self.assertEqual((report['fulfilled_commitments'], stewart['access_passed'], orchard['access_passed']), ([], False, True))
        self.assertIn(unfulfilled, stewart['error'])
        with self.assertRaisesRegex(Decision, unfulfilled):
            self.workflow.new(connection='stewart')  # Real preparation asks the same.
        for _ in range(2):
            row = self.workflow.new(connection='orchard')
            self.start(row['run'], self.berman)
            self.workflow.finish('done', connection='orchard')
        # Fulfilled in the real workspace, the commitment asks nothing of Stewart, though no disposable run fulfils it.
        report = self.check('experiments/native-fulfilled')
        self.assertEqual((report['fulfilled_commitments'], report['access_passed']), (['orchard-study'], True))
        self.assertEqual([(c['run'], c['recipe']) for c in report['connections'].values()],
                         [('run-001', 'work-accounts'), ('run-002', 'orchard-accounts'), ('run-003', 'native-check-fixture')])
        self.assertEqual(self.seen[-1]['relationships'], ['stewart-fieldwork-roles'])
        self.assertEqual(plan, read(self.root / 'plans/orchard-study/plan.json'))

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
        native = partial(berman_verify.native_access, harness=Simulated)
        result = self.verify(native=native, record='experiments/verify-native', skip=berman_verify.SKIPPABLE)
        check = result['checks']['native-access']
        folder = self.root / 'experiments/verify-native/native-access'
        self.assertEqual((check['status'], check['details']['evidence'], check['details']['retained']),
                         ('passed', str(folder), True))
        self.assertEqual(read(folder / 'report.json'), check['details']['report'])
        self.assertTrue((folder / 'stewart/readiness.json').is_file())
        self.assertIn('native-access/', result['record']['files'])
        # Hook trust is not recorded here; native access is still its own passing fact.
        self.assertEqual(result['checks']['hook-trust:legacy-codex']['status'], 'not-established')
        check = self.verify(native=native, skip=berman_verify.SKIPPABLE)['checks']['native-access']
        self.assertEqual((check['status'], check['details']['evidence'], check['details']['retained']), ('passed', None, False))
        self.assertIn('evidence not retained', check['summary'])
        failing = partial(berman_verify.native_access, harness=partial(Simulated, failing={'git_read'}))
        result = self.verify(native=failing, skip=berman_verify.SKIPPABLE)
        check = result['checks']['native-access']
        self.assertEqual((check['status'], len(check['details']['problems'])), ('failed', 3))
        self.assertTrue(check['details']['problems'][0].startswith('stewart: Decision: Native access check did not pass'))
        self.assertEqual(check['next'], 'Keep the evidence with berman verify --native-access --record experiments/NAME')
        self.assertIn('stewart: Decision: Native access check did not pass', berman_verify.render(result))
        problems = self.verify(native=failing, record='experiments/verify-failing',
                               skip=berman_verify.SKIPPABLE)['checks']['native-access']['details']['problems']
        self.assertTrue(all(Path(p.rpartition('Details: ')[2]).is_file() for p in problems))

    def test_cli_native_access_flag_runs_the_maintained_check(self):
        self.code()
        self.trust()
        with mock.patch.object(berman_verify, 'verify', partial(berman_verify.verify, run=Runner(), code=self.root)), \
             mock.patch.object(berman_verify, 'native_access', partial(berman_verify.native_access, harness=Simulated)):
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
    """Stewart alone: berman-codex serves no configured connection, so its gaps are optional."""

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
        self.trust(captures=('legacy-codex',))

    def test_capture_gap_for_unused_adapter_is_optional(self):
        result = self.verify(native=passing)
        native = result['checks']['native-capture:berman-codex']
        self.assertEqual((native['status'], native['required'], native['details']['used_by']), ('not-established', False, []))
        self.assertEqual(result['checks']['hook-trust:berman-codex']['required'], False)
        self.assertEqual((result['verdict'], result['not_established']), ('passed', ['native-capture:berman-codex']))
        text = berman_verify.render(result)
        self.assertIn('not established (optional)', text)
        self.assertIn('Not established: native-capture:berman-codex (optional)', text)
        self.assertEqual(self.verify()['verdict'], 'incomplete')


if __name__ == '__main__':
    unittest.main()
