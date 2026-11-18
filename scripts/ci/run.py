"""One integration trial. The checkout drives the build; the installed candidate drives fieldwork."""

from argparse import Namespace
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
from uuid import uuid4
import zipfile

from berman import connector, evidence, executor, files, verify
from berman.archive import package
from berman.executor import capture

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / 'tests/fixtures/ci'
STATE = Path.home() / '.local/share/berman-ci'
SOURCE_CHECKS = (
    ('ruff', 'check'),
    ('ruff', 'format', '--check'),
    ('lint-imports',),
    ('python', '-m', 'unittest', 'discover', '-s', 'tests', '-t', '.'),
    ('berman', 'verify'),
)


class Trial:
    def __init__(self, directory):
        self.directory = directory
        self.path = directory / 'receipt.json'
        self.data = files.read(self.path) if self.path.exists() else {}

    def update(self, **fields):
        self.data.update(fields)
        files.write(self.path, self.data)

    def step(self, name):
        self.update(step=name)
        print(f'  {name}', file=sys.stderr, flush=True)


def ssh(settings, *argv, **kwargs):
    try:
        return subprocess.run([*connector.ssh_client(settings), shlex.join(argv)], check=True, **kwargs)
    except subprocess.CalledProcessError as error:
        raise ValueError(f'SSH to {settings["host"]} exited {error.returncode} while running {argv[0]}') from error


def source_check(trial):
    log = trial.directory / 'build.log'
    with log.open('wb') as output:
        for args in SOURCE_CHECKS:
            trial.step(shlex.join(args))
            subprocess.run(['uv', 'run', '--locked', *args], cwd=ROOT, check=True, stdout=output, stderr=output)
        trial.step('build candidate wheel')
        # Through a source distribution, never directly from the checkout: setuptools' build/
        # directory keeps modules that have since been moved or removed, and would ship them.
        subprocess.run(
            ['uv', 'build', '--out-dir', trial.directory],
            cwd=ROOT,
            check=True,
            env=dict(os.environ, SOURCE_DATE_EPOCH='1704067200'),
            stdout=output,
            stderr=output,
        )
    (wheel,) = trial.directory.glob('*.whl')
    return wheel


def deploy(trial, config, wheel):
    digest = files.digest(wheel.read_bytes())
    trial.update(wheel=str(wheel), wheel_sha256=digest)
    installer = Path(__file__).with_name('install.py')
    with (trial.directory / 'install.log').open('wb') as log, wheel.open('rb') as source:
        trial.step('install and verify candidate on macOS')
        host = json.loads(
            subprocess.check_output(
                [sys.executable, '-I', installer, 'Darwin', wheel.name, digest], stdin=source, stderr=log
            )
        )
        trial.step('install and verify candidate on Linux over SSH')
        source.seek(0)
        guest = json.loads(
            ssh(
                config,
                'python3.14',
                '-I',
                '-c',
                installer.read_text(),
                'Linux',
                wheel.name,
                digest,
                stdin=source,
                stdout=subprocess.PIPE,
                stderr=log,
            ).stdout
        )
    if (
        host['source_sha256'] != guest['source_sha256']
        or host['source_sha256'] != verify.package_identity()['source_sha256']
    ):
        raise ValueError('Checkout and installed candidate sources differ; inspect the build directory and wheel')
    trial.update(host_install=host, guest_install=guest)
    return host['python']


CREATE_STUDY = """from pathlib import Path
import json, os, sys
os.umask(0o077)
study = Path.home() / 'research' / sys.argv[1]
study.mkdir(parents=True, exist_ok=False)
for name, content in json.load(sys.stdin).items():
    path = study / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
"""


def provision(trial, config):
    project = trial.directory / 'checkout'
    shutil.copytree(FIXTURE / 'project', project, ignore=shutil.ignore_patterns('__pycache__'))
    challenge = (uuid4().hex + '\n').encode()
    (project / 'challenge.txt').write_bytes(challenge)
    trial.update(challenge_sha256=files.digest(challenge))
    for args in (
        ['init', '-q'],
        ['add', '.'],
        [
            '-c',
            'user.name=Berman CI',
            '-c',
            'user.email=ci@example.invalid',
            '-c',
            'commit.gpgsign=false',
            'commit',
            '-qm',
            'Qualification fixture',
        ],
    ):
        subprocess.run(['git', '-C', project, *args], check=True)
    study = FIXTURE / 'study'
    inputs = {str(p.relative_to(study)): p.read_text() for p in study.rglob('*') if p.is_file()}
    inputs['collection.json'] = json.dumps({'schema': 'berman.collection/1', 'id': trial.data['collection_id']})
    ssh(config, 'python3.14', '-I', '-c', CREATE_STUDY, trial.data['study'], input=json.dumps(inputs).encode())


