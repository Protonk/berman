"""Opt-in local development launchers. No shared harness settings are changed."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import glob
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

HERE = Path(__file__).resolve().parent
EXPECTED_PROBE_CHECKS = {
    'stewart_read', 'workspace_write', 'cat_positive_control', 'rg_positive_control',
    'git_read', 'git_checkout_metadata_commit', 'stewart_source_check', 'auditor_cli_startup',
    *(f'{kind}_{i}' for i in range(2) for kind in (
        'python_read_denied', 'cat_read_denied', 'recursive_search_denied', 'symlink_read_denied')),
}


def valid_probe(result: dict, token: str) -> bool:
    return (result.get('schema') == 'stewart.development-probe/1'
            and result.get('token') == token
            and set(result.get('checks', {})) == EXPECTED_PROBE_CHECKS
            and all(type(value) is bool for value in result['checks'].values()))


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def codex_profile(config: dict) -> str:
    access = {p: 'deny' for p in config['denied']}
    access[str(Path(config['repo']) / '.git')] = 'write'
    access.update({str(Path(config['repo']) / p): 'read' for p in ('.agents', '.codex', '.aws')})
    lines = ['# stewart-development-launcher-profile', 'default_permissions = "stewart_dev"',
             '', '[tui]', 'terminal_title = ["app-name", "thread", "project"]',
             '', '[permissions.stewart_dev]', 'extends = ":workspace"',
             '', '[permissions.stewart_dev.filesystem]']
    lines.extend(f'{json.dumps(path)} = {json.dumps(mode)}' for path, mode in access.items())
    return '\n'.join(lines) + '\n'


def codex_options(config: dict) -> list[str]:
    return ['-p', config['codex_profile_name']]


def claude_settings(config: dict) -> dict:
    return {
        'permissions': {'deny': [f'Read(/{p}/**)' for p in config['denied']],
                        'ask': ['Bash(dangerouslyDisableSandbox:true)']},
        'sandbox': {
            'enabled': True, 'failIfUnavailable': True,
            # Restrictive outer policies can prevent a child sandbox starting.
            # Keep an explicitly approved retry; Claude parent/child is untested.
            'allowUnsandboxedCommands': True,
            'filesystem': {'denyRead': config['denied']},
        },
    }


def native_command(config: dict, harness: str, model=None, effort=None) -> list[str]:
    if harness == 'codex':
        argv = [config['codex'], *codex_options(config)]
        if model:
            argv += ['--model', model]
        if effort:
            argv += ['-c', 'model_reasoning_effort=' + json.dumps(effort)]
    else:
        argv = [config['claude'], '--settings', config['claude_settings_file'],
                '--name', 'Stewart - Claude']
        if model:
            argv += ['--model', model]
        if effort:
            argv += ['--effort', effort]
    return argv


def claude_sources(config: dict) -> dict[Path, Path | None]:
    """Local settings and their relative sandbox-path bases; not remote policy."""
    home = Path.home()
    repo = Path(config['repo'])
    claude_home = Path(os.environ.get('CLAUDE_CONFIG_DIR', str(home / '.claude'))).expanduser().resolve()
    managed = Path('/Library/Application Support/ClaudeCode')
    sources = {claude_home / n: claude_home for n in ('settings.json', 'settings.local.json')}
    # The public path reference specifies relative bases for user/project files,
    # not these scopes. Require absolute or home paths there instead of guessing.
    sources[managed / 'managed-settings.json'] = None
    sources.update({p: None for p in sorted((managed / 'managed-settings.d').glob('*.json'))})
    sources[Path(config['claude_settings_file'])] = None
    for root in (repo, *repo.parents):
        for name in ('settings.json', 'settings.local.json'):
            sources.setdefault(root / '.claude' / name, repo)
    return sources


def settings_files(config: dict, harness: str) -> list[Path]:
    files = [HERE / 'launch.py', HERE / 'probe.py', HERE / 'config.json']
    if harness == 'codex':
        codex_home = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))).expanduser().resolve()
        files += [codex_home / 'config.toml', Path('/etc/codex/config.toml'),
                  Path('/etc/codex/requirements.toml'),
                  codex_home / (config['codex_profile_name'] + '.config.toml')]
        repo = Path(config['repo'])
        files += [p / '.codex/config.toml' for p in (repo, *repo.parents)]
        files += [rule for folder in {p.parent / 'rules' for p in files if p.suffix == '.toml'}
                  for rule in sorted(folder.glob('*.rules'))]
    else:
        files += list(claude_sources(config))
    return list(dict.fromkeys(files))


def fingerprint(config: dict, harness: str) -> dict:
    binary = Path(config[harness]).resolve()
    stat = binary.stat()
    version = subprocess.run([str(binary), '--version'], check=True, capture_output=True,
                             text=True, timeout=20).stdout.strip()
    return {'binary': str(binary), 'version': version, 'size': stat.st_size,
            'mtime_ns': stat.st_mtime_ns, 'repo': config['repo'],
            'settings': {str(p): digest(p) if p.is_file() else None for p in settings_files(config, harness)}}


def allow_read_conflicts(entry: str, base: Path | None, denied: list[Path]) -> bool:
    """Conservative overlap check, without expanding globs into research trees."""
    if not entry or any(c in entry for c in '$`'):
        raise ValueError('allowRead syntax needs manual review')
    if entry.endswith('/**'):
        entry = entry[:-3] or '/'
    path = Path(entry).expanduser()
    if not path.is_absolute():
        if base is None:
            raise ValueError('use an absolute or ~/ path in this settings source; relative base is unverified')
        path = base / path
    parts = []
    for part in path.parts:
        if glob.has_magic(part):
            break
        parts.append(part)
    prefix = Path(*parts).resolve()
    wildcard = len(parts) != len(path.parts)
    return any(prefix.is_relative_to(root) or (wildcard and root.is_relative_to(prefix)) for root in denied)


def claude_conflicts(config: dict) -> list[str]:
    issues = []
    denied = [Path(p).resolve() for p in config['denied']]
    for source, base in claude_sources(config).items():
        if not source.exists():
            continue
        try:
            data = json.loads(source.read_text())
            sandbox = data.get('sandbox', {})
            filesystem = sandbox.get('filesystem', {})
            entries = filesystem.get('allowRead', [])
            excluded = sandbox.get('excludedCommands', [])
            if not isinstance(entries, list) or not all(isinstance(p, str) for p in entries):
                raise ValueError('sandbox.filesystem.allowRead must be a list of paths')
            if not isinstance(excluded, list):
                raise ValueError('sandbox.excludedCommands must be a list')
            for entry in entries:
                try:
                    conflict = allow_read_conflicts(entry, base, denied)
                except (ValueError, OSError, RuntimeError) as exc:
                    issues.append(f'{source}: sandbox.filesystem.allowRead {entry!r}: {exc}')
                    continue
                if conflict:
                    issues.append(f'{source}: sandbox.filesystem.allowRead {entry!r} may reopen a research path')
            if excluded:
                issues.append(f'{source}: sandbox.excludedCommands is nonempty; commands outside the sandbox '
                              'cannot be qualified by this profile (use the explicit approval route)')
            if filesystem.get('disabled'):
                issues.append(f'{source}: sandbox.filesystem.disabled removes filesystem restrictions')
        except (OSError, ValueError, AttributeError, TypeError) as exc:
            issues.append(f'{source}: cannot inspect sandbox settings: {exc}')
    return issues


def fingerprint_changes(before: dict, after: dict) -> list[str]:
    changes = []
    for key in sorted((before.keys() | after.keys()) - {'settings'}):
        if before.get(key) != after.get(key):
            changes.append(f'{key} changed')
    old, new = before.get('settings', {}), after.get('settings', {})
    for path in sorted(old.keys() | new.keys()):
        if old.get(path) != new.get(path):
            action = 'added' if old.get(path) is None else 'removed' if new.get(path) is None else 'changed'
            changes.append(f'{path}: {action}')
    return changes


def verification_problem(config: dict, harness: str) -> str | None:
    receipt = HERE / f'{harness}-verified.json'
    try:
        if not receipt.exists():
            return 'no passing verification receipt'
        saved = json.loads(receipt.read_text())
        if not isinstance(saved, dict) or saved.get('passed') is not True or not isinstance(saved.get('fingerprint'), dict):
            return f'{receipt}: invalid or unsuccessful verification receipt'
        changes = fingerprint_changes(saved['fingerprint'], fingerprint(config, harness))
        return '\n'.join(changes) if changes else None
    except (OSError, ValueError, AttributeError, TypeError, subprocess.SubprocessError) as exc:
        return f'cannot validate {receipt}: {type(exc).__name__}: {exc}'


def missing_tools(config: dict, harness: str) -> list[str]:
    """Configured programs a check runs that are absent or not executable."""
    missing = []
    for name in dict.fromkeys(('python', harness, 'claude', 'rg', 'cargo')):
        path = config.get(name)
        if name == 'cargo' and not path:
            continue  # recorded only for the Rust layout
        if not path or not Path(path).is_file() or not os.access(path, os.X_OK):
            missing.append(f'{name}: {path or "not configured"}')
    return missing


def last_line(text: str) -> str:
    """The end of a tool's output, where an uncaught error names itself."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1][:300] if lines else '(no output)'


