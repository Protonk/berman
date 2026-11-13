"""Maintenance verification: each check reports one fact, and the result names what remains unestablished.

Passing tests establish neither hook trust nor native capture, recorded trust is not native capture,
and none of them is native sandbox access. By default verify runs only checks that are cheap,
non-interactive and read-only. For the interactive steps (the actual-CLI /hooks review with
record-trust, and a native support trial) it reports their recorded results and never performs them.
Without --record or --native-access it creates or changes no file, takes no lock and runs no Git
command. --native-access runs the real Codex binary against disposable Berman, packet and Codex roots
and the real checkouts, as a preparation does; nothing outside its evidence folder is kept.
"""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

import berman_capture
import berman_files as files
from berman_files import Decision, digest_json, keep, read, write
from berman_harness import Codex
from berman_recipes import git_state
from berman_records import ROOT, Store
from berman_workflow import Workflow

SKIPPABLE = ('tests', 'launcher-tests', 'archive')
EXIT = {'passed': 0, 'failed': 1, 'incomplete': 3}
SUITES = {'tests': 'tools', 'launcher-tests': 'tools/development_launchers'}
RECEIPTS = {'tests': 'tests.txt', 'launcher-tests': 'development-launcher-tests.txt', 'archive': 'archive-verify.json'}
BUILDER_OUTPUT = {'stdout': 'archive-verify.stdout.txt', 'stderr': 'archive-verify.stderr.txt'}
TIMEOUT = 1800
PROBLEMS = (OSError, ValueError, KeyError, TypeError, AttributeError)
RAN = re.compile(r'^Ran (\d+) tests? in ([\d.]+)s$', re.M)
OUTCOME = re.compile(r'^(OK|FAILED|NO TESTS RAN)(?: \((.*)\))?$', re.M)
FAILING = re.compile(r'^((?:FAIL|ERROR): .+)$', re.M)
COLOUR = re.compile(r'\x1b\[[0-9;]*m')
MEANING = {
    'tests': 'The main suite passes in disposable fixtures; it establishes neither hook trust, native capture nor native access.',
    'launcher-tests': "The development launchers' offline tests pass; the installed launchers' --check is separate.",
    'frozen-collector': 'tools/capture_session.py keeps the bytes its historical basis records, and the retained legacy '
                        'definition is unchanged.',
    'recorded-hashes': 'Files listed in experiments/*/checksums.json, experiments/*/experiment-checksums.sha256 and '
                       'inquiry/*/MANIFEST.json keep their recorded bytes.',
    'workspace': "This workspace's berman.json, catalogue, connections and plans load with this code, and berman status "
                 'reports no problem: none in the workspace, on an outstanding run (one with no connection blocks every '
                 "connection) or on a support trial. Each connection's next step is listed, not judged, even when its plan "
                 'refuses one.',
    'hook-trust': 'The installed hooks match a definition reviewed in the actual CLI for these implementation bytes '
                  '(Codex), or the per-launch template, in-session bytes, helpers and settings policy match the '
                  'qualified support trial (Claude, which needs no review). verify reads that record; it never '
                  'performs the review or the trial.',
    'native-capture': 'A native session captured its required events with these implementation bytes. Trust alone '
                      'does not show this.',
    'archive': 'Generated reading aids and the cumulative archive match their inputs. verify never rebuilds them.',
    'native-access': "The native Codex sandbox gives each connection's assignment its intended access, checked in "
                     'disposable roots.',
}
NOT_COVERED = [
    "The installed development launchers' --check (tools/development_launchers/README.md).",
    'The actual-CLI /hooks review and berman capture record-trust for the Codex adapters; verify reports only their '
    'recorded result. Claude needs no review (LIMITS.md).',
    'A native support trial; verify reports only a recorded successful capture.',
]
TRUST_NEXT = ("With the researcher's approval, review /hooks in the actual Codex CLI, then record it with "
              'berman capture record-trust --evidence FILE --attest')
CLAUDE_TRUST_NEXT = ("With the researcher's approval, a new berman-claude support trial qualifies the current template, "
                     'in-session bytes, helpers and settings policy; no CLI /hooks review is required (LIMITS.md)')
CLAUDE_TRIAL_NEXT = ("A native support trial for berman-claude, with the researcher's approval: "
                     'berman support-trial prepare --evidence experiments/NAME --adapter berman-claude; '
                     'berman support-trial launch --evidence experiments/NAME --model haiku --max-budget-usd 2 '
                     '(bounded model usage, non-interactive); berman support-trial collect --evidence experiments/NAME; '
                     'berman capture record-capture --trial experiments/NAME; '
                     'berman support-trial cleanup --evidence experiments/NAME --reason TEXT')
