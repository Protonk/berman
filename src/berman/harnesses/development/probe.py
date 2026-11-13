"""Harmless filesystem and development checks; never print protected contents."""
from __future__ import annotations

import errno
import json
import os
from pathlib import Path
import subprocess
import sys


def start(argv: list, problems: list, **options):
    """Run a tool and return its result, or None when it could not start or finish.

    A missing or blocked tool is a recorded failure. It must not end the probe
    without a result, and callers must not read it as a denied read.
    """
    try:
        return subprocess.run(argv, capture_output=True, **options)
    except (OSError, subprocess.TimeoutExpired) as exc:
        reason = getattr(exc, 'strerror', None)
        problems.append(f'{argv[0]}: {type(exc).__name__}' + (f': {reason}' if reason else ''))
        return None


def source_check(spec: dict, root: Path, fixture: Path, problems: list | None = None) -> bool:
    problems = [] if problems is None else problems
    manifest = root / 'service/Cargo.toml'
    if manifest.is_file():
        if not spec.get('cargo'):
            return False
        # This is a local source/toolchain access check, not a Linux build or
        # runtime qualification. Run from service/ to load its Cargo settings.
        p = start([spec['cargo'], 'metadata', '--no-deps', '--locked', '--offline', '--format-version', '1'],
                  problems, cwd=manifest.parent, timeout=45)
        if p is None or p.returncode != 0:
            return False
        metadata = json.loads(p.stdout)
        return any(package.get('name') == 'stewart' and package.get('manifest_path') == str(manifest)
                   for package in metadata.get('packages', []))
    p = start([sys.executable, '-I', '-B', str(root / 'src/stewart/_bootstrap.py'),
               'source', '--manager-home', str(fixture / 'manager-home'), '--', '--help'],
              problems, timeout=45)
    return p is not None and p.returncode == 0


def run(spec: dict) -> dict:
    checks = {}
    problems = []
    root, fixture = Path(spec['repo']), Path(spec['fixture'])
    checks['stewart_read'] = bool((root / 'README.md').read_bytes())
    output = fixture / 'write-test'
    output.write_text('development write\n')
    checks['workspace_write'] = output.read_text() == 'development write\n'
    p = start(['/bin/cat', str(output)], problems)
    checks['cat_positive_control'] = p is not None and p.returncode == 0 and p.stdout == b'development write\n'
    p = start([spec['rg'], '-l', 'development write', str(output)], problems)
    checks['rg_positive_control'] = p is not None and p.returncode == 0 and bool(p.stdout)
    output.unlink()
    for index, name in enumerate(spec['canaries']):
        path = Path(name)
        try:
            path.read_bytes()
        except OSError as exc:
            checks[f'python_read_denied_{index}'] = exc.errno in (errno.EACCES, errno.EPERM)
        else:
            checks[f'python_read_denied_{index}'] = False
        alias = fixture / f'alias-{index}'
        alias.symlink_to(path)
        try:
            alias.read_bytes()
        except OSError as exc:
            checks[f'symlink_read_denied_{index}'] = exc.errno in (errno.EACCES, errno.EPERM)
        else:
            checks[f'symlink_read_denied_{index}'] = False
        # A tool that never ran proves nothing about the read boundary.
        p = start(['/bin/cat', str(path)], problems)
        checks[f'cat_read_denied_{index}'] = p is not None and p.returncode != 0 and not p.stdout
        p = start([spec['rg'], '--hidden', '--no-ignore', '-l', spec['token'], str(path.parent)], problems)
        checks[f'recursive_search_denied_{index}'] = p is not None and p.returncode == 2 and not p.stdout
    env = dict(os.environ, GIT_OPTIONAL_LOCKS='0')
    p = start(['git', '-C', str(root), 'rev-parse', '--show-toplevel'], problems, env=env)
    checks['git_read'] = (p is not None and p.returncode == 0
                          and Path(os.fsdecode(p.stdout).strip()).resolve() == root)
    # Its separate metadata lives in a disposable directory under Stewart's
    # actual .git, so these writes exercise that path's permissions. Stewart's
    # own index, objects and refs are never used by this disposable repository.
    repo = fixture / 'git-probe'
    # The host initialized this disposable repository, as a developer's checkout
    # already exists before a session. Claude protects creation of .git/config.
    (repo / 'file.txt').write_text('probe\n')
    added = start(['git', '-C', str(repo), 'add', 'file.txt'], problems)
    p = start(['git', '-C', str(repo), '-c', 'user.name=Launcher probe',
               '-c', 'user.email=probe@invalid', '-c', 'commit.gpgsign=false',
               '-c', 'core.hooksPath=/dev/null', 'commit', '-qm', 'probe'], problems)
    checks['git_checkout_metadata_commit'] = (added is not None and p is not None
                                              and added.returncode == 0 and p.returncode == 0)
    checks['stewart_source_check'] = source_check(spec, root, fixture, problems)
    # Auditor CLI startup and nested shell subprocesses; this does not commission an audit.
    p = start([spec['claude'], '--version'], problems, timeout=20)
    checks['auditor_cli_startup'] = p is not None and p.returncode == 0 and b'Claude Code' in p.stdout
    # Tool names and error kinds only; the checks above already carry the outcome.
    result = {'schema': 'stewart.development-probe/1', 'token': spec['token'], 'checks': checks,
              'errors': list(dict.fromkeys(problems))}
    print(json.dumps(result, sort_keys=True))
    return result


if __name__ == '__main__':
    result = run(json.loads(Path(sys.argv[1]).read_text()))
    raise SystemExit(0 if all(result['checks'].values()) else 1)
