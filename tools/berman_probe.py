"""Run inside the native sandbox: common access checks, then a connection's declared project checks.

Common checks never print protected contents. Project checks are explicit argument lists from a
researcher-controlled connection description; their exit status (and optional output text) is
recorded as a boolean.
"""
import errno
import json
import os
from pathlib import Path
import subprocess
import sys


def denied(checks, label, path, fixture, rg, token):
    try:
        path.read_bytes()
    except OSError as exc:
        checks[f'python_read_denied_{label}'] = exc.errno in (errno.EACCES, errno.EPERM)
    else:
        checks[f'python_read_denied_{label}'] = False
    alias = fixture / f'alias-{label}'
    alias.symlink_to(path)
    try:
        alias.read_bytes()
    except OSError as exc:
        checks[f'symlink_read_denied_{label}'] = exc.errno in (errno.EACCES, errno.EPERM)
    else:
        checks[f'symlink_read_denied_{label}'] = False
    p = subprocess.run(['/bin/cat', str(path)], capture_output=True)
    checks[f'cat_read_denied_{label}'] = p.returncode != 0 and not p.stdout
    p = subprocess.run([rg, '--hidden', '--no-ignore', '-l', token, str(path.parent)], capture_output=True)
    checks[f'recursive_search_denied_{label}'] = p.returncode == 2 and not p.stdout


def run(spec):
    checks = {}
    repo, fixture, packet = Path(spec['repo']), Path(spec['fixture']), Path(spec['packet'])
    reads = [repo / name for name in spec['reads']]
    checks['project_read'] = all(bool(p.read_bytes()) for p in reads) if reads else bool(os.listdir(repo))
    output = fixture / 'write-test'
    output.write_text('development write\n')
    checks['workspace_write'] = output.read_text() == 'development write\n'
    p = subprocess.run(['/bin/cat', str(output)], capture_output=True)
    checks['cat_positive_control'] = p.returncode == 0 and p.stdout == b'development write\n'
    p = subprocess.run([spec['rg'], '-l', 'development write', str(output)], capture_output=True)
    checks['rg_positive_control'] = p.returncode == 0 and bool(p.stdout)
    output.unlink()
    for label, name in spec['canaries'].items():
        denied(checks, label, Path(name), fixture, spec['rg'], spec['token'])
    env = dict(os.environ, GIT_OPTIONAL_LOCKS='0')
    p = subprocess.run(['git', '-C', str(repo), 'rev-parse', '--show-toplevel'], env=env, capture_output=True)
    checks['git_read'] = p.returncode == 0 and Path(os.fsdecode(p.stdout).strip()).resolve() == repo
    # The host initialized this disposable repository, as a developer's checkout already exists.
    probe = fixture / 'git-probe'
    (probe / 'file.txt').write_text('probe\n')
    added = subprocess.run(['git', '-C', str(probe), 'add', 'file.txt'], capture_output=True)
    p = subprocess.run(['git', '-C', str(probe), '-c', 'user.name=Berman probe', '-c', 'user.email=probe@invalid',
                        '-c', 'commit.gpgsign=false', '-c', 'core.hooksPath=/dev/null', 'commit', '-qm', 'probe'],
                       capture_output=True)
    checks['git_disposable_commit'] = added.returncode == 0 and p.returncode == 0
    checks['assigned_instructions_readable'] = bool((packet / 'INSTRUCTIONS.md').read_bytes())
    marker = packet / 'notes' / ('.' + spec['token'])
    try:
        with marker.open('x') as stream:
            stream.write(spec['token'])
        checks['assigned_notes_writable'] = marker.read_text() == spec['token']
    finally:
        if marker.exists() and marker.read_text() == spec['token']:
            marker.unlink()
    for check in spec['project_checks']:
        p = subprocess.run(check['argv'], capture_output=True, timeout=check['timeout'])
        checks[check['id']] = p.returncode == 0 and (
            'stdout_contains' not in check or check['stdout_contains'].encode() in p.stdout)
    return {'schema': 'berman.access-probe/1', 'token': spec['token'], 'checks': checks}


if __name__ == '__main__':
    result = run(json.loads(Path(sys.argv[1]).read_text()))
    print(json.dumps(result, sort_keys=True))
    raise SystemExit(0 if all(result['checks'].values()) else 1)