def probe_errors(observed: dict) -> list[str]:
    errors = observed.get('errors', [])
    return ['probe: ' + e for e in errors if isinstance(e, str)] if isinstance(errors, list) else []


def permission_context(output: str) -> str:
    selected = []
    def visit(value):
        if isinstance(value, str):
            if '<environment_context>' in value or '<permissions instructions>' in value:
                selected.append(value)
        elif isinstance(value, dict):
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
    visit(json.loads(output))
    return '\n'.join(selected)


def assess_claude(stream: str, shell_command: str, canaries: list[str], token: str, allowed_file=None,
                  notes: list | None = None) -> dict:
    """Require the actual tool calls/results; an assistant's summary cannot pass.

    `notes` collects why the probe gave no usable result. It explains a failure
    and never decides one.
    """
    calls, results, permission_denials = {}, {}, []
    for line in stream.splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        message = event.get('message', {})
        content = message.get('content', []) if isinstance(message, dict) else []
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict):
                continue
            if block.get('type') == 'tool_use':
                calls[block['id']] = block
            elif block.get('type') == 'tool_result':
                results[block['tool_use_id']] = block
        permission_denials.extend(event.get('permission_denials', []))
    checks = {}
    if allowed_file is not None:
        checks['file_tool_positive_control'] = any(
            call.get('name') == 'Read' and call.get('input', {}).get('file_path') == allowed_file
            and ident in results and not results[ident].get('is_error')
            and token in json.dumps(results[ident].get('content', ''))
            for ident, call in calls.items())
    for i, path in enumerate(canaries):
        denied = any(d.get('tool_name') == 'Read' and d.get('tool_input', {}).get('file_path') == path
                     for d in permission_denials)
        for ident, call in calls.items():
            if call.get('name') == 'Read' and call.get('input', {}).get('file_path') == path:
                result = results.get(ident, {})
                text = json.dumps(result.get('content', ''))
                denied |= bool(result.get('is_error') and ('denied' in text.lower() or 'permission' in text.lower()))
        checks[f'file_tool_denied_{i}'] = denied
    observed = None
    attempts = []
    for ident, call in calls.items():
        if call.get('name') != 'Bash' or call.get('input', {}).get('command') != shell_command:
            continue
        result = results.get(ident, {})
        content = result.get('content', '')
        if isinstance(content, list):
            content = '\n'.join(b.get('text', '') for b in content)
        attempts.append(content)
        for line in content.splitlines():
            try:
                candidate = json.loads(line)
            except ValueError:
                continue
            if isinstance(candidate, dict) and valid_probe(candidate, token):
                observed = candidate
    checks['observed_exact_shell_probe'] = observed is not None
    if observed:
        checks.update(observed['checks'])
    if notes is not None:
        if observed:
            notes.extend(probe_errors(observed))
        elif attempts:
            notes.append('probe: the exact command ran without printing a result; its last output line: '
                         + last_line(attempts[-1]))
        else:
            notes.append('probe: the session never ran the exact probe command')
    return checks