REBUILD = 'After intended changes, rebuild once with python3 -I -B tools/build_archive.py, then verify again'
NATIVE_NEXT = 'berman verify --native-access'
TRIAL_NEXT = ("A native support trial for {name}, with the researcher's approval: "
              'berman support-trial prepare --evidence experiments/NAME --adapter {name}; '
              'berman support-trial launch --evidence experiments/NAME (interactive); '
              'berman support-trial collect --evidence experiments/NAME; '
              'if the basis predates these bytes, berman capture record-trust --evidence '
              '{root}/experiments/NAME/native.terminal --attest; '
              'berman capture record-capture --trial experiments/NAME; '
              'berman support-trial cleanup --evidence experiments/NAME --reason TEXT')
FIXTURE = 'native-check-fixture'
ACCESS_ONLY = 'Native access only; hook trust and native capture are reported separately by berman verify.'
ACCESS_EVIDENCE = ('readiness.json', 'profile.config.toml', 'checks', 'ASSIGNMENT.md', 'INSTRUCTIONS.md')
ACCESS_SCOPE = ('Disposable Berman, packet and Codex configuration roots (only config.toml and hooks.json copied); real '
                "checkouts with their declared project checks; each connection prepared by berman new at its plan's first "
                'step, with a commitment the real workspace has fulfilled left out of its plan copy. No model request, '
                'session registration or live run.')
ACCESS_NOT_ESTABLISHED = [
    'Hook trust and native capture; berman verify reports them separately as hook-trust:* and native-capture:*.',
    'Access with the real packet root, Codex home and profiles; every real berman new and launch rechecks it.',
    "Each plan's next step; a recipe changes the assignment text, not the access profile.",
    'A model request, Codex folder trust or a live session.',
]
FIXTURE_RELATIONSHIP = 'Native access fixture only; prepared in a disposable workspace and never launched.'
LABELS = {'passed': 'passed', 'failed': 'FAILED', 'not-established': 'not established', 'not-run': 'not run'}
LISTED = {'failing': '', 'problems': '', 'mismatched': 'mismatched: ', 'missing': 'missing: ',
          'changed_implementation': 'changed: '}


def row(status, summary, required=True, step=None, **details):
    result = {'status': status, 'required': required, 'summary': summary}
    if step:
        result['next'] = step
    result['details'] = details
    return result


