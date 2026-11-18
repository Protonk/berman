"""Verification: each check reports one fact, and the result names what remains unestablished.

`software` checks the installed package and loads no study. `verify` checks a co-located
workspace, a directory with berman.json made by release 0.26.3 or earlier, as evidence: recorded
hashes, that its records load, whether anything in it is unsettled, its preserved recordings and
its published reading edition. It runs no harness and reads no harness configuration, so it says
nothing about hook trust, native capture or native access; the executor qualifies a native
installation from its own launches. Recordings and archive checks perform full reads and can be
slow.

A co-located workspace is read-only here. Unsettled work is reported, never settled: it has to
be closed with the release that opened it. Without --record, verify creates or changes no file,
takes no lock and runs no Git command; --record adds one new evidence folder and touches nothing
else. Fieldwork commissioned through the controller is verified in its guest study instead
(berman remote verify RUN).
"""

import importlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys

from berman import environment, files, recordings, records
from berman import schemas
from berman.files import Decision, keep, read
from berman.installation import PACKAGE, FROZEN, FROZEN_SHA256, command as module_command
from berman.workspace import identity

SKIPPABLE = ('archive', 'recordings')
EXIT = {'passed': 0, 'failed': 1, 'incomplete': 3}
RECEIPTS = {'archive': 'archive-verify.json'}
BUILDER_OUTPUT = {'stdout': 'archive-verify.stdout.txt', 'stderr': 'archive-verify.stderr.txt'}
TIMEOUT = 1800
PROBLEMS = (OSError, ValueError, KeyError, TypeError, AttributeError)
MEANING = {
    'frozen-collector': 'The installed historical collector retains its frozen bytes.',
    'installation': 'Installed package metadata and runtime resources can be read; no study is loaded.',
    'dependencies': 'Active runtime requirements, including transitive versions and Python constraints, are satisfied, '
    'and the dependency modules Berman uses import. Their versions '
    'are environment facts: nothing fingerprints them and no qualification rests on them.',
    'schemas': 'The packaged record schemas are present, well formed and named for their own record families.',
    'recorded-hashes': 'Files listed in experiments/*/checksums.json, experiments/*/experiment-checksums.sha256 and '
    'inquiry/*/MANIFEST.json keep their recorded bytes.',
    'workspace': "This co-located workspace's berman.json, catalogue and connections load with this code, and berman "
    'status reports no problem: none in the workspace, on a listed run, on a support trial, in a combined '
    'request or in a selected plan. Plan sequence conflicts are problems; a plan with steps left is not.',
    'settled': 'Nothing in this co-located workspace awaits the release that opened it: no reserved, prepared or open '
    'engagement, no declared ending whose preservation, migration, packaging or reading publication is '
    'unfinished, no pending combined request and no open support trial. This version reads such work and '
    'never settles it; a run at rest without an ending or a package stays as recorded.',
    'archive': 'Generated reading aids and the cumulative archive match their inputs. verify never rebuilds them.',
    'recordings': 'Preserved originals match their inventory; capture, migration and ending evidence agree; '
    'recording ZIPs exist and match their recorded hashes and exact inventoried contents. '
    'Missing historical authority is unestablished, never invented. No files are changed.',
}
NOT_COVERED = [
    'Developer regression tests (run separately from the source checkout).',
    'Hook trust, native capture and native access. This version operates no harness in a co-located workspace; the '
    'executor qualifies each launch from its own evidence (berman remote qualify RUN).',
    'Fieldwork commissioned through the controller. Its recording and edition are verified in the guest study '
    '(berman remote verify RUN).',
]
REBUILD = 'After intended changes, rebuild once with berman archive, then verify again'
SETTLE = (
    'Close each with the Berman release that opened it and keep that runtime installed until none remain; this '
    'version commissions and closes fieldwork only through the controller and executor (docs/usage.md)'
)


def row(status, summary, required=True, step=None, **details):
    result = {'status': status, 'required': required, 'summary': summary}
    if step:
        result['next'] = step
    result['details'] = details
    return result