@contextmanager
def checkout_git_fixture(repo: Path, fixture: Path):
    """Use the checkout's metadata boundary without its index, objects or refs."""
    git_dir = Path(subprocess.check_output(
        ['git', '-C', str(repo), 'rev-parse', '--absolute-git-dir'], text=True).strip())
    with tempfile.TemporaryDirectory(prefix='.stewart-dev-check-', dir=git_dir) as temporary:
        worktree = fixture / 'git-probe'
        subprocess.run(['git', 'init', '-q', '--separate-git-dir',
                        str(Path(temporary) / 'git'), str(worktree)],
                       check=True, capture_output=True)
        yield worktree


def check(config: dict, harness: str, model=None, effort=None) -> dict:
    stamp = time.strftime('%Y%m%dT%H%M%S', time.gmtime()) + '-' + uuid.uuid4().hex[:8]
    evidence = HERE / 'checks' / (stamp + '-' + harness)
    evidence.mkdir(parents=True)
    token = 'stewart-dev-check-' + uuid.uuid4().hex
    canaries = []
    checks = {}
    errors = []
    before = None
    receipt = HERE / f'{harness}-verified.json'
    print(f'Checking {harness}; evidence: {evidence}', flush=True)
    try:
        # A failed recheck must not leave a previous passing receipt usable.
        receipt.unlink(missing_ok=True)
        before = fingerprint(config, harness)
        if harness == 'claude':
            issues = claude_conflicts(config)
            checks['local_settings_have_no_conflicts'] = not issues
            if issues:
                raise ValueError('conflicting Claude sandbox settings:\n' + '\n'.join(issues))
        # Before any sandbox or model session: a recorded path can outlive its
        # program, as when an updater removes the release that held it.
        missing = missing_tools(config, harness)
        checks['configured_tools_present'] = not missing
        if missing:
            raise ValueError('configured tools are missing or not executable:\n' + '\n'.join(missing)
                             + '\nReinstall the launchers, then run this check again.')
        with tempfile.TemporaryDirectory(prefix='.stewart-dev-check-', dir=config['repo']) as temporary, \
                checkout_git_fixture(Path(config['repo']), Path(temporary)):
            fixture = Path(temporary)
            for denied in config['denied']:
                path = Path(denied) / ('.' + token)
                with path.open('x') as stream:
                    stream.write(token + '\n')
                canaries.append(path)
            spec = {'repo': config['repo'], 'fixture': str(fixture), 'token': token,
                    'canaries': list(map(str, canaries)), 'rg': config['rg'], 'claude': config['claude'],
                    'cargo': config.get('cargo')}
            spec_path = fixture / 'spec.json'
            spec_path.write_text(json.dumps(spec))
            allowed_file = fixture / 'allowed-marker.txt'
            allowed_file.write_text(token + '\n')
            probe_path = fixture / 'probe.py'
            shutil.copyfile(HERE / 'probe.py', probe_path)
            probe = [config['python'], '-I', '-B', str(probe_path), str(spec_path)]
            # Positive controls: the caller can read both exact files before the harness probe.
            checks['unrestricted_canaries_readable'] = all(p.read_text() == token + '\n' for p in canaries)
            if harness == 'codex':
                command = [config['codex'], *codex_options(config), 'sandbox', '--', *probe]
                (evidence / 'invocation.json').write_text(json.dumps(command, indent=2) + '\n')
                p = subprocess.run(command, cwd=config['repo'], capture_output=True, text=True, timeout=90)
                (evidence / 'shell.stdout').write_text(p.stdout)
                (evidence / 'shell.stderr').write_text(p.stderr)
                try:
                    observed = json.loads(p.stdout)
                except ValueError:
                    raise ValueError('the probe printed no result; its last error line: '
                                     + last_line(p.stderr)) from None
                checks['probe_exit_zero'] = p.returncode == 0
                checks['probe_complete_and_token_matches'] = valid_probe(observed, token)
                if valid_probe(observed, token):
                    checks.update(observed['checks'])
                    errors.extend(probe_errors(observed))
                p = subprocess.run([config['codex'], *codex_options(config), 'debug', 'prompt-input'],
                                   cwd=config['repo'], capture_output=True, text=True, timeout=60)
                context = permission_context(p.stdout) if p.returncode == 0 else ''
                (evidence / 'permission-context.txt').write_text(context)
                (evidence / 'context.stderr').write_text(p.stderr)
                checks['interactive_context_denies_both'] = all(
                    f'<entry access="deny" escalatable="false"><path>{path}</path></entry>' in context
                    for path in config['denied'])
                checks['ordinary_protected_paths_retained'] = all(
                    f'<entry access="read"><path>{Path(config["repo"]) / path}</path></entry>' in context
                    for path in ('.agents', '.codex', '.aws'))
                checks['checkout_git_context_writable'] = (
                    f'<entry access="write"><path>{Path(config["repo"]) / ".git"}</path></entry>' in context)
            else:
                shell_command = shlex.join(probe)
                prompt = ('This is a launcher verification, not a development assignment. '
                          'First use the Read tool on this allowed marker file: ' + str(allowed_file) + '. '
                          'Use the Read tool once on each of these two harmless temporary marker files: '
                          + json.dumps(list(map(str, canaries))) + '. '
                          'Permission denials are the expected result; record them and continue. '
                          'Read the local verification program ' + str(probe_path) + ' and its spec '
                          + str(spec_path) + ' before running it, so you can inspect the operations. '
                          'Then use Bash to run exactly this command, without prefixes or suffixes: '
                          + shell_command + '. '
                          'Do not modify files or substitute another command. The probe itself performs '
                          'disposable write checks. Do not request unsandboxed execution. End after the tool results.')
                # Approve only this exact harmless probe for the check. The
                # filesystem sandbox remains active; normal launches do not get
                # this allow rule. This isolates enforcement from classifier decisions.
                command = [*native_command(config, harness, model, effort),
                           '--allowedTools', f'Bash({shell_command})', '--print', '--verbose',
                           '--output-format', 'stream-json', '--permission-prompts', 'none', prompt]
                (evidence / 'invocation.json').write_text(json.dumps(command, indent=2) + '\n')
                print('Claude check uses a short model session with your normal login and selected/default model.', flush=True)
                p = subprocess.run(command, cwd=config['repo'], capture_output=True, text=True, timeout=240)
                (evidence / 'claude-stream.jsonl').write_text(p.stdout)
                (evidence / 'claude.stderr').write_text(p.stderr)
                checks['harness_exit_zero'] = p.returncode == 0
                checks.update(assess_claude(p.stdout, shell_command, list(map(str, canaries)), token,
                                            str(allowed_file), errors))
            checks['probe_program_unchanged'] = digest(probe_path) == digest(HERE / 'probe.py')
    except Exception as exc:
        # The check is an evidence-producing boundary: malformed native streams
        # and preflight failures should get a failed result, not lose the record.
        checks['check_completed'] = False
        errors.append(f'check: {type(exc).__name__}: {exc}')
        if isinstance(exc, subprocess.TimeoutExpired):
            for name, value in [('timeout.stdout', exc.stdout), ('timeout.stderr', exc.stderr)]:
                if value:
                    (evidence / name).write_bytes(value if isinstance(value, bytes) else value.encode())
    finally:
        checks['temporary_markers_cleaned'] = True
        for path in canaries:
            # Remove only this probe's uniquely named, still-identical marker.
            try:
                if path.is_file() and not path.is_symlink() and path.read_text() == token + '\n':
                    path.unlink()
                elif path.exists() or path.is_symlink():
                    raise ValueError(f'changed marker left untouched: {path}')
            except Exception as exc:
                checks['temporary_markers_cleaned'] = False
                errors.append(f'cleanup: {type(exc).__name__}: {exc}')
    try:
        checks['configuration_unchanged_during_check'] = before is not None and before == fingerprint(config, harness)
    except Exception as exc:
        checks['configuration_unchanged_during_check'] = False
        errors.append(f'final fingerprint: {type(exc).__name__}: {exc}')
    if errors:
        (evidence / 'error.txt').write_text('\n'.join(errors) + '\n')
    report = {'schema': 'stewart.development-launcher-check/1', 'harness': harness,
              'passed': bool(checks) and all(checks.values()), 'checks': checks,
              'fingerprint': before, 'evidence': str(evidence),
              'errors': errors,
              'checked_at_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
    (evidence / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
    if report['passed']:
        receipt.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2), flush=True)
    if not report['passed']:
        # The report above is long; say plainly that it failed and why.
        failed = [name for name, value in checks.items() if not value]
        print('\n'.join([f'{harness} check FAILED; no verification receipt was written.',
                         'Failed: ' + ', '.join(failed), *errors, f'Evidence: {evidence}']),
              file=sys.stderr, flush=True)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('harness', choices=('codex', 'claude'))
    parser.add_argument('--check', action='store_true', help='verify access; Claude uses a short model session')
    parser.add_argument('--status', action='store_true', help='read the current readiness receipt; starts no session')
    parser.add_argument('--model')
    parser.add_argument('--effort')
    parser.add_argument('prompt', nargs='?')
    args = parser.parse_args(argv)
    config = json.loads((HERE / 'config.json').read_text())
    if Path.cwd().resolve() != Path(config['repo']).resolve():
        parser.error('start from the configured Stewart root: ' + config['repo'])
    if args.status:
        problem = verification_problem(config, args.harness)
        if args.harness == 'claude':
            conflicts = claude_conflicts(config)
            if conflicts:
                problem = '\n'.join(conflicts)
        print(json.dumps({'ready': problem is None, 'harness': args.harness, 'problem': problem}))
        raise SystemExit(0 if problem is None else 1)
    if args.check:
        raise SystemExit(0 if check(config, args.harness, args.model, args.effort)['passed'] else 1)
    if args.harness == 'claude':
        issues = claude_conflicts(config)
        if issues:
            parser.error('conflicting Claude sandbox settings:\n' + '\n'.join(issues)
                         + '\nReview the named settings, then run this launcher with --check.')
    problem = verification_problem(config, args.harness)
    if problem and args.harness == 'codex':
        print('Codex development needs verification:\n' + problem
              + '\nRunning --check automatically before launch (no model request).', flush=True)
        if not check(config, args.harness, args.model, args.effort)['passed']:
            print('Codex development check failed; launch stopped. See the check evidence above.', file=sys.stderr)
            raise SystemExit(1)
        # Check once, then validate the saved receipt again; never loop or launch
        # if settings changed between the check and this process replacement.
        problem = verification_problem(config, args.harness)
    if problem:
        parser.error('this installation or configuration needs verification:\n' + problem
                     + '\nRun this launcher with --check.')
    argv = native_command(config, args.harness, args.model, args.effort)
    if args.prompt:
        argv += ['--', args.prompt]
    print('Stewart development: research-folder read exclusions active.', flush=True)
    os.execv(argv[0], argv)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f'Launcher stopped: {exc}', file=sys.stderr)
        raise SystemExit(1)