def evidence_folder(root, path, resume=None):
    """The single path policy for kept maintenance evidence: a new folder under <root>/experiments/.

    Relative paths resolve against the Berman root. A path through a symlink or a preserved
    collection (in any letter case, since the volume may ignore case) is refused, and an existing folder
    unless resume names a file in it marking it resumable.
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
        if resume and (folder / resume).is_file() and not (folder / resume).is_symlink():
            return folder
        raise Decision(f'Evidence folder already exists; choose a new name: {folder}')
    return folder


def skipped(name):
    return row('not-run', 'Skipped with --skip', step=f'berman verify without --skip {name}')


def version(python, run):
    try:
        done = run([python, '--version'], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    return (done.stdout or done.stderr).strip() or None


def unfinished(exc):
    """What a timed-out child wrote before it was stopped; subprocess keeps it as bytes even in text mode."""
    output = exc.stderr
    return output.decode('utf-8', 'replace') if isinstance(output, bytes) else output


def suite(python, code, start, run):
    """One unittest suite in a child process; it passes only on exit 0, an OK summary and at least one test.

    The child is asked for uncoloured output (NO_COLOR outranks FORCE_COLOR) so its summary parses and
    its receipt reads plainly; any colour that remains is stripped before parsing.
    """
    argv = [python, '-I', '-B', '-m', 'unittest', 'discover', '-s', start, '-p', 'test_*.py']
    env = {k: v for k, v in os.environ.items() if k != 'FORCE_COLOR'} | {'BERMAN_VERIFYING': '1', 'NO_COLOR': '1'}
    try:
        done = run(argv, cwd=code, env=env, capture_output=True, encoding='utf-8', errors='replace', timeout=TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        return row('failed', f'The suite did not finish: {exc}', argv=argv), unfinished(exc)
    except (OSError, subprocess.SubprocessError) as exc:
        return row('failed', f'The suite could not run: {exc}', argv=argv), None
    text = COLOUR.sub('', done.stderr)
    ran, outcome = RAN.findall(text), OUTCOME.findall(text)
    counts = {k.strip().replace(' ', '_'): int(v) for k, v in re.findall(r'([a-z ]+)=(\d+)', outcome[-1][1])} if outcome else {}
    details = {'argv': argv, 'exit': done.returncode, 'ran': int(ran[-1][0]) if ran else None,
               'seconds': float(ran[-1][1]) if ran else None, 'outcome': outcome[-1][0] if outcome else None,
               'failures': counts.get('failures', 0), 'errors': counts.get('errors', 0),
               'unexpected_successes': counts.get('unexpected_successes', 0),
               'skipped': counts.get('skipped', 0), 'failing': FAILING.findall(text)}
    if done.returncode == 0 and details['outcome'] == 'OK' and details['ran']:
        return row('passed', f'{details["ran"]} tests passed in {details["seconds"]}s', **details), done.stderr
    if details['outcome'] == 'FAILED':
        summary = f'{details["ran"]} tests ran: {details["failures"]} failures, {details["errors"]} errors'
        if details['unexpected_successes']:
            summary += f', {details["unexpected_successes"]} unexpected successes'
    elif details['outcome'] == 'NO TESTS RAN' or details['ran'] == 0:
        summary = 'No tests ran'
    else:
        summary = f'The suite exited {done.returncode} without a passing unittest summary'
    return row('failed', summary, **details), done.stderr


def integrity(code):
    """The frozen collector's bytes and the retained legacy definition, independently of the suite."""
    if not (code / 'hooks/verification/legacy-codex.json').is_file():
        expected = 'f87241a6b9506590fffa4e2a964f37bb7f7c719e508f4024661b45c57f983a99'
        try:
            actual = files.digest((code / 'tools/capture_session.py').read_bytes())
        except OSError as exc:
            return row('failed', f'Frozen collector cannot be read: {exc}')
        return row('passed' if actual == expected else 'failed',
                   f'Frozen collector SHA-256 {actual}; expected {expected}', sha256=actual)
    try:
        basis = read(code / 'hooks/verification/legacy-codex.json')
        expected = basis['implementation_sha256']['tools/capture_session.py']
        actual = files.digest((code / 'tools/capture_session.py').read_bytes())
        definition = digest_json(read(code / basis['definition']))
    except PROBLEMS as exc:
        return row('failed', f'The frozen-collector basis cannot be checked: {exc}')
    details = {'sha256': actual, 'recorded_by': 'hooks/verification/legacy-codex.json', 'definition': basis['definition']}
    problems = []
    if actual != expected:
        problems.append(f'tools/capture_session.py is {actual}, frozen as {expected}')
    if definition != basis['definition_sha256']:
        problems.append(f'{basis["definition"]} digest is {definition}, recorded as {basis["definition_sha256"]}')
    if problems:
        return row('failed', '; '.join(problems), step='Restore the recorded bytes; tools/capture_session.py is never edited',
                   **details)
    return row('passed', f'tools/capture_session.py matches frozen {expected[:12]}; historical definition retained', **details)


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
    mismatched = [Path(os.path.relpath(p, root)).as_posix() for p, value in expected
                  if p.is_file() and files.digest(p.read_bytes()) != value]
    details = {'hashes': len(expected), 'mismatched': mismatched, 'missing': missing}
    if missing or mismatched:
        return row('failed', f'{len(missing) + len(mismatched)} of {len(expected)} recorded hashes do not hold',
                   step='Restore the recorded bytes from Git; preserved evidence is never edited', **details)
    return row('passed', f'{len(expected)} recorded hashes match', **details)


def workspace(store, problem):
    if store is None:
        return row('failed', f'The workspace does not load with this code: {problem}',
                   step='Repair berman.json or the file it names')
    try:
        status = Workflow(store).status()
    except PROBLEMS as exc:
        return row('failed', f'berman status fails with this code: {exc}')
    connections = status['connections']
    # A problem run with no connection (a damaged support marker, or an unattributed run) blocks berman new
    # for every connection, though no connection's blocked_by lists it.
    problems = (status['workspace_problems']
                + [f'{name}: {p}' for name, c in connections.items() for p in c['blocked_by']]
                + [f'{"every connection" if r["connection"] is None else r["connection"] + " (not registered)"}: {p}'
                   for r in status['runs'] if r['connection'] not in connections for p in r['problems']]
                + [f'support trial: {p}' for t in status['support_trials'] for p in t['problems']])
    details = {'outstanding_runs': [r['run'] for r in status['runs']],
               'next_recipe': {name: c['next_recipe'] for name, c in connections.items()},
               'operations_in_progress': status['operations_in_progress'], 'problems': problems}
    if problems:
        return row('failed', f'berman status reports {len(problems)} problem(s)', step='berman status', **details)
    return row('passed', f'The workspace loads with this code; {len(connections)} connection(s), no problems',
               **details)