def binding(trial, config):
    live = trial.data['worker']
    if live == 'deterministic':
        home, executable = trial.directory / 'fixture-home', FIXTURE / 'worker.py'
        harness = 'claude'
    else:
        harness = live
        executable = shutil.which(config.get('executables', {}).get(live, live))
        if not executable:
            raise ValueError(f'Find the installed {live} executable on PATH or set executables.{live} in the CI config')
        home = STATE / (live + '-home')
    if harness != 'codex':
        home.mkdir(mode=0o700, exist_ok=True)
    native = {
        'executable': str(Path(executable).resolve()),
        'home': str(home),
        'project': str(trial.directory / 'checkout'),
    }
    if harness == 'codex':
        native.update(policy='workspace-write', model='gpt-6-luna')
    elif live == 'claude':
        native['model'] = 'haiku'
    return harness, native


def codex_hooks(home, spool, directory):
    template = capture.definition(spool, home)
    proposal = directory / 'codex-hooks.json'
    files.write(proposal, template)
    hooks = home / 'hooks.json'
    try:
        home.mkdir(mode=0o700)
    except FileExistsError:
        pass
    else:
        # Only a newly created CI home receives an initial registration.
        files.write(hooks, template)
    try:
        executor.verify_hooks(files.read(hooks), template)
    except (OSError, ValueError) as error:
        raise ValueError(
            f'Review {proposal} for {hooks} and review the hooks in the actual Codex CLI: {error}'
        ) from error


def codex_preflight(native, spool, directory):
    home = Path(native['home'])
    codex_hooks(home, spool, directory)
    executable = native['executable']
    env = dict(os.environ, CODEX_HOME=str(home))
    project = native['project']
    print(
        f'In the Codex window opening now ({home}), sign in if prompted and review this candidate in /hooks. '
        'Exit Codex after review to start the fixture assignment.',
        file=sys.stderr,
        flush=True,
    )
    review = [executable, '--model', native['model'], '--sandbox', native['policy'], '-C', project]
    subprocess.run(review, env=env, cwd=project, check=True)
    status = subprocess.run([executable, 'login', 'status'], env=env, cwd=project, stdin=subprocess.DEVNULL, timeout=30)
    if status.returncode:
        raise ValueError(
            f'Codex could not confirm sign-in in {home}. Retry CI and finish sign-in in the review window.'
        )


def access(read, challenge_sha256):
    """Match the checkout edit, the unchanged passing test and the fieldworker's note."""
    challenge = read('initial-project/challenge.txt')
    value = challenge.decode().strip()
    if (
        not re.fullmatch(r'[0-9a-f]{32}', value)
        or read('final-project/challenge.txt') != challenge
        or files.digest(challenge) != challenge_sha256
    ):
        raise ValueError('The checkout challenge changed')
    for name in ('answer.py', 'check.py'):
        if read('initial-project/' + name) != (FIXTURE / 'project' / name).read_bytes():
            raise ValueError(f'Unexpected initial {name}')
    if read('final-project/check.py') != read('initial-project/check.py'):
        raise ValueError('The worker changed the fixture test')
    answer = read('final-project/answer.py')
    result = json.loads(read('final-project/test-result.json'))
    if answer == read('initial-project/answer.py') or files.digest(answer) != result['answer_sha256']:
        raise ValueError('The final source must be the edit that passed the fixture test')
    code = result['code']
    note = read('packet/notes/account.md')
    if (
        not isinstance(code, str)
        or not re.fullmatch(r'[0-9a-f]{32}', code)
        or code == value
        or not all(s.encode() in note for s in (value, code))
    ):
        raise ValueError('The note must contain the checkout value and the result code from the passing test')
    return note


