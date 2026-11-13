"""Offline regression tests; native harness checks are separate."""
from tests.support.paths import REPO
import importlib.util
import json
from pathlib import Path
import tomllib
import unittest
from unittest import mock
import contextlib
import io
import tempfile
import subprocess
import sys


from berman.harnesses.development import launch, install, probe


class LaunchTests(unittest.TestCase):
    def setUp(self):
        self.config = {'codex': '/bin/codex', 'claude': '/bin/claude', 'repo': '/tmp/stewart',
                       'codex_profile_name': 'stewart-dev', 'claude_settings_file': '/tmp/claude-settings.json',
                       'denied': ['/tmp/analysis space', '/tmp/fieldwork']}

    def test_both_profiles_have_the_exact_denials(self):
        self.assertEqual(launch.codex_options(self.config), ['-p', 'stewart-dev'])
        profile = tomllib.loads(launch.codex_profile(self.config))['permissions']['stewart_dev']
        self.assertEqual(profile['extends'], ':workspace')
        for path in self.config['denied']:
            self.assertEqual(profile['filesystem'][path], 'deny')
        self.assertEqual(profile['filesystem']['/tmp/stewart/.git'], 'write')
        for name in ('.agents', '.codex', '.aws'):
            self.assertEqual(profile['filesystem']['/tmp/stewart/' + name], 'read')
        settings = launch.claude_settings(self.config)
        self.assertEqual(settings['permissions']['deny'],
                         ['Read(//tmp/analysis space/**)', 'Read(//tmp/fieldwork/**)'])
        self.assertEqual(settings['sandbox']['filesystem']['denyRead'], self.config['denied'])
        self.assertTrue(settings['sandbox']['allowUnsandboxedCommands'])
        self.assertEqual(settings['permissions']['ask'], ['Bash(dangerouslyDisableSandbox:true)'])
        self.assertTrue(settings['sandbox']['failIfUnavailable'])

    def test_no_model_role_or_global_home_override(self):
        for harness in ('codex', 'claude'):
            args = launch.native_command(self.config, harness)
            self.assertNotIn('--model', args)
            self.assertNotIn('--system-prompt', args)
            self.assertNotIn('--safe-mode', args)
            self.assertNotIn('--restricted', args)
            self.assertNotIn('--resume', args)
        args = launch.native_command(self.config, 'codex', 'model-name', 'high')
        self.assertIn('model_reasoning_effort="high"', args)

    def test_assistant_claims_cannot_pass_verification(self):
        stream = json.dumps({'type': 'assistant', 'message': {'content': [
            {'type': 'text', 'text': 'Both reads denied and every check passed.'}]}})
        result = launch.assess_claude(stream, 'probe', ['a', 'b'], 'token')
        self.assertFalse(any(result.values()))

    def test_string_status_messages_are_not_tool_records(self):
        stream = json.dumps({'type': 'system', 'message': 'A status message'})
        self.assertFalse(any(launch.assess_claude(stream, 'probe', ['a'], 'token').values()))

    def test_successful_read_is_not_a_denial(self):
        stream = '\n'.join(map(json.dumps, [
            {'message': {'content': [{'type': 'tool_use', 'id': 'r', 'name': 'Read', 'input': {'file_path': 'a'}}]}},
            {'message': {'content': [{'type': 'tool_result', 'tool_use_id': 'r', 'content': 'content'}]}}
        ]))
        self.assertFalse(launch.assess_claude(stream, 'probe', ['a'], 'token')['file_tool_denied_0'])

    def test_exact_command_token_and_tools_required(self):
        records = [
            {'type': 'assistant', 'message': {'content': [
                {'type': 'tool_use', 'id': 'r', 'name': 'Read', 'input': {'file_path': 'a'}},
                {'type': 'tool_use', 'id': 'b', 'name': 'Bash', 'input': {'command': 'probe'}}]}},
            {'type': 'user', 'message': {'content': [
                {'type': 'tool_result', 'tool_use_id': 'r', 'is_error': True, 'content': 'Permission denied'},
                {'type': 'tool_result', 'tool_use_id': 'b', 'content': json.dumps({
                    'schema': 'stewart.development-probe/1', 'token': 'token',
                    'checks': {key: True for key in launch.EXPECTED_PROBE_CHECKS}})}]}}
        ]
        stream = '\n'.join(map(json.dumps, records))
        self.assertTrue(all(launch.assess_claude(stream, 'probe', ['a'], 'token').values()))
        self.assertFalse(launch.assess_claude(stream, 'different', ['a'], 'token')['observed_exact_shell_probe'])
        self.assertFalse(launch.assess_claude(stream, 'probe', ['a'], 'other')['observed_exact_shell_probe'])

    def test_incomplete_probe_is_rejected(self):
        self.assertFalse(launch.valid_probe({'schema': 'stewart.development-probe/1',
                                           'token': 'token', 'checks': {}}, 'token'))

    def test_git_probe_uses_checkout_boundary_and_leaves_its_metadata_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            repo, fixture = root / 'repo', root / 'fixture'
            fixture.mkdir()
            subprocess.run(['git', 'init', '-q', str(repo)], check=True)
            metadata = repo / '.git'
            def snapshot():
                return {str(p.relative_to(metadata)): p.read_bytes()
                        for p in metadata.rglob('*') if p.is_file()}
            before = snapshot()
            with launch.checkout_git_fixture(repo, fixture) as worktree:
                actual = Path(subprocess.check_output(
                    ['git', '-C', str(worktree), 'rev-parse', '--absolute-git-dir'], text=True).strip())
                self.assertTrue(actual.is_relative_to(metadata))
                self.assertNotEqual(actual, metadata)
                (worktree / 'probe').write_text('probe\n')
                subprocess.run(['git', '-C', str(worktree), 'add', 'probe'], check=True)
                subprocess.run(['git', '-C', str(worktree), '-c', 'user.name=Probe',
                                '-c', 'user.email=probe@invalid', '-c', 'commit.gpgsign=false',
                                '-c', 'core.hooksPath=/dev/null', 'commit', '-qm', 'probe'], check=True)
            self.assertEqual(snapshot(), before)

    def test_interpreter_survives_a_symlink_target_upgrade(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home, repo, analysis, fieldwork = [root / n for n in ('home', 'repo', 'analysis', 'fieldwork')]
            for p in (home, repo / 'src/stewart', analysis, fieldwork):
                p.mkdir(parents=True)
            (repo / 'src/stewart/_bootstrap.py').touch()
            old, new, stable = [root / n for n in ('python-old', 'python-new', 'python-stable')]
            old.touch()
            new.touch()
            stable.symlink_to(old)
            with mock.patch.object(install.sys, 'executable', str(stable)), \
                 mock.patch.dict(install.os.environ, {'CODEX_HOME': str(home / '.codex')}), \
                 mock.patch.object(install.shutil, 'which', return_value='/bin/sh'):
                record = install.install(repo, analysis, fieldwork, home)
            self.assertEqual(record['configuration']['python'], str(stable.absolute()))
            stable.unlink()
            stable.symlink_to(new)
            old.unlink()
            self.assertTrue(Path(record['configuration']['python']).exists())
            self.assertIn(str(stable), (home / '.local/bin/stewart-dev-codex').read_text())

    def test_allow_read_checks_descendants_relative_paths_globs_and_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            denied = root / 'analysis'
            repo = root / 'repo'
            denied.mkdir()
            repo.mkdir()
            (repo / 'alias').symlink_to(denied, target_is_directory=True)
            cases = {
                str(denied / 'inquiry'): True,
                '../analysis/inquiry': True,
                'alias/inquiry': True,
                str(denied): True,
                '../ana*/inquiry': True,
                '../*/inquiry': True,
                str(root): False,  # a broader literal allow does not beat the narrower deny
                str(root) + '/**': False,  # Claude strips this suffix before matching
                '.': False,
                './build/**': False,
                '../analysis-unrelated': False,
            }
            for entry, expected in cases.items():
                with self.subTest(entry=entry):
                    self.assertEqual(launch.allow_read_conflicts(entry, repo, [denied]), expected)
            with self.assertRaises(ValueError):
                launch.allow_read_conflicts('$RESEARCH/inquiry', repo, [denied])
            with self.assertRaises(ValueError):
                launch.allow_read_conflicts('../analysis/inquiry', None, [denied])

    def test_install_accepts_current_rust_layout_and_records_cargo(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home, repo, analysis, fieldwork = [root / n for n in ('home', 'repo', 'analysis', 'fieldwork')]
            for path in (home, repo / 'service', analysis, fieldwork):
                path.mkdir(parents=True)
            (repo / 'service/Cargo.toml').write_text('[package]\nname = "stewart"\n')
            with mock.patch.dict(install.os.environ, {'CODEX_HOME': str(home / '.codex')}), \
                 mock.patch.object(install.shutil, 'which', return_value='/fixture/cargo'):
                record = install.install(repo, analysis, fieldwork, home)
            self.assertEqual(record['configuration']['cargo'], '/fixture/cargo')
            with mock.patch.object(install.shutil, 'which', side_effect=lambda name: None if name == 'cargo' else '/bin/sh'):
                with self.assertRaisesRegex(ValueError, 'cargo is not on PATH'):
                    install.install(repo, analysis, fieldwork, home)
            (repo / 'service/Cargo.toml').write_text('[package]\nname = "unrelated"\n')
            with mock.patch.object(install.shutil, 'which', return_value='/bin/sh'):
                with self.assertRaisesRegex(ValueError, 'Stewart source checkout'):
                    install.install(repo, analysis, fieldwork, home)

    def test_install_skips_tools_in_a_versioned_codex_release_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home, repo, analysis, fieldwork = [root / n for n in ('home', 'repo', 'analysis', 'fieldwork')]
            for path in (home, repo / 'src/stewart', analysis, fieldwork):
                path.mkdir(parents=True)
            (repo / 'src/stewart/_bootstrap.py').touch()
            codex_home = home / '.codex'
            release = codex_home / 'packages/app-server-daemon/releases/0.1.0-test/codex-path'
            current = codex_home / 'packages/standalone/current/bin'
            stable = root / 'stable/bin'
            def tool(path):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('#!/bin/sh\n')
                path.chmod(0o755)
            for path in (release / 'rg', stable / 'rg', current / 'codex', stable / 'claude'):
                tool(path)
            search = [release, current, stable]
            with mock.patch.dict(install.os.environ, {'CODEX_HOME': str(codex_home),
                                                      'PATH': install.os.pathsep.join(map(str, search))}):
                config = install.install(repo, analysis, fieldwork, home)['configuration']
                self.assertEqual(config['rg'], str(stable / 'rg'))
                # The updater's stable symlink directory is not a pinned release.
                self.assertEqual(config['codex'], str(current / 'codex'))
                self.assertFalse(launch.missing_tools(config, 'codex'))
                (stable / 'rg').unlink()
                with self.assertRaisesRegex(ValueError, 'versioned Codex release directory') as refused:
                    install.install(repo, analysis, fieldwork, home)
                self.assertIn(str(release / 'rg'), str(refused.exception))
                (release / 'rg').unlink()
                with self.assertRaisesRegex(ValueError, 'rg is not on PATH'):
                    install.install(repo, analysis, fieldwork, home)

    def test_probe_reports_tools_that_cannot_start_instead_of_crashing_or_passing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            fixture = root / 'fixture'
            (fixture / 'git-probe').mkdir(parents=True)
            (root / 'README.md').write_text('readme\n')
            canaries = [root / 'canary-0', root / 'canary-1']
            for path in canaries:
                path.write_text('token\n')
            gone = root / 'releases/0.1.0/codex-path/rg'
            spec = {'repo': str(root), 'fixture': str(fixture), 'token': 'token',
                    'canaries': list(map(str, canaries)), 'rg': str(gone), 'claude': str(root / 'no-claude')}
            with contextlib.redirect_stdout(io.StringIO()) as output:
                result = probe.run(spec)
            self.assertEqual(json.loads(output.getvalue()), result)
            self.assertTrue(launch.valid_probe(result, 'token'))  # complete, so the launcher can read it
            self.assertTrue(result['checks']['cat_positive_control'])
            for name in ('rg_positive_control', 'recursive_search_denied_0', 'recursive_search_denied_1',
                         'auditor_cli_startup'):
                self.assertFalse(result['checks'][name], name)
            self.assertEqual(result['errors'], [f'{gone}: FileNotFoundError: No such file or directory',
                                                f'{root / "no-claude"}: FileNotFoundError: No such file or directory'])
            self.assertFalse((fixture / 'write-test').exists())
            for path in fixture.glob('alias-*'):
                path.unlink()
            # A blocked tool is not evidence that a read was denied.
            with mock.patch.object(probe.subprocess, 'run', side_effect=PermissionError(1, 'Operation not permitted')), \
                 contextlib.redirect_stdout(io.StringIO()):
                blocked = probe.run(spec)['checks']
            self.assertEqual({name for name, value in blocked.items() if value}, {'stewart_read', 'workspace_write'})

    def test_rust_source_check_requires_success_and_the_expected_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / 'service').mkdir()
            manifest = root / 'service/Cargo.toml'
            manifest.touch()
            for code, package, expected in (
                (0, {'name': 'stewart', 'manifest_path': str(manifest)}, True),
                (1, {'name': 'stewart', 'manifest_path': str(manifest)}, False),
                (0, {'name': 'unrelated', 'manifest_path': str(manifest)}, False),
                (0, {'name': 'stewart', 'manifest_path': '/other/Cargo.toml'}, False),
            ):
                with self.subTest(code=code, package=package), mock.patch.object(probe.subprocess, 'run',
                        return_value=subprocess.CompletedProcess([], code, json.dumps({'packages': [package]}))) as run:
                    self.assertEqual(probe.source_check({'cargo': '/fixture/cargo'}, root, root / 'fixture'), expected)
                    run.assert_called_once_with(['/fixture/cargo', 'metadata', '--no-deps', '--locked', '--offline',
                                                 '--format-version', '1'], cwd=manifest.parent,
                                                capture_output=True, timeout=45)
            with mock.patch.object(probe.subprocess, 'run') as run:
                self.assertFalse(probe.source_check({}, root, root / 'fixture'))
                run.assert_not_called()

    def test_settings_conflicts_identify_source_and_do_not_accept_excluded_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'settings.json'
            settings = {'sandbox': {'filesystem': {'allowRead': ['/tmp/analysis space/inquiry']},
                                    'excludedCommands': ['python *']}}
            source.write_text(json.dumps(settings))
            with mock.patch.object(launch, 'claude_sources', return_value={source: Path('/tmp/stewart')}):
                issues = launch.claude_conflicts(self.config)
                self.assertEqual(len(issues), 2)
                self.assertTrue(all(str(source) in issue for issue in issues))
                self.assertIn('allowRead', issues[0])
                self.assertIn('excludedCommands', issues[1])
                source.write_text('{broken')
                self.assertIn('cannot inspect', launch.claude_conflicts(self.config)[0])

    def test_home_settings_keep_the_user_relative_base_when_home_is_an_ancestor(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            config = {**self.config, 'repo': str(home / 'Desktop/stewart')}
            with mock.patch.object(launch.Path, 'home', return_value=home), \
                 mock.patch.dict(launch.os.environ, {'CLAUDE_CONFIG_DIR': str(home / '.claude')}):
                sources = launch.claude_sources(config)
            self.assertEqual(sources[home / '.claude/settings.json'], home / '.claude')
            self.assertEqual(sources[home / 'Desktop/stewart/.claude/settings.json'], home / 'Desktop/stewart')

    def test_fingerprint_diff_names_changes_without_disclosing_settings_values(self):
        old = {'version': 'old', 'settings': {'a': 'secret-old', 'b': 'secret', 'c': None}}
        new = {'version': 'new', 'settings': {'a': 'secret-new', 'b': None, 'c': 'new'}}
        self.assertEqual(launch.fingerprint_changes(old, new),
                         ['version changed', 'a: changed', 'b: removed', 'c: added'])

    def test_failed_checks_write_records_and_invalidate_previous_receipts(self):
        for error in (FileNotFoundError('missing native binary'), KeyError('unexpected stream key')):
            with self.subTest(error=type(error).__name__), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                receipt = root / 'claude-verified.json'
                receipt.write_text('{"passed":true}')
                with mock.patch.object(launch, 'HERE', root), \
                     mock.patch.object(launch, 'fingerprint', side_effect=error), \
                     contextlib.redirect_stdout(io.StringIO()), \
                     contextlib.redirect_stderr(io.StringIO()) as summary:
                    report = launch.check(self.config, 'claude')
                self.assertFalse(report['passed'])
                self.assertIn('claude check FAILED; no verification receipt was written.', summary.getvalue())
                self.assertIn(type(error).__name__, summary.getvalue())
                self.assertIn(report['evidence'], summary.getvalue())
                self.assertIsNone(report['fingerprint'])
                self.assertFalse(receipt.exists())
                evidence = Path(report['evidence'])
                self.assertEqual(json.loads((evidence / 'result.json').read_text()), report)
                self.assertIn(type(error).__name__, (evidence / 'error.txt').read_text())

    def test_conflict_stops_check_before_model_or_probe(self):
        with tempfile.TemporaryDirectory() as directory, \
             mock.patch.object(launch, 'HERE', Path(directory)), \
             mock.patch.object(launch, 'fingerprint', return_value={'test': True}), \
             mock.patch.object(launch, 'claude_conflicts', return_value=['settings.json: conflicting allowRead']), \
             mock.patch.object(launch.subprocess, 'run') as run, \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            report = launch.check(self.config, 'claude')
            run.assert_not_called()
            self.assertFalse(report['passed'])
            self.assertFalse(report['checks']['local_settings_have_no_conflicts'])
            self.assertIn('settings.json', report['errors'][0])

    def test_missing_configured_tool_stops_check_before_model_or_probe(self):
        # The recorded ripgrep lived in a Codex release directory that an update removed.
        for harness in ('claude', 'codex'):
            with self.subTest(harness=harness), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                gone = root / 'releases/0.1.0/codex-path/rg'
                config = {**self.config, 'repo': str(root), 'python': sys.executable, 'codex': sys.executable,
                          'claude': sys.executable, 'rg': str(gone), 'denied': [str(root / 'analysis')]}
                self.assertEqual(launch.missing_tools(config, harness), [f'rg: {gone}'])
                with mock.patch.object(launch, 'HERE', root), \
                     mock.patch.object(launch, 'fingerprint', return_value={'test': True}), \
                     mock.patch.object(launch, 'claude_conflicts', return_value=[]), \
                     mock.patch.object(launch.subprocess, 'run') as run, \
                     contextlib.redirect_stdout(io.StringIO()), \
                     contextlib.redirect_stderr(io.StringIO()) as summary:
                    report = launch.check(config, harness)
                run.assert_not_called()
                self.assertFalse(report['passed'])
                self.assertFalse(report['checks']['configured_tools_present'])
                self.assertIn(f'rg: {gone}', report['errors'][0])
                self.assertIn(f'rg: {gone}', (Path(report['evidence']) / 'error.txt').read_text())
                self.assertIn('Failed: configured_tools_present', summary.getvalue())
                self.assertIn(f'rg: {gone}', summary.getvalue())
                self.assertFalse((root / 'analysis').exists())  # no marker was attempted

    def test_missing_tools_names_unconfigured_and_unexecutable_programs(self):
        with tempfile.TemporaryDirectory() as directory:
            plain = Path(directory) / 'not-executable'
            plain.touch()
            config = {'python': sys.executable, 'claude': sys.executable, 'codex': str(plain)}
            self.assertEqual(launch.missing_tools(config, 'codex'), [f'codex: {plain}', 'rg: not configured'])
            self.assertEqual(launch.missing_tools({**config, 'rg': sys.executable, 'cargo': directory}, 'claude'),
                             [f'cargo: {directory}'])

    def test_probe_without_a_result_is_explained_but_never_passed(self):
        def stream(command, content, error=True):
            return '\n'.join(map(json.dumps, [
                {'message': {'content': [{'type': 'tool_use', 'id': 'b', 'name': 'Bash', 'input': {'command': command}}]}},
                {'message': {'content': [{'type': 'tool_result', 'tool_use_id': 'b', 'is_error': error,
                                          'content': content}]}}]))
        crash = "Exit code 1\nTraceback (most recent call last):\nFileNotFoundError: [Errno 2] No such file: '/gone/rg'\n"
        notes = []
        checks = launch.assess_claude(stream('probe', crash), 'probe', [], 'token', notes=notes)
        self.assertFalse(checks['observed_exact_shell_probe'])
        self.assertEqual(len(notes), 1)
        self.assertIn("FileNotFoundError: [Errno 2] No such file: '/gone/rg'", notes[0])
        notes = []
        self.assertFalse(launch.assess_claude(stream('other', crash), 'probe', [], 'token',
                                              notes=notes)['observed_exact_shell_probe'])
        self.assertEqual(notes, ['probe: the session never ran the exact probe command'])
        reported = json.dumps({'schema': 'stewart.development-probe/1', 'token': 'token',
                               'checks': {key: key != 'rg_positive_control' for key in launch.EXPECTED_PROBE_CHECKS},
                               'errors': ['/gone/rg: FileNotFoundError: No such file or directory']})
        notes = []
        checks = launch.assess_claude(stream('probe', reported), 'probe', [], 'token', notes=notes)
        self.assertTrue(checks['observed_exact_shell_probe'])
        self.assertFalse(checks['rg_positive_control'])
        self.assertEqual(notes, ['probe: /gone/rg: FileNotFoundError: No such file or directory'])
        # Notes explain; a note-free call still returns the same checks.
        self.assertEqual(launch.assess_claude(stream('probe', reported), 'probe', [], 'token'), checks)

    def test_malformed_stream_records_preserve_raw_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'probe.py').write_text('# disposable test probe')
            repo = root / 'repo'
            repo.mkdir()
            denied = [root / n for n in ('analysis', 'fieldwork')]
            for p in denied:
                p.mkdir()
            config = {**self.config, 'repo': str(repo), 'python': sys.executable, 'claude': sys.executable,
                      'denied': list(map(str, denied)), 'rg': sys.executable}
            malformed = json.dumps({'message': {'content': [{'type': 'tool_use', 'name': 'Read'}]}})
            completed = subprocess.CompletedProcess([], 0, stdout=malformed, stderr='')
            with mock.patch.object(launch, 'HERE', root), \
                 mock.patch.object(launch, 'fingerprint', return_value={'test': True}), \
                 mock.patch.object(launch, 'claude_conflicts', return_value=[]), \
                 mock.patch.object(launch, 'checkout_git_fixture', return_value=contextlib.nullcontext()), \
                 mock.patch.object(launch.subprocess, 'run', return_value=completed), \
                 contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                report = launch.check(config, 'claude')
            self.assertFalse(report['passed'])
            evidence = Path(report['evidence'])
            self.assertEqual((evidence / 'claude-stream.jsonl').read_text(), malformed)
            self.assertIn('KeyError', (evidence / 'error.txt').read_text())
            self.assertTrue((evidence / 'invocation.json').exists())
            self.assertTrue(all(not list(p.iterdir()) for p in denied))
            self.assertFalse(list(repo.iterdir()))

    def test_codex_automatically_checks_unusable_receipts_then_launches(self):
        for saved in (None, '{broken', '{"passed":false}',
                      json.dumps({'passed': True, 'fingerprint': {'version': 'old'}})):
            with self.subTest(saved=saved), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                config = {**self.config, 'repo': str(root)}
                (root / 'config.json').write_text(json.dumps(config))
                receipt = root / 'codex-verified.json'
                if saved is not None:
                    receipt.write_text(saved)
                def checked(*args):
                    receipt.write_text(json.dumps({'passed': True, 'fingerprint': {'version': 'new'}}))
                    return {'passed': True}
                with mock.patch.object(launch, 'HERE', root), \
                     mock.patch.object(launch.Path, 'cwd', return_value=root), \
                     mock.patch.object(launch, 'fingerprint', return_value={'version': 'new'}), \
                     mock.patch.object(launch, 'check', side_effect=checked) as check, \
                     mock.patch.object(launch.os, 'execv') as execute, \
                     contextlib.redirect_stdout(io.StringIO()) as output:
                    launch.main(['codex', '--model', 'chosen', '--effort', 'high', 'a prompt'])
                    check.assert_called_once_with(config, 'codex', 'chosen', 'high')
                    execute.assert_called_once_with('/bin/codex', [
                        '/bin/codex', '-p', 'stewart-dev', '--model', 'chosen', '-c',
                        'model_reasoning_effort="high"', '--', 'a prompt'])
                    self.assertIn('Running --check automatically', output.getvalue())
                    check.reset_mock()
                    execute.reset_mock()
                    launch.main(['codex'])
                    check.assert_not_called()
                    execute.assert_called_once()

    def test_automatic_check_failure_or_stale_result_never_launches_or_retries(self):
        for passed in (False, True):
            with self.subTest(passed=passed), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                (root / 'config.json').write_text(json.dumps({**self.config, 'repo': str(root)}))
                with mock.patch.object(launch, 'HERE', root), \
                     mock.patch.object(launch.Path, 'cwd', return_value=root), \
                     mock.patch.object(launch, 'check', return_value={'passed': passed}) as check, \
                     mock.patch.object(launch.os, 'execv') as execute, \
                     contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as stopped:
                        launch.main(['codex'])
                    self.assertNotEqual(stopped.exception.code, 0)
                    check.assert_called_once()
                    execute.assert_not_called()

    def test_explicit_checks_exit_without_launching_and_claude_never_autochecks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / 'config.json').write_text(json.dumps({**self.config, 'repo': str(root)}))
            with mock.patch.object(launch, 'HERE', root), \
                 mock.patch.object(launch.Path, 'cwd', return_value=root), \
                 mock.patch.object(launch, 'claude_conflicts', return_value=[]), \
                 mock.patch.object(launch, 'check') as check, \
                 mock.patch.object(launch.os, 'execv') as execute, \
                 contextlib.redirect_stderr(io.StringIO()) as errors:
                with self.assertRaises(SystemExit):
                    launch.main(['claude'])
                check.assert_not_called()
                self.assertIn('--check', errors.getvalue())
                for harness in ('codex', 'claude'):
                    for passed in (False, True):
                        check.reset_mock()
                        check.return_value = {'passed': passed}
                        with self.assertRaises(SystemExit) as stopped:
                            launch.main([harness, '--check', '--model', 'chosen', '--effort', 'high'])
                        self.assertEqual(stopped.exception.code, 0 if passed else 1)
                        check.assert_called_once_with({**self.config, 'repo': str(root)}, harness, 'chosen', 'high')
                execute.assert_not_called()

    def test_verified_startup_forwards_only_user_options(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            for name in ('launch.py', 'probe.py'):
                (root / name).write_bytes(Path(launch.__file__).with_name(name).read_bytes())
            fake = root / 'fake-cli'
            fake.write_text('#!/bin/sh\nif [ "$1" = "--version" ]; then echo fake-1; else printf "%s\\n" "$@"; fi\n')
            fake.chmod(0o755)
            config = {**self.config, 'repo': str(root), 'codex': str(fake), 'claude': str(fake)}
            (root / 'config.json').write_text(json.dumps(config))
            command = [sys.executable, '-I', '-B', str(root / 'launch.py'), 'codex']
            old = launch.HERE
            try:
                launch.HERE = root
                (root / 'codex-verified.json').write_text(json.dumps({
                    'passed': True, 'fingerprint': launch.fingerprint(config, 'codex')}))
            finally:
                launch.HERE = old
            p = subprocess.run([*command, '--model', 'chosen', '--effort', 'high', 'a prompt'],
                               cwd=root, capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn('--model\nchosen\n', p.stdout)
            self.assertIn('--\na prompt\n', p.stdout)


if __name__ == '__main__':
    unittest.main()