def adapters(store, root, code):
    """Hook trust and native capture per adapter, required only for adapters a configured connection uses.

    Readiness compares the installed hooks and implementation bytes with this code, so for another
    workspace both facts are reported as not run rather than as missing.
    """
    checks = {}
    for name, adapter in berman_capture.ADAPTERS.items():
        trust, native = f'hook-trust:{name}', f'native-capture:{name}'
        if store is None:
            checks[trust], checks[native] = row('not-run', 'Depends on workspace'), row('not-run', 'Depends on workspace')
            continue
        used = sorted(c.id for c in store.connections.values() if c.capture == name)
        details = {'basis': adapter['basis'], 'used_by': used}
        claude = name == 'berman-claude'
        trial = CLAUDE_TRIAL_NEXT if claude else TRIAL_NEXT.format(name=name, root=root)
        try:
            ready = berman_capture.readiness(store, name)
        except PROBLEMS as exc:
            try:
                changed = berman_capture.changed_implementation(store, name)
            except PROBLEMS:
                changed = None
            checks[trust] = row('not-established', str(exc), bool(used), CLAUDE_TRUST_NEXT if claude else TRUST_NEXT,
                                changed_implementation=changed, **details)
            checks[native] = row('not-established', f'Depends on {trust}', bool(used), trial, **details)
            continue
        warnings = list(ready.get('warnings', []))
        summary = ('Per-launch hook template, in-session bytes, helpers and settings policy match the qualified '
                   'support trial' if claude else
                   'Installed hooks match the definition reviewed in the actual CLI for these implementation bytes')
        if warnings:
            summary += '; warning: ' + ' '.join(warnings)
        checks[trust] = row('passed', summary, bool(used), changed_implementation=[], warnings=warnings, **details)
        evidence = berman_capture.native_capture(store, name)
        if evidence:
            checks[native] = row('passed', f'Native {", ".join(evidence["events"])} captured by these implementation bytes '
                                           f'({evidence["run"] or evidence["path"]})', bool(used), **details, **evidence)
        else:
            checks[native] = row('not-established', 'Trust recorded; no native capture demonstrated for these '
                                                    'implementation bytes', bool(used), trial, **details)
    return checks