def evidence_folder(root, path):
    """The path policy for kept verification evidence: a new folder under <root>/experiments/.

    Relative paths resolve against the workspace root. A path through a symlink or a preserved
    collection (in any letter case, since the volume may ignore case) is refused, and so is an
    existing folder.
    """
    root = Path(root).expanduser().resolve()
    experiments = root / 'experiments'
    folder = Path(os.path.abspath(root / Path(path).expanduser()))
    if not folder.is_relative_to(experiments) or folder == experiments:
        raise Decision(f'Evidence is kept in a new folder under {experiments}; refusing {folder}')
    if any(part.casefold() == 'collection' for part in folder.relative_to(experiments).parts):
        raise Decision(f'Evidence folders never lie inside a preserved collection: {folder}')
    if any(p.is_symlink() for p in (folder, *folder.parents)):
        raise Decision(f'Evidence folder path passes through a symlink: {folder}')
    if folder.exists():
        raise Decision(f'Evidence folder already exists; choose a new name: {folder}')
    return folder


def skipped(name):
    return row('not-run', 'Skipped with --skip', step=f'berman verify without --skip {name}')


def unfinished(exc):
    """What a timed-out child wrote before it was stopped; subprocess keeps it as bytes even in text mode."""
    output = exc.stderr
    return output.decode('utf-8', 'replace') if isinstance(output, bytes) else output


def integrity(code=PACKAGE):
    """Check the frozen implementation without loading study-specific receipts."""
    try:
        actual = files.digest((code / FROZEN).read_bytes())
    except OSError as exc:
        return row('failed', f'Frozen collector cannot be read: {exc}')
    return row(
        'passed' if actual == FROZEN_SHA256 else 'failed',
        f'Frozen collector SHA-256 {actual}; expected {FROZEN_SHA256}',
        sha256=actual,
    )


def recorded_hashes(root):
    """Recompute the hashes retained beside experiment evidence and preparation records."""
    expected = []
    try:
        for path in sorted(root.glob('experiments/*/checksums.json')):
            expected += [(path.parent / rel, value) for rel, value in read(path).items()]
        for path in sorted(root.glob('experiments/*/experiment-checksums.sha256')):
            for line in path.read_text().splitlines():
                if line.strip():
                    value, _, rel = line.partition('  ')
                    expected.append((path.parent / rel, value))
        # Top-level manifests only: inquiry/*/revisions/*/MANIFEST.json describes the files packed in
        # archive/run-007-preparation-r2.zip, not the contents of a working directory.
        for path in sorted(root.glob('inquiry/*/MANIFEST.json')):
            expected += [(path.parent / entry['path'], entry['sha256']) for entry in read(path)['files']]
    except PROBLEMS as exc:
        return row('failed', f'A recorded hash list cannot be read: {exc}')
    missing = [Path(os.path.relpath(p, root)).as_posix() for p, _ in expected if not p.is_file()]
    mismatched = [
        Path(os.path.relpath(p, root)).as_posix()
        for p, value in expected
        if p.is_file() and files.digest(p.read_bytes()) != value
    ]
    details = {'hashes': len(expected), 'mismatched': mismatched, 'missing': missing}
    if missing or mismatched:
        return row(
            'failed',
            f'{len(missing) + len(mismatched)} of {len(expected)} recorded hashes do not hold',
            step='Restore the recorded bytes from Git; preserved evidence is never edited',
            **details,
        )
    return row('passed', f'{len(expected)} recorded hashes match', **details)


def workspace(store, problem):
    """The workspace check, and the status it read (None when the workspace or its status cannot be read)."""
    if store is None:
        return row(
            'failed',
            f'The workspace does not load with this code: {problem}',
            step='Inspect berman.json and the file it names; this version changes neither',
        ), None
    try:
        status = records.status(store)
    except PROBLEMS as exc:
        return row('failed', f'berman status fails with this code: {exc}'), None
    connections = status['connections']
    # A problem run with no registered connection (a damaged support marker, or an unattributed run) is in
    # no connection's own list.
    problems = (
        status['workspace_problems']
        + [f'{name}: {p}' for name, c in connections.items() for p in c['problems']]
        + [f'{name}: {c["plan_error"]}' for name, c in connections.items() if c.get('plan_error')]
        + [
            f'{"no connection" if r["connection"] is None else r["connection"] + " (not registered)"}: {p}'
            for r in status['runs']
            if r['connection'] not in connections
            for p in r['problems']
        ]
        + [f'support trial: {p}' for t in status['support_trials'] for p in t['problems']]
    )
    details = {
        'outstanding_runs': [r['run'] for r in status['runs']],
        'plans': {
            name: {key: c[key] for key in ('plan', 'next_step', 'next_recipe')} for name, c in connections.items()
        },
        'problems': problems,
    }
    if problems:
        return row(
            'failed', f'berman status reports {len(problems)} problem(s)', step='berman status', **details
        ), status
    return row(
        'passed', f'The workspace loads with this code; {len(connections)} connection(s), no problems', **details
    ), status