def launch_fixture(trial, op, assignment):
    """Record the observed exit even when the guest loses the launch reply."""
    run = assignment['run']
    try:
        op.perform(Namespace(action='launch', run=run, one_turn=trial.data['worker'] == 'deterministic'))
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        observed = executor.sitting_receipts(op.spool, assignment['operation'])
        if not observed or observed[0]['exit'] is None:
            raise  # An unresolved sitting has no observed ending for this fixture.
        # Closeout replays the completed launch to the guest, including after a lost reply.
        trial.update(launch_command_error=str(error))
    else:
        observed = executor.sitting_receipts(op.spool, assignment['operation'])
    problem = None
    sitting = observed[0]
    code = sitting['exit'].get('returncode')
    session = sitting['session_id']
    if code != 0 or not session:
        exit_detail = f'exited {code}' if code is not None else f'has no observed exit code ({sitting["exit"]})'
        attribution = f' in session {session}' if session else ' without an attributed session'
        problem = f'{trial.data["worker"]} {exit_detail}{attribution}'
    trial.update(launch={'exit': sitting['exit'], 'session_id': session}, launch_problem=problem)
    if problem:
        print(f'Launch: {problem}. Collecting the fixture evidence.', file=sys.stderr, flush=True)


def close_fixture(trial, op, run):
    try:
        report = op.closeout(
            Namespace(
                run=run, supplement=None, missing_reason=None, reason='The qualification fixture sitting has ended.'
            )
        )
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        failure = {'status': 'failed', 'problem': str(error)}
        try:
            state = op.read('status', run)['result']
            failure.update(accepted_members=(state['accepted'] or {}).get('accepted_members'), facts=state['facts'])
        except (OSError, ValueError, subprocess.SubprocessError) as status_error:
            failure['status_problem'] = str(status_error)
        trial.update(closeout=failure)
        count = failure.get('accepted_members')
        detail = f'guest accepted {count} files; ' if count is not None else ''
        raise ValueError(f'Closeout: {detail}{error}') from error
    trial.update(closeout=report)
    if report['status'] != 'verified' or report['missing'] or report['omitted']:
        raise ValueError('Incomplete qualification collection; inspect closeout in the receipt')
    print(
        f'Closeout: guest accepted {report["accepted_members"]} files and verified the recording and edition.',
        file=sys.stderr,
        flush=True,
    )
    return report


def engage(trial, config):
    harness, native = binding(trial, config)
    spool = STATE / 'spool'
    settings = dict(
        config,
        study=trial.data['study'],
        collection_id=trial.data['collection_id'],
        spool=str(spool),
        bindings={'fixture': {harness: native}},
    )
    remote = connector.Remote(settings, controller_python=trial.data['guest_install']['python'])
    op = connector.Operator(settings, remote)
    trial.step('connect to qualification collection')
    remote.call('status', run=None)
    trial.update(candidate_verified=True, requested_model=native.get('model'))
    if harness == 'codex':
        trial.step('review the qualification hooks and confirm Codex sign-in')
        codex_preflight(native, spool, trial.directory)
    trial.step('commission fixture assignment')
    assignment = op.commission(
        Namespace(request='access', connection='fixture', recipe='access', plan=None, harness=harness)
    )['result']
    run = assignment['run']
    trial.update(run=run)
    trial.step(f'prepare {run}')
    op.perform(Namespace(action='prepare', run=run))
    trial.step(f'launch {run} with {native.get("model", "deterministic worker")}')
    launch_fixture(trial, op, assignment)
    trial.step(f'end, transfer, preserve and publish {run}')
    report = close_fixture(trial, op, run)
    trial.step('copy and verify detached evidence')
    paths = {}
    for kind in ('recording', 'edition'):
        # Relative guest paths are interpreted from the SSH login directory.
        remote_path = report[kind].removeprefix('~/')
        target = trial.directory / (kind + '.zip')
        with target.open('wb') as out:
            ssh(config, 'cat', '--', remote_path, stdout=out)
        paths[kind] = target
    record = evidence.verify_package(paths['recording'], report['recording_sha256'])
    edition = package.verify_edition(paths['edition'], report['edition_sha256'])
    for verified in (record, edition):
        if (verified['collection_id'], verified['run']) != (remote.collection, run):
            raise ValueError('Detached evidence names another engagement')
    trial.update(evidence={k: str(p) for k, p in paths.items()})
    trial.step('check launch and capture attribution')
    capture_report = op.perform(Namespace(action='qualify', run=run, supplement=None, expected_child=None))
    required = (
        'native_launch',
        'opening_and_stop',
        'offline_hook_snapshot',
        'attributed_transcript',
        'closeout_parent_observed',
        'capture_intact',
    )
    checks = {key: capture_report['checks'][key] for key in required}
    trial.update(session_id=capture_report['session_id'], capture_checks=checks)
    missing = [key for key in checks if not checks[key]]
    if missing:
        raise ValueError('Missing launch or capture evidence: ' + ', '.join(missing))
    trial.step('verify checkout access and published note')
    base = f'runs/{run}/'
    with zipfile.ZipFile(paths['recording']) as recording, zipfile.ZipFile(paths['edition']) as published:
        note = access(lambda name: recording.read(base + name), trial.data['challenge_sha256'])
        trial.update(access_verified=True)
        name = base + 'packet/notes/account.md'
        if published.read(package.PREFIX + name) != note or edition['recording_manifest_sha256'] != files.digest(
            recording.read(evidence.MANIFEST)
        ):
            raise ValueError('The edition differs from the verified recording or note')
    trial.update(publication_verified=True, note=name, status='passed')