def archive(python, root, code, run):
    """The builder's own --verify, which compares generated files and the archive and writes nothing.

    Returns the check and its receipts by file name: the JSON summary when it passes, otherwise
    whatever output the builder produced.
    """
    argv = [python, '-I', '-B', 'tools/build_archive.py', '--root', str(root), '--verify']
    try:
        done = run(argv, cwd=code, capture_output=True, encoding='utf-8', errors='replace', timeout=TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        return (row('failed', f'The archive builder did not finish: {exc}', argv=argv),
                {BUILDER_OUTPUT['stderr']: unfinished(exc)})
    except (OSError, subprocess.SubprocessError) as exc:
        return row('failed', f'The archive builder could not run: {exc}', argv=argv), {}
    output = {BUILDER_OUTPUT['stdout']: done.stdout, BUILDER_OUTPUT['stderr']: done.stderr}
    lines = done.stderr.strip().splitlines()
    if done.returncode != 0:
        return row('failed', lines[-1] if lines else f'The archive builder exited {done.returncode}', step=REBUILD,
                   argv=argv, exit=done.returncode), output
    try:
        built = json.loads(done.stdout)
    except ValueError:
        return row('failed', 'The archive builder printed no JSON summary', argv=argv, exit=0), output
    return (row('passed', f'Generated files and {built.get("archive_path")} match their inputs '
                          f'({built.get("zip_members")} members)', argv=argv, exit=0, builder=built),
            {RECEIPTS['archive']: done.stdout})


class Disposable(Codex):
    """The real harness in a disposable workspace; only hooks() differs, and it claims nothing.

    Access is then checked even while hook trust is not renewed; berman verify reports trust and
    native capture as their own facts.
    """

    def hooks(self, adapter):
        return {'adapter': adapter, 'established': False, 'meaning': ACCESS_ONLY}


def checkout_state(path):
    try:
        return git_state(path)
    except (OSError, subprocess.SubprocessError) as exc:
        return {'error': f'{type(exc).__name__}: {exc}'}


def fixture_connection(root, base, plans):
    """A disposable Git project on berman-codex's notes layout, related to every selected commitment."""
    project = base / FIXTURE
    folder, recipe, plan = (root / kind / FIXTURE for kind in ('connections', 'recipes', 'plans'))
    taken = [str(p.relative_to(root)) for p in (folder, recipe, plan) if p.exists()]
    if taken:
        raise Decision(f'The native access fixture would replace researcher inputs: {", ".join(taken)}')
    (project / 'notes').mkdir(parents=True)
    (project / 'GUIDE.md').write_text('How the disposable native-check project works.\n')
    (project / 'notes/plan.txt').write_text('A plan kept in the disposable project.\n')
    for argv in (['init', '-q', '-b', 'main'], ['add', '.'],
                 ['-c', 'user.name=Berman native check', '-c', 'user.email=check@invalid', '-c', 'commit.gpgsign=false',
                  '-c', 'core.hooksPath=/dev/null', 'commit', '-qm', 'Native access fixture']):
        subprocess.run(['git', '-C', str(project), *argv], check=True, capture_output=True)
    write(folder / 'connection.json', {
        'schema': 'berman.connection/1', 'id': FIXTURE, 'name': 'Native check fixture', 'version': 1,
        'description': 'A disposable project that exercises the notes layout natively; never launched.',
        'documents': ['GUIDE.md', 'notes/plan.txt', 'HANDOFF.md'], 'commission': 'COMMISSION.md',
        'revision': 'Native access fixture; preparation-time HEAD {head}', 'checks': [], 'readers': []})
    (folder / 'COMMISSION.md').write_text('Help maintain the disposable native-check project.\n')
    write(recipe / 'recipe.json', {
        'schema': 'berman.recipe/2', 'id': FIXTURE, 'connections': [FIXTURE], 'coverage': ['notes'],
        'recording_protocol': 'work-accounts/1', 'instructions': 'INSTRUCTIONS.md', 'research_commission': 'COMMISSION.md',
        'source': 'berman verify --native-access', 'meaning': 'Native access fixture; never launched.'})
    (recipe / 'INSTRUCTIONS.md').write_text('# Native check fixture\n\nWrite accounts of the work in `{{notes_directory}}`.\n')
    (recipe / 'COMMISSION.md').write_text('Also keep accounts of the work as the instructions describe.\n')
    write(plan / 'plan.json', {
        'schema': 'berman.research-plan/1', 'id': FIXTURE, 'connection': FIXTURE,
        'steps': [{'id': 'native-check', 'recipe': FIXTURE}],
        'relationships': [{'plan': name, 'relationship': FIXTURE_RELATIONSHIP} for name in plans]})
    return {'definition': f'connections/{FIXTURE}/connection.json', 'checkout': str(project),
            'plan': f'plans/{FIXTURE}/plan.json', 'capture': 'berman-codex'}


def fulfilled_commitments(store, names):
    """Selected plans whose commitment the real workspace has fulfilled, and why that is unknown if it is."""
    try:
        rows = store.discover()
    except PROBLEMS as exc:
        return [], f'{type(exc).__name__}: {exc}'
    plans = [store.connections[n].plan for n in names]
    return sorted(p.id for p in plans if p.commitment and p.fulfilled(rows)), None


def disposable_workspace(store, base, names, synthetic, fulfilled=()):
    """Copies of researcher inputs and Codex configuration only.

    No verification evidence, run, catalogue entry, credential, session or registry is copied, so
    nothing here can stand for the real workspace's history or its capture readiness. Having no runs,
    the disposable workspace would count every commitment unfulfilled, so a plan named in fulfilled is
    copied without its commitment: preparation then asks for the relationships real preparation asks for.
    """
    root, home = base / 'berman', base / 'codex'
    home.mkdir()
    shutil.copytree(store.root / 'recipes', root / 'recipes')
    entries = {}
    for name in names:
        conn = store.connections[name]
        shutil.copytree(conn.folder, root / conn.folder.relative_to(store.root), dirs_exist_ok=True)
        plan = conn.plan.path.relative_to(store.root) if conn.plan.path else Path('plans', conn.plan.id, 'plan.json')
        if conn.plan.id in fulfilled:
            write(root / plan, {k: v for k, v in conn.plan.data.items() if k != 'commitment'})
        elif conn.plan.path is None:  # A legacy configuration synthesizes its plan in place.
            write(root / plan, conn.plan.data)
        else:
            (root / plan).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(conn.plan.path, root / plan)
        entries[name] = {'definition': conn.definition_path.relative_to(store.root).as_posix(),
                         'checkout': str(conn.checkout), 'plan': plan.as_posix(), 'capture': conn.capture}
        if 'documents' in conn.entry:
            entries[name]['documents'] = conn.entry['documents']
    if synthetic:
        entries[FIXTURE] = fixture_connection(root, base, [p.id for p in (store.connections[n].plan for n in names)
                                                           if p.commitment and p.id not in fulfilled])
    for name in ('config.toml', 'hooks.json'):
        if (store.home / name).is_file():
            shutil.copyfile(store.home / name, home / name)
    write(root / 'source-layout.json', {'runs': []})
    write(root / 'connections/catalogue.json', {'schema': 'berman.run-catalogue/1', 'support_collections': [], 'runs': []})
    write(root / 'berman.json', {
        'schema': 'berman.workspace/1', 'packet_root': str(base / 'fieldwork'), 'codex_home': str(home),
        'python': str(store.python), 'codex': str(store.codex),
        'default_connection': store.default_connection if store.default_connection in names else None,
        'legacy_connection': store.legacy_connection if store.legacy_connection in names else None,
        'catalogue': 'connections/catalogue.json', 'connections': entries})
    return root


def retain(fixture, run, target):
    """Copy one disposable preparation's access evidence; returns {disposable path: kept path}.

    Nothing else leaves the disposable roots.
    """
    source = fixture.folder(run) / 'preparation' if run else None
    copied = {}
    for name in ACCESS_EVIDENCE if source else ():
        if (source / name).is_dir():
            shutil.copytree(source / name, target / name)
        elif (source / name).is_file():
            target.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / name, target / name)
        else:
            continue
        copied[str(source / name)] = str(target / name)
    return copied