def settled(status, loaded):
    """Whether anything awaits the release that opened it. Reported, and never settled here."""
    if status is None:
        return row('not-run', 'Depends on workspace')
    waiting = status['unsettled']
    if waiting:
        return row(
            'not-established',
            f'{len(waiting)} unsettled; this version reads a co-located workspace and closes nothing in it',
            step=SETTLE,
            unsettled=waiting,
        )
    if loaded['status'] != 'passed':
        return row('not-established', 'Unknown while the workspace check reports problems', unsettled=[])
    return row(
        'passed',
        'No engagement, preparation, request or support trial awaits the release that opened it',
        unsettled=[],
    )


def archive(root, code, run):
    """Verify a published edition against its retained member manifest; current inputs are a separate freshness check.

    Returns the check and its receipts by file name: the JSON summary when it passes, otherwise
    whatever output the builder produced.
    """
    argv = module_command('--root', root, 'archive', '--verify')
    try:
        done = run(argv, cwd=code, capture_output=True, encoding='utf-8', errors='replace', timeout=TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        return (
            row('failed', f'The archive builder did not finish: {exc}', argv=argv),
            {BUILDER_OUTPUT['stderr']: unfinished(exc)},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return row('failed', f'The archive builder could not run: {exc}', argv=argv), {}
    output = {BUILDER_OUTPUT['stdout']: done.stdout, BUILDER_OUTPUT['stderr']: done.stderr}
    lines = done.stderr.strip().splitlines()
    if done.returncode != 0:
        return row(
            'failed',
            lines[-1] if lines else f'The archive builder exited {done.returncode}',
            step=REBUILD,
            argv=argv,
            exit=done.returncode,
        ), output
    try:
        built = json.loads(done.stdout)
    except ValueError:
        return row('failed', 'The archive builder printed no JSON summary', argv=argv, exit=0), output
    return (
        row(
            'passed',
            f'Published edition {built.get("archive_path")} matches its retained member manifest '
            f'({built.get("zip_members")} members)',
            argv=argv,
            exit=0,
            builder=built,
        ),
        {RECEIPTS['archive']: done.stdout},
    )


def verdict(checks):
    if any(c['status'] == 'failed' for c in checks.values()):
        return 'failed'
    if any(c['required'] and c['status'] != 'passed' for c in checks.values()):
        return 'incomplete'
    return 'passed'


def recording_check(store):
    if store is None:
        return row('not-run', 'Depends on workspace')
    try:
        runs = recordings.audit(store)
    except recordings.ERRORS as exc:
        return row('failed', f'Preserved recording evidence cannot be inspected: {exc}')
    problems = [f'{run["run"]}: {problem}' for run in runs for problem in run['problems']]
    unknown = [f'{run["run"]}: {problem}' for run in runs for problem in run['unknown']]
    unfinished = any(run.get('unfinished') for run in runs)
    status = 'failed' if problems else 'not-established' if unfinished else 'passed'
    return row(
        status,
        f'{len(runs)} preserved run(s); {len(problems)} problem(s), {len(unknown)} unestablished fact(s)',
        runs=runs,
        problems=problems,
        missing=unknown,
        coverage={'unknown': unknown},
        step='Inspect the named evidence; retain originals and record any repair separately'
        if problems or unknown
        else None,
    )


def verify(root, skip=(), record=None, run=subprocess.run, code=PACKAGE):
    """Run every check not skipped and return the result; a failed check is reported, never raised.

    root is the co-located workspace (as --root); code is the installed package resource directory.
    Raises Decision only for a refused record folder or an unknown skip.
    """
    if set(skip) - set(SKIPPABLE):
        raise Decision('Only these checks can be skipped: ' + ', '.join(SKIPPABLE))
    root, code = Path(root).expanduser().resolve(), Path(code).resolve()
    folder = None if record is None else evidence_folder(root, record)
    started = files.now()
    try:
        store, problem = records.Store(root), None
    except PROBLEMS as exc:
        store, problem = None, str(exc)
    checks, receipts = {}, {}
    checks['frozen-collector'] = integrity(code)
    checks['recorded-hashes'] = recorded_hashes(root)
    checks['workspace'], status = workspace(store, problem)
    checks['settled'] = settled(status, checks['workspace'])
    checks['recordings'] = skipped('recordings') if 'recordings' in skip else recording_check(store)
    if 'archive' in skip:
        checks['archive'] = skipped('archive')
    else:
        checks['archive'], output = archive(root, code, run)
        receipts.update(output)
    for name, check in checks.items():
        check['meaning'] = MEANING[name]
    states = {
        state: [name for name, c in checks.items() if c['status'] == state]
        for state in ('failed', 'not-run', 'not-established')
    }
    result = {
        'schema': 'berman.verification/1',
        'verdict': verdict(checks),
        'checked_at_utc': started,
        'root': str(root),
        'code': str(code),
        'python': sys.executable,
        'python_version': 'Python ' + platform.python_version(),
        'checks': checks,
        'failed': states['failed'],
        'not_run': states['not-run'],
        'not_established': states['not-established'],
        'not_covered': list(NOT_COVERED),
        'record': None,
        'meaning': 'Each check is a separate fact; passed requires every required check. The co-located workspace '
        'was read: nothing was rebuilt, closed or launched.',
    }
    if folder is not None:
        kept = {name: data for name, data in receipts.items() if data}
        result['record'] = {'directory': str(folder), 'files': ['verification.json', *kept]}
        for name, data in kept.items():
            keep(folder / name, data.encode())
        keep(folder / 'verification.json', files.json_bytes(result))
    return result


def dependencies():
    """Check requirements and explicit API imports, even when installed files have vanished."""
    try:
        installed, broken = environment.inspect()
    except Exception as exc:
        installed, broken = {}, [f'{type(exc).__name__}: {exc}']
    missing = sorted(name for name, version in installed.items() if version is None)
    # Diagnostic imports only; state computation never uses these libraries directly. Do not
    # discover names from installed files: deleting a package must not erase the check itself.
    required = {
        'jsonschema': {'jsonschema': ('Draft202012Validator',)},
        'rich': {
            'rich': (),
            'rich.console': ('Console',),
            'rich.padding': ('Padding',),
            'rich.table': ('Table',),
            'rich.text': ('Text',),
        },
    }
    for name, modules in required.items():
        try:
            for module, attributes in modules.items():
                loaded = importlib.import_module(module)
                for attribute in attributes:
                    if not callable(getattr(loaded, attribute)):
                        raise TypeError(f'{module}.{attribute} is not callable')
        except Exception as exc:  # A damaged binary wheel fails here in its own way.
            broken.append(f'{name}: {type(exc).__name__}: {exc}')
    details = {'installed': installed, 'missing': missing, 'problems': broken}
    if missing or broken:
        return row(
            'failed',
            f'{len(missing) + len(broken)} runtime dependency problem(s)',
            step='Reinstall the package with its dependencies',
            **details,
        )
    return row(
        'passed', ', '.join(f'{name} {version}' for name, version in installed.items()) or 'none declared', **details
    )


def packaged_schemas():
    try:
        digests = schemas.check()
    except PROBLEMS + (ImportError,) as exc:
        return row('failed', str(exc), step='Reinstall the package; schema files are part of it')
    return row('passed', f'{len(digests)} record schemas are packaged and well formed', sha256=digests)


def software(skip=(), code=PACKAGE):
    """Verify installed package resources; never load a workspace or harness home."""
    if skip:
        raise Decision('Software verification has no skippable checks')
    try:
        installed = identity()
        checks = {'installation': row('passed', f'Berman {installed["version"]} is installed', software=installed)}
    except Exception as exc:  # Third-party metadata and parser failures are installation evidence.
        checks = {'installation': row('failed', f'Installed package cannot be read: {exc}')}
    checks['dependencies'] = dependencies()
    checks['frozen-collector'] = integrity(code)
    checks['schemas'] = packaged_schemas()
    for name, check in checks.items():
        check['meaning'] = MEANING[name]
    return {
        'schema': 'berman.verification/1',
        'scope': 'software',
        'verdict': verdict(checks),
        'checked_at_utc': files.now(),
        'root': None,
        'code': str(code),
        'checks': checks,
        'failed': [n for n, c in checks.items() if c['status'] == 'failed'],
        'not_run': [n for n, c in checks.items() if c['status'] == 'not-run'],
        'not_established': [],
        'not_covered': [
            'Developer regression tests (run separately from the source checkout).',
            'Workspace records, preserved evidence and reading editions.',
            'Native harness execution, hook trust and native capture.',
        ],
        'record': None,
        'meaning': 'Software checks only. No workspace or native installation was verified.',
    }