def main(config_path, live=None):
    os.umask(0o077)
    config = files.read(Path(config_path))
    if not isinstance(config, dict) or set(config) - {'host', 'ssh_config', 'executables'}:
        raise ValueError('Use a CI config containing host, optional ssh_config and optional executables')
    connector.ssh_client(config)
    binaries = config.get('executables', {})
    if (
        not isinstance(binaries, dict)
        or set(binaries) - {'codex', 'claude'}
        or not all(isinstance(value, str) and value for value in binaries.values())
    ):
        raise ValueError('executables maps codex or claude to a native executable')
    if live and not (sys.stdin.isatty() and sys.stdout.isatty()):
        raise ValueError('Run a live qualification from an ordinary interactive terminal')
    identity = uuid4()
    directory = STATE / 'trials' / str(identity)
    directory.mkdir(parents=True)
    trial = Trial(directory)
    settings = directory / 'config.json'
    files.write(settings, config)
    print(f'Berman CI receipt: {trial.path}', file=sys.stderr, flush=True)
    trial.update(
        schema='berman.ci/1',
        status='running',
        worker=live or 'deterministic',
        host=config['host'],
        study='ci-' + identity.hex,
        collection_id=str(identity),
        candidate_verified=None,
        access_verified=None,
        publication_verified=None,
    )
    try:
        wheel = source_check(trial)
        python = deploy(trial, config, wheel)
        trial.step('create qualification checkout and guest study')
        provision(trial, config)
        subprocess.run([python, '-I', __file__, '--engagement', str(trial.directory), settings], check=True)
    except (Exception, KeyboardInterrupt) as error:
        trial = Trial(directory)
        problem = trial.data.get('problem') or str(error) or 'Interrupted'
        trial.update(status='failed', problem=problem)
        run = trial.data.get('run', 'uncommissioned')
        print(
            f'{trial.data["study"]}/{run} · {trial.data["step"]}: {problem}\n'
            f'Inspect {directory} (build.log, install.log, receipt.json) and rerun for a fresh trial.',
            file=sys.stderr,
        )
        return 1
    report = files.read(trial.path)
    print(
        f'{report["worker"]} · {report["study"]}/{report["run"]}\n'
        f'PASS  Candidate wheel on macOS and Linux over SSH\n'
        f'PASS  Checkout read, source edit, passing test and packet note\n'
        f'PASS  Detached recording and reading edition with matching note\n{trial.path}'
    )
    return 0


if __name__ == '__main__':
    if sys.argv[1:2] == ['--engagement']:
        trial = Trial(Path(sys.argv[2]))
        try:
            engage(trial, files.read(Path(sys.argv[3])))
        except (Exception, KeyboardInterrupt) as error:
            problem = str(error) or 'Interrupted'
            if trial.data.get('launch_problem'):
                problem = f'Launch: {trial.data["launch_problem"]}. {problem}'
            trial.update(status='failed', problem=problem)
            sys.exit(1)
    else:
        try:
            sys.exit(main(*sys.argv[1:]))
        except (OSError, ValueError, KeyError, TypeError) as error:
            sys.exit(f'Berman CI: {error}')