def native_access(store, folder, connections=None, synthetic=True, harness=None):
    """Prepare each connection in disposable roots and run the real native access check; returns the report.

    connections defaults to every configured connection with a research plan; synthetic adds the
    native-check-fixture connection. harness builds the harness for the disposable store (default
    Disposable). The report and each connection's evidence go into folder, which the caller chooses
    (no path policy here). A connection whose preparation or check fails is reported, not raised;
    only a setup refusal raises.
    """
    folder = Path(folder).resolve()  # A temporary folder may lie beneath a symlink such as /var.
    if folder.exists() and any(folder.iterdir()):
        raise Decision(f'Native access evidence goes into an empty folder: {folder}')
    if connections is None:
        names = [c.id for c in store.connections.values() if c.plan]
        unplanned = [c.id for c in store.connections.values() if not c.plan]
    else:
        names, unplanned = list(dict.fromkeys(connections)), []
        unknown = [n for n in names if n not in store.connections]
        if unknown:
            raise Decision(f'Unknown connection {", ".join(unknown)}; registered connections: '
                           + ', '.join(sorted(store.connections)))
        if any(store.connections[n].plan is None for n in names):
            raise Decision('Native access is checked through a preparation; choose connections that have a research plan')
    if synthetic and FIXTURE in store.connections:
        raise Decision(f'A configured connection is named {FIXTURE}; check it with synthetic=False')
    claude = [n for n in names if store.connections[n].capture == 'berman-claude']
    if connections is not None and claude:
        raise Decision('Claude native access uses an explicit bounded berman-claude support trial, not the Codex sandbox probe')
    names = [n for n in names if n not in claude]
    fulfilled, unknown = fulfilled_commitments(store, names)
    report = {'schema': 'berman.native-access-check/1', 'checked_at_utc': files.now(), 'scope': ACCESS_SCOPE,
              'codex': str(store.codex), 'codex_version': None, 'python': str(store.python),
              'harness_sha256': {rel: files.digest((ROOT / rel).read_bytes())
                                 for rel in ('tools/berman_harness.py', 'tools/berman_probe.py')},
              'fulfilled_commitments': fulfilled,
              'connections': {}, 'access_passed': False, 'disposable_roots_removed': False,
              'not_established': ACCESS_NOT_ESTABLISHED + [f'{n}: Claude access is qualified by its separate bounded '
                                                           'support trial, not this Codex probe' for n in claude]
                                 + [f'{n}: no research plan assigns fieldwork, so no preparation '
                                                           'checked its access' for n in unplanned]
                                 + ([f'Which commitments the real workspace has fulfilled ({unknown}); every commitment '
                                     'stayed in the disposable plan copies'] if unknown else [])}
    results = report['connections']
    with tempfile.TemporaryDirectory(prefix='berman-native-roots-') as temporary:
        base = Path(temporary).resolve()
        fixture = Store(disposable_workspace(store, base, names, synthetic, fulfilled))
        checker = (harness or Disposable)(fixture)
        try:
            report['codex_version'] = checker.invoke([str(fixture.codex), '--version'], base, check=True,
                                                     timeout=20).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
        checked = names + ([FIXTURE] if synthetic else [])
        before = {name: checkout_state(fixture.connections[name].checkout) for name in checked}
        for name in checked:
            conn = fixture.connections[name]
            entry = {'source': 'synthetic' if name == FIXTURE else 'real', 'checkout': str(conn.checkout),
                     'capture_adapter': conn.capture, 'run': None, 'recipe': None, 'access_passed': False,
                     'project_checks': [c['id'] for c in conn.checks()]}
            workflow = Workflow(fixture, checker)
            try:
                prepared = workflow.new(connection=name)
                entry.update(run=prepared['run'], recipe=prepared['recipe'], access_passed=prepared['checks_passed'] is True)
            except PROBLEMS + (IndexError, subprocess.SubprocessError) as exc:
                entry['error'] = f'{type(exc).__name__}: {exc}'
                entry['run'] = max(workflow.touched, key=lambda r: int(r[4:]), default=None)
                record = fixture.record(entry['run']) if entry['run'] else None
                entry['recipe'] = record['recipe']['metadata']['id'] if record else None
            kept = retain(fixture, entry['run'], folder / name)
            entry['evidence'] = f'{name}/' if kept else None
            for disposable, copy in kept.items() if 'error' in entry else ():
                entry['error'] = entry['error'].replace(disposable, copy)  # The disposable path is about to go.
            results[name] = entry
        for name in checked:
            after = checkout_state(fixture.connections[name].checkout)
            results[name]['checkout_unchanged'] = 'error' not in before[name] and before[name] == after
    report['disposable_roots_removed'] = not base.exists()
    report['access_passed'] = (bool(results) and report['disposable_roots_removed']
                               and all(e['access_passed'] and e['checkout_unchanged'] for e in results.values()))
    write(folder / 'report.json', report)
    return report


def native_check(store, native, folder):
    """A supplied native access check, kept in folder/native-access or in a removed temporary folder."""
    if native is None:
        return row('not-run', 'Not selected', step=NATIVE_NEXT)
    if store is None:
        return row('not-run', 'Depends on workspace')
    evidence = None if folder is None else folder / 'native-access'
    keeping = None if evidence else f'Keep the evidence with {NATIVE_NEXT} --record experiments/NAME'
    try:
        if evidence is None:
            with tempfile.TemporaryDirectory(prefix='berman-native-access-') as temporary:
                report = native(store, Path(temporary))
        else:
            evidence.mkdir(parents=True)
            report = native(store, evidence)
    except Exception as exc:  # A refusal or harness failure is this check's result, never verify's.
        return row('failed', f'The native access check did not complete: {exc}', step=keeping,
                   evidence=str(evidence) if evidence else None, retained=evidence is not None)
    details = {'evidence': str(evidence) if evidence else None, 'retained': evidence is not None, 'report': report}
    if isinstance(report, dict) and report.get('access_passed') is True:
        return row('passed', 'Native access passed in disposable roots' + ('' if evidence else '; evidence not retained'),
                   **details)
    connections = report.get('connections', {}) if isinstance(report, dict) else {}
    problems = [f'{name}: ' + (c.get('error') or ('access check did not pass' if not c.get('access_passed')
                                                  else 'checkout changed during the check'))
                for name, c in connections.items() if not (c.get('access_passed') and c.get('checkout_unchanged'))]
    if isinstance(report, dict) and connections and report.get('disposable_roots_removed') is False:
        problems.append('disposable roots were not removed')
    return row('failed', 'Native access did not pass', step=keeping, problems=problems, **details)


def verdict(checks):
    if any(c['status'] == 'failed' for c in checks.values()):
        return 'failed'
    if any(c['required'] and c['status'] != 'passed' for c in checks.values()):
        return 'incomplete'
    return 'passed'


def verify(root=ROOT, skip=(), native=None, record=None, run=subprocess.run, code=ROOT):
    """Run every check not skipped and return the result; a failed check is reported, never raised.

    root is the workspace (as --root); code is the repository whose suites, frozen collector and
    builder are checked. native, when given, is called as native(store, folder) and returns a report
    whose access_passed must be True. Raises Decision only for a refused record folder, an unknown
    skip, or a nested run with the real subprocess runner.
    """
    if os.environ.get('BERMAN_VERIFYING') and run is subprocess.run:
        raise Decision('berman verify is already running in this process tree; a nested run would repeat its own suites')
    if set(skip) - set(SKIPPABLE):
        raise Decision('Only these checks can be skipped: ' + ', '.join(SKIPPABLE))
    root, code = Path(root).expanduser().resolve(), Path(code).resolve()
    folder = None if record is None else evidence_folder(root, record)
    started = files.now()
    try:
        store, problem = Store(root), None
    except PROBLEMS as exc:
        store, problem = None, str(exc)
    python = str(store.python) if store else sys.executable
    checks, receipts = {}, {}
    for name, start in SUITES.items():
        if name in skip:
            checks[name] = skipped(name)
        else:
            checks[name], receipts[RECEIPTS[name]] = suite(python, code, start, run)
    checks['frozen-collector'] = integrity(code)
    checks['recorded-hashes'] = recorded_hashes(root)
    checks['workspace'] = workspace(store, problem)
    checks.update(adapters(store, root, code))
    if 'archive' in skip:
        checks['archive'] = skipped('archive')
    else:
        checks['archive'], output = archive(python, root, code, run)
        receipts.update(output)
    checks['native-access'] = native_check(store, native, folder)
    for name, check in checks.items():
        check['meaning'] = MEANING[name.partition(':')[0]]
    states = {state: [name for name, c in checks.items() if c['status'] == state]
              for state in ('failed', 'not-run', 'not-established')}
    result = {'schema': 'berman.verification/1', 'verdict': verdict(checks), 'checked_at_utc': started,
              'root': str(root), 'code': str(code), 'python': python, 'python_version': version(python, run),
              'checks': checks, 'failed': states['failed'], 'not_run': states['not-run'],
              'not_established': states['not-established'], 'not_covered': list(NOT_COVERED), 'record': None,
              'meaning': 'Each check is a separate fact; passed requires every required check. Nothing was rebuilt, '
                         'reviewed or launched.'}
    if folder is not None:
        kept = {name: data for name, data in receipts.items() if data}
        result['record'] = {'directory': str(folder), 'files': ['verification.json', *kept]
                            + (['native-access/'] if (folder / 'native-access').is_dir() else [])}
        for name, data in kept.items():
            keep(folder / name, data.encode())
        keep(folder / 'verification.json', files.json_bytes(result))
    return result


def render(result):
    lines = [f'Berman verification: {result["verdict"].upper()}']
    for name, check in result['checks'].items():
        label = LABELS[check['status']] + ('' if check['required'] else ' (optional)')
        lines.append(f'  {label:<28}{name}: {check["summary"]}')
        for key, prefix in LISTED.items():
            items = check['details'].get(key) or []
            lines += [' ' * 30 + prefix + item for item in items[:10]]
            if len(items) > 10:
                lines.append(' ' * 30 + f'... and {len(items) - 10} more')
        if check.get('next'):
            lines.append(' ' * 30 + 'next: ' + check['next'])

    def named(names):
        return ', '.join(n + ('' if result['checks'][n]['required'] else ' (optional)') for n in names)
    for key, title in (('failed', 'Failed'), ('not_run', 'Not run'), ('not_established', 'Not established')):
        if result[key]:
            lines.append(f'{title}: {named(result[key])}')
    lines.append('Never checked by berman verify:')
    lines += ['  ' + item for item in result['not_covered']]
    if result['record']:
        lines.append('Recorded in ' + result['record']['directory'])
    return '\n'.join(lines)


def software(skip=(), run=subprocess.run, code=ROOT):
    """Verify portable source only; never load a workspace or harness home."""
    if os.environ.get('BERMAN_VERIFYING') and run is subprocess.run:
        raise Decision('berman verify is already running in this process tree')
    if set(skip) - set(SUITES):
        raise Decision('Software verification can skip only tests or launcher-tests')
    checks = {}
    for name, start in SUITES.items():
        checks[name] = skipped(name) if name in skip else suite(sys.executable, code, start, run)[0]
    checks['frozen-collector'] = integrity(code)
    for name, check in checks.items():
        check['meaning'] = MEANING[name]
    return {'schema': 'berman.verification/1', 'scope': 'software', 'verdict': verdict(checks),
            'checked_at_utc': files.now(), 'root': None, 'code': str(code), 'checks': checks,
            'failed': [n for n, c in checks.items() if c['status'] == 'failed'],
            'not_run': [n for n, c in checks.items() if c['status'] == 'not-run'],
            'not_established': [], 'not_covered': [
                'Workspace records, preserved evidence and reading editions.',
                'Installed launchers, hook trust, native capture and native access.'],
            'record': None, 'meaning': 'Software checks only. No workspace or native installation was verified.'}
