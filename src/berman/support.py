"""Native support trials: verify capture with a packet that is never research and consumes no plan step.

A trial's identity (run-900101 to run-900199) is reserved by an immutable marker,
operations/<run>/operation.json with purpose support-trial, written once under the global lock
before any packet, profile or project exists, so discovery never takes it for research. The
trial's changing state and its evidence live in a new folder under experiments/ (trial.json). Two
acts need an operator, both inside launch's recorded terminal: the actual-CLI /hooks review and
the one-reply session. collect verifies what was captured and preserves the packet in the trial's
own collection. Trust is recorded only by berman capture record-trust, and a native capture only
by record_capture from verified records; Berman infers neither.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import uuid

import berman_capture
import berman_files as files
from berman_files import Decision, digest_json, keep, read, write
from berman_harness import Codex, profile_name
from berman_lifecycle import SESSION_MATERIAL, validate_record
from berman_verify import evidence_folder
import fieldwork

SCHEMA = 'berman.support-trial/1'
COLLECTED = ('collected', 'collected-incomplete')
CLOSED = ('cleaned', 'abandoned')
REQUIRED_EVENTS = ['Stop', 'SessionEnd']
PROBLEMS = (OSError, ValueError, KeyError, TypeError)
ASSIGNMENT = 'Perform the one-response support check described in INSTRUCTIONS.md.'
REVISION = 'native support trial of {adapter} capture; not a research engagement and not a study of any project'
RETIREMENT = ('Closed native support trial preserved in a separate collection; future resumption must not refresh '
              'this frozen packet.')
LIMITATIONS = 'Codex folder-trust entries are left in Codex configuration; Berman does not edit it.'
OPEN_TURN = ('A prompt had no later terminal callback. Once the packet left the shared packet root no callback could '
             'record one, so the registration was retired on the cleanup reason; registry-before-retirement.json keeps '
             'active_turn as it was.')
CAPTURE_BASIS = ('A separate native Codex CLI support session used the installed router and normal hook trust. The '
                 'expected final assistant response was present at Stop, Stop and SessionEnd were captured, and the '
                 'closed packet was finalized and preserved separately without an incomplete-boundary override.')


def relative(store, path):
    return Path(path).relative_to(store.root).as_posix()


def requested(store, path):
    return Path(os.path.abspath(store.root / Path(path).expanduser()))


def evidence(store, path):
    """The trial folder, exactly experiments/NAME: new, or already holding a trial.json."""
    folder = requested(store, path)
    if folder.parent != store.root / 'experiments':
        raise Decision(f'A support trial folder lies directly under {store.root / "experiments"} (experiments/NAME), '
                       f'never inside another experiment or packet; refusing {folder}')
    return evidence_folder(store.root, path, resume='trial.json')


def expected_reply(run):
    return f'BERMAN_SUPPORT_TRIAL_OK_{run[4:]}'


def command(store, record):
    """The Codex invocation a trial launches, from its immutable marker."""
    return [str(store.codex), '--no-daemon', '--no-alt-screen', '-p', record['harness']['profile'], '-C', record['project']]


def claim(store, folder, run=None):
    """This folder's trial.json and the reservation marker naming it; a Decision, writing nothing, otherwise.

    trial.json changes as a trial proceeds, so its identity, packet and project count only where
    the immutable marker agrees. A folder holding any other trial.json is never treated as a trial.
    """
    rel, path = relative(store, folder), folder / 'trial.json'
    if not path.exists():
        raise Decision(f'No support trial is recorded in {folder}; start one with berman support-trial prepare')
    try:
        trial = read(path)
    except (OSError, ValueError) as exc:
        raise Decision(f'{path} cannot be read ({exc}); it was left unchanged for inspection') from exc
    if not isinstance(trial, dict) or trial.get('schema') != SCHEMA or trial.get('evidence') != rel:
        raise Decision(f'{path} is not a {SCHEMA} receipt for {rel}; it was left unchanged. A maintained support trial '
                       'starts in a new folder')
    found = trial.get('run')
    if run is not None and found != run:
        raise Decision(f'{path} now names {found}, not {run}; it was left unchanged for inspection')
    try:
        record = store.record(found) if isinstance(found, str) else None
    except PROBLEMS as exc:
        raise Decision(f'{found}: its reservation marker cannot be used ({exc}); {rel} was left unchanged') from exc
    marked = record and record.get('purpose') == 'support-trial' and (
        record['support_trial'], record['capture']['adapter'], record['packet'], record.get('project'),
        record['harness'].get('profile'), expected_reply(found)) == (
        rel + '/trial.json', trial.get('adapter'), trial.get('packet'), trial.get('project'), trial.get('profile'),
        trial.get('expected_reply'))
    if not marked or not isinstance(record.get('project'), str) or not isinstance(record['harness'].get('profile'), str):
        raise Decision(f'{found}: no support reservation marker agrees with {rel}/trial.json; both were left unchanged. '
                       f'Inspect {store.folder(found) if record else store.operations}')
    if record['packet'] != str(store.field / found):
        raise Decision(f'{found}: recorded packet {record["packet"]} is outside the shared packet root {store.field}')
    return trial, record


def receipt(store, path):
    folder = evidence(store, path)
    return (folder, *claim(store, folder))


def interrupted(store, path):
    """Remove the empty folder that a reservation interrupted after its marker left; prepare then resumes that marker."""
    folder = requested(store, path)
    if folder.parent != store.root / 'experiments' or folder.parent.is_symlink() or folder.is_symlink() \
            or not folder.is_dir() or any(folder.iterdir()) or not store.operations.is_dir():
        return
    name = relative(store, folder) + '/trial.json'
    with store.lock('global', wait=30):
        for p in sorted(store.operations.glob('run-*')):
            try:
                record = store.record(p.name)
            except PROBLEMS:
                continue
            if (record or {}).get('purpose') == 'support-trial' and record['support_trial'] == name:
                if folder.is_dir() and not folder.is_symlink() and not any(folder.iterdir()):
                    folder.rmdir()
                return


def catalogue(store):
    path = store.workspace.get('catalogue')
    if not path:
        raise Decision('Support collections are listed in the run catalogue, and this workspace configures none')
    return store.path(path)


def codex_config(store):
    """Codex's own configuration; folder trust may change it, and Berman never edits it."""
    path = store.home / 'config.toml'
    return files.digest(files.regular(path).read_bytes()) if path.exists() else None


def installation(store, adapter, harness):
    """What the trial runs against. The router must be installed exclusively; its trust may predate these bytes."""
    found = berman_capture.installed(store, adapter)
    try:
        readiness = {'ready': True, 'basis': berman_capture.readiness(store, adapter)['basis']}
    except PROBLEMS as exc:
        readiness = {'ready': False, 'reason': str(exc)}
    version = harness.invoke([str(store.codex), '--version'], store.root, check=True, timeout=20).stdout.strip()
    return {'codex_version': version, 'implementation_sha256': berman_capture.implementation(adapter),
            'definition_sha256': digest_json(found['definition']), 'installed_hooks_sha256': digest_json(found['installed']),
            'codex_config_sha256': codex_config(store), 'readiness_at_preparation': readiness}


def instructions(packet, layout, token):
    placeholders = (f'`{packet}/notes/` is an unused layout placeholder.' if layout == 'notes' else
                    f'`{packet}/narrative.md` and `{packet}/scratch/` are unused layout placeholders.')
    return ('This is a one-response infrastructure support trial, not a research engagement.\n'
            f'{placeholders}\n'
            'No fieldwork notes, development, other file inspection or extra tool use is requested.\n'
            f'After reading these instructions, reply exactly {token} and stop.\n')


def reserve(store, folder, adapter, facts):
    """The short global section: allocate an identity and publish its marker before anything else exists."""
    rel = relative(store, folder)
    with store.lock('global', wait=30):
        if (folder / 'trial.json').exists():
            return claim(store, folder)[0]  # A concurrent prepare reserved it first.
        rows = store.discover()
        if store.discovery_problems:
            raise Decision(store.discovery_problems[0] + '; recover its identity before reserving a support trial')
        # A marker that an interrupted prepare published for this folder is resumed, not reserved again.
        claimed = [run for run, t in store.support_trials.items() if t['record']['support_trial'] == rel + '/trial.json']
        if claimed:
            run, record = claimed[0], store.support_trials[claimed[0]]['record']
            if record['capture']['adapter'] != adapter:
                raise Decision(f'{run} was reserved for {record["capture"]["adapter"]} in this folder, not {adapter}')
        else:
            run = store.allocate_support(rows)
            project = (Path(facts['project']) if adapter == 'berman-claude' else
                       Path(tempfile.gettempdir()).resolve() / f'berman-support-{run}-{uuid.uuid4().hex[:8]}')
            record = {'schema': 'berman.operation/1', 'run': run, 'phase': 'support-trial', 'purpose': 'support-trial',
                      'support_trial': rel + '/trial.json', 'reserved_at_utc': files.now(),
                      'workspace': {'root': str(store.root), 'tag': store.tag}, 'packet_root': str(store.field),
                      'packet': str(store.field / run), 'project': str(project),
                      'capture': berman_capture.reservation(adapter),
                      'harness': store.harness_identity(adapter, run)}
            keep(store.folder(run) / 'operation.json', files.json_bytes(validate_record(record, run)))
        trial = {'schema': SCHEMA, 'run': run, 'adapter': adapter, 'phase': 'reserved',
                 'purpose': 'Native support trial; consumes no research-plan step.', 'evidence': rel,
                 'operation_record': relative(store, store.folder(run) / 'operation.json'),
                 'packet': record['packet'], 'project': record['project'], 'profile': record['harness']['profile'],
                 'expected_reply': expected_reply(run), 'reserved_at_utc': record['reserved_at_utc'],
                 **facts}
        write(folder / 'trial.json', trial)
    return trial


def prepare(store, path, adapter, harness=None):
    """Reserve a support identity, then prepare its packet, disposable project and opt-in profile.

    Resumable while the trial is reserved; a trial in any later phase is never prepared again.
    """
    if adapter == 'berman-claude':
        from berman_claude_support import prepare as claude_prepare
        return claude_prepare(store, path)
    if adapter not in berman_capture.ADAPTERS:
        raise Decision(f'Unknown capture adapter {adapter}')
    harness = harness or Codex(store)
    interrupted(store, path)
    folder = evidence(store, path)

    def refuse(trial):
        if trial.get('phase') != 'reserved' or trial.get('adapter') != adapter:
            raise Decision(f'{folder} already holds a {trial.get("adapter")} trial in phase {trial.get("phase")}; a trial '
                           'is prepared once. Continue it with the command berman status names, or choose a new folder')
    trial = claim(store, folder)[0] if (folder / 'trial.json').exists() else None
    if trial:
        refuse(trial)
    catalogue(store)
    if trial is None:
        trial = reserve(store, folder, adapter, installation(store, adapter, harness))
    run = trial['run']
    with store.lock('run', run):
        trial, record = claim(store, folder, run)
        refuse(trial)  # A concurrent prepare or cleanup may have moved it on.
        if berman_capture.implementation(adapter) != record['capture']['implementation_sha256']:
            raise Decision('Capture code changed after this trial was reserved; clean it up and prepare a new trial '
                           'in a new folder')
        a = berman_capture.ADAPTERS[adapter]
        project, packet = Path(record['project']), Path(record['packet'])
        project.mkdir(mode=0o700, exist_ok=True)
        files.safe_directory(project)
        subprocess.run(['git', '-C', str(project), 'init', '-q'], check=True, capture_output=True)
        source = folder / 'SUPPORT-INSTRUCTIONS.txt'
        keep(source, instructions(packet, a['layout'], trial['expected_reply']).encode())
        if not packet.exists():
            notes = a['layout'] == 'notes'
            fieldwork.prepare(store.collector_for(record), run, ASSIGNMENT, project, REVISION.format(adapter=adapter),
                              ['notes'] if notes else ['narrative', 'scratch'], source,
                              recording_protocol=fieldwork.SUPPORT_PROTOCOL if notes else None, marker=a['marker'],
                              layout=a['layout'], connection=fieldwork.SUPPORT_CONNECTION)
        harness.install_profile(run, project, folder)
        trial.update(phase='prepared', prepared_at_utc=files.now(), instructions=source.name, argv=command(store, record))
        write(folder / 'trial.json', trial)
    return dict(trial, next=next_step(store, trial))


def guide(trial):
    return (f'Native support trial {trial["run"]} ({trial["adapter"]}). It is not a research engagement and consumes no '
            'research-plan step.\n\n'
            'In the Codex session this command starts, inside the recorded terminal:\n'
            f'1. Answer the folder-trust prompt for the disposable project {trial["project"]}.\n'
            '2. Type /hooks and review all four capture events (UserPromptSubmit, Stop, Interrupt, SessionEnd): each '
            'must be enabled and Trusted, from ~/.codex/hooks.json. This record is the evidence for berman capture '
            'record-trust.\n'
            f'3. Paste the whole of {trial["packet"]}/ASSIGNMENT.md as the first message.\n'
            f'4. After the single reply ({trial["expected_reply"]}), exit Codex normally so SessionEnd is captured.\n\n'
            f'Then: berman support-trial collect --evidence {trial["evidence"]}\n')


def launch(store, path, model=None, max_budget_usd=None):
    """Start the trial's one interactive session inside a recorded terminal; a trial is never launched twice."""
    folder, trial, _ = receipt(store, path)
    if trial['adapter'] == 'berman-claude':
        from berman_claude_support import launch as claude_launch
        return claude_launch(store, path, model, max_budget_usd)
    if model is not None or max_budget_usd is not None:
        raise Decision('The model/budget flags here belong to the bounded Claude support trial')
    run, terminal = trial['run'], folder / 'native.terminal'

    def refuse_repeat(trial):
        if trial.get('phase') != 'prepared' or terminal.exists() or terminal.is_symlink():
            raise Decision(f'{run} is {trial.get("phase")}, not prepared, or its terminal record exists; a native trial is '
                           'never repeated. Collect or clean it up, and prepare another in a new folder')
    refuse_repeat(trial)
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        raise Decision('berman support-trial launch is interactive; run it in a terminal, where the /hooks review and '
                       'the session are recorded')
    with store.lock('run', run):
        trial, record = claim(store, folder, run)
        refuse_repeat(trial)
        store.check_installation(record)
        if any((Path(record['packet']) / name).exists() for name in SESSION_MATERIAL):
            raise Decision(f'A session already registered {run} outside launch, so it is not launched. Collect it with '
                           f'berman support-trial collect --evidence {trial["evidence"]} --allow-incomplete REASON (it '
                           'demonstrates no capture), and prepare another trial in a new folder')
        installed = store.home / (record['harness']['profile'] + '.config.toml')
        if files.regular(installed).read_bytes() != files.regular(folder / 'profile.config.toml').read_bytes():
            raise Decision(f'{installed} differs from the prepared profile; inspect it before launching')
        text = guide(trial)
        keep(folder / 'LAUNCH.txt', text.encode())
        argv = ['/usr/bin/script', '-q', str(terminal), *command(store, record)]
        trial.update(phase='launching', launch={'requested_at_utc': files.now(), 'argv': argv, 'terminal': terminal.name})
        write(folder / 'trial.json', trial)
    print(text, flush=True)
    os.environ['CODEX_HOME'] = str(store.home)
    try:
        os.execv(argv[0], argv)
    except OSError as exc:
        with store.lock('run', run):
            failed = dict(trial.pop('launch'), exec_error=str(exc))
            trial.setdefault('failed_launches', []).append(failed)
            trial['phase'] = 'prepared'
            write(folder / 'trial.json', trial)
        raise


def observe(trial, packet, status):
    """What collect requires of the native capture, and what is missing from it."""
    captures = packet / 'session/captures.jsonl'
    rows = [json.loads(line) for line in files.regular(captures).read_text().splitlines()] if captures.exists() else []
    events = [row.get('event') for row in rows]
    reply = files.digest(trial['expected_reply'].encode())
    missing = []
    if trial.get('phase') == 'prepared':
        missing.append('no launch was recorded: the session started outside berman support-trial launch')
    if berman_capture.implementation(trial['adapter']) != trial['implementation_sha256']:
        missing.append('the capture implementation changed after preparation')
    if (status.get('binding') or {}).get('run') != trial['run']:
        missing.append(f'no session binding for {trial["run"]}')
    missing += [f'no {event} capture' for event in REQUIRED_EVENTS if event not in events]
    # The token also appears where the session read its instructions, so only the Stop record's hash is precise.
    if not any(row.get('event') == 'Stop' and row.get('last_assistant_message_present') is True
               and row.get('expected_last_assistant_sha256') == reply for row in rows):
        missing.append(f'no Stop capture whose final assistant message is exactly {trial["expected_reply"]}')
    if status.get('snapshot_consistent') is not True:
        missing.append('the capture snapshot is not consistent')
    if status.get('prompt_without_terminal_callback'):
        missing.append('a submitted prompt has no later terminal callback')
    return {'observed_at_utc': files.now(), 'state': status.get('state'),
            'session_id': (status.get('binding') or {}).get('session_id'), 'events': events, 'missing': missing}


def aside(store, record, collection):
    """The run's routed collector re-rooted at a support collection.

    Its locked() stays the workspace packet lock, which fieldwork.migrate holds only to recheck and
    rename, as in research preservation; the collection's own index takes the collection's lock.
    """
    live = store.collector_for(record)
    target = type(live)(live.fieldwork, getattr(live, 'claude_home', live.codex_home), collection).for_run(record['run'])
    target.locked = live.locked
    return target


def listed(store, collection):
    """Name the collection in the catalogue once; discovery and numbering then exclude its identity."""
    path = catalogue(store)
    data = read(path)
    entry = relative(store, collection)
    if entry not in data.get('support_collections', []):
        data.setdefault('support_collections', []).append(entry)
        write(path, data)


def collect(store, path, allow_incomplete=None):
    """Verify the native capture, then finalize and preserve the packet in the trial's own collection.

    Resumable at every step. Missing evidence is refused unless allow_incomplete explains an
    intentional boundary; the trial is then preserved but demonstrates no capture. So is a session
    that registered a prepared trial without launch: nothing else could collect or clean it up.
    """
    allow_incomplete = allow_incomplete if allow_incomplete and allow_incomplete.strip() else None
    folder, trial, _ = receipt(store, path)
    run, rel = trial['run'], trial['evidence']

    def refuse_missing(missing, advice):
        if missing and not allow_incomplete:
            raise Decision(f'Not collected: {"; ".join(missing)}. {advice}, or keep an intentional incomplete boundary '
                           f'with berman support-trial collect --evidence {rel} --allow-incomplete REASON (the trial then '
                           'demonstrates no capture)')
    with store.lock('run', run):
        trial, record = claim(store, folder, run)
        if trial.get('phase') in COLLECTED or trial.get('collected_at_utc'):
            return dict(trial, state='already-collected', next=next_step(store, trial))
        if trial.get('phase') not in ('launching', 'prepared'):
            raise Decision(f'{run} is {trial.get("phase")}; collect follows berman support-trial launch --evidence {rel}')
        live, target = store.collector_for(record), aside(store, record, folder / 'collection')
        packet, moved = store.field / run, folder / 'collection/runs' / run
        if packet.exists() and not (packet / 'session/finalized.json').exists():
            status = live.status(run)
            if not status.get('binding') and trial['phase'] == 'prepared':
                raise Decision(f'{run} is prepared; collect follows berman support-trial launch --evidence {rel}')
            if not status.get('binding'):
                raise Decision(f'No session has registered {run} ({status["state"]}); nothing to collect. If none will, '
                               f'berman support-trial cleanup --evidence {rel} --reason TEXT retains the unused packet')
            trial['observed'] = observe(trial, packet, status)
            refuse_missing(trial['observed']['missing'], 'Wait for the single reply and a normal exit')
            write(folder / 'trial.json', trial)
            write(folder / 'native-status.json', status)
            try:
                live.command('finalize', run, reason=allow_incomplete)
            except ValueError as exc:
                if not str(exc).startswith('Not finalized:'):
                    raise
                raise Decision(str(exc).split('. Wait/refresh,')[0] + '. Wait for the session to finish, or keep an '
                               'intentional incomplete boundary with --allow-incomplete REASON') from exc
        current = packet if packet.exists() else moved
        if not (current / 'session/finalized.json').exists():
            raise Decision(f'{run}: neither {packet} nor {moved} holds a finalized capture; inspect them')
        if 'observed' not in trial:  # Finalized outside collect: observe what it froze.
            observed = observe(trial, current, target.status(run))
            refuse_missing(observed['missing'], f'{run} was finalized outside collect with this evidence missing')
            trial['observed'] = observed
            if observed['missing']:
                trial['allow_incomplete'] = allow_incomplete  # Beside the frozen boundary's own override_reason.
            write(folder / 'trial.json', trial)
        keep(folder / 'finalization.json', files.regular(current / 'session/finalized.json').read_bytes())
        if not (folder / 'native-status.json').exists():
            write(folder / 'native-status.json', target.status(run))
        if not (folder / 'collection/source-layout.json').exists():
            write(folder / 'collection/source-layout.json', {'runs': []})
        keep(folder / 'migration.json', files.json_bytes(fieldwork.migrate(target, run)))
        with store.lock('global', wait=30):
            listed(store, folder / 'collection')
        frozen = read(folder / 'finalization.json')
        override = frozen['boundary']['override_reason']
        demonstrated = not trial['observed']['missing'] and override is None
        trial.update(phase='collected' if demonstrated else 'collected-incomplete', collected_at_utc=files.now(),
                     session_id=frozen['session_id'], events=trial['observed']['events'],
                     capture_sha256=frozen['capture_sha256'], preserved_packet=relative(store, moved),
                     demonstrates_capture=demonstrated, override_reason=override)
        write(folder / 'trial.json', trial)
    return dict(trial, next=next_step(store, trial))


def retire(store, folder, trial, collector):
    """Retire the closed session's registration under the session lock, then the packet lock, as the runtime orders them.

    A pending prompt is recorded, not waited for: with the packet out of the field, the collector answers
    every later callback before it would clear active_turn, so cleanup's reason stands for the session's end.
    """
    run, sid = trial['run'], trial['session_id']
    path = collector.registry(sid)
    before, after = folder / 'registry-before-retirement.json', folder / 'registry-after-retirement.json'
    if (store.field / run).exists():
        raise Decision(f'{run} is still in the shared packet root; collect it before cleanup')
    with collector.session_lock(sid), collector.locked():
        state = read(path)
        if state.get('run') != run or state.get('workspace') != str(store.root):
            raise Decision(f'{path} names another run or workspace; it was left unchanged for inspection')
        if not state.get('retired'):
            keep(before, files.regular(path).read_bytes())
            state.update(retired=True, retired_at_utc=files.now(), retirement_reason=RETIREMENT,
                         preserved_packet=trial['preserved_packet'])
            files.atomic(path, files.json_bytes(state))
        elif not before.exists():
            raise Decision(f'{path} was retired outside this trial; inspect it')
        keep(after, files.regular(path).read_bytes())
    return {'support_registry_retired': str(path), 'registry_before': before.name, 'registry_after': after.name,
            'open_turn_at_retirement': OPEN_TURN if read(before).get('active_turn') else None}


def unused(store, folder, trial, collector):
    """Move a packet no session registered into the trial folder, as an unused research preparation is retained."""
    run = trial['run']
    packet, destination, collection = store.field / run, folder / 'unused-packet', folder / 'collection'
    if (collection / 'runs' / run).exists() or (collection / 'migrations' / f'{run}.json').exists():
        raise Decision(f'An interrupted collect already moved {run} toward {relative(store, collection)}; finish it with '
                       f'berman support-trial collect --evidence {trial["evidence"]}')
    if any((base / name).exists() for base in (packet, destination) for name in SESSION_MATERIAL):
        raise Decision(f'{run} has session material; collect it first (berman support-trial collect --evidence '
                       f'{trial["evidence"]}, with --allow-incomplete REASON for an incomplete capture)')
    if packet.exists():
        if destination.exists():
            raise Decision('Both unused-packet locations exist; neither was overwritten')
        if 'unused_inventory' not in trial:
            # Hash outside the packet lock; recheck metadata briefly under it.
            trial['unused_inventory'] = files.inventory(packet)
            write(folder / 'trial.json', trial)
        with collector.locked():
            if any((packet / name).exists() for name in SESSION_MATERIAL):
                raise Decision(f'{run} has session material; collect it before cleanup')
            if not files.metadata_matches(packet, trial['unused_inventory']):
                raise Decision('Unused packet changed during cleanup')
            packet.rename(destination)
    if not destination.exists():
        return None
    if files.inventory(destination) != trial['unused_inventory']:
        raise Decision('Retained unused packet differs from its inventory')
    return relative(store, destination)


def disposable(folder, trial, record):
    """Remove the disposable project only if nothing in it lies outside .git; otherwise keep it and say why."""
    path = Path(record['project'])
    if not path.exists():
        return {'empty_disposable_project_removed': trial.get('project_removed'), 'project_retained': None}
    files.safe_directory(path)
    extra = sorted(p.name for p in path.iterdir() if p.name != '.git' or p.is_symlink() or not p.is_dir())
    if extra or not path.name.startswith('berman-support-'):
        reason = 'it holds files outside .git: ' + ', '.join(extra[:20]) if extra else 'it is not a disposable trial project'
        return {'empty_disposable_project_removed': None, 'project_retained': {'path': str(path), 'reason': 'Retained: ' + reason}}
    shutil.rmtree(path)
    trial['project_removed'] = str(path)
    write(folder / 'trial.json', trial)
    return {'empty_disposable_project_removed': str(path), 'project_retained': None}


def cleanup(store, path, reason):
    """Close a trial: retire its registration or retain its unused packet, and remove an empty disposable project.

    The opt-in profile is retained, and Codex configuration is never edited. Idempotent.
    """
    if not reason or not reason.strip():
        raise Decision('Describe why this support trial is being cleaned up')
    folder, trial, _ = receipt(store, path)
    run = trial['run']
    with store.lock('run', run):
        trial, record = claim(store, folder, run)
        if trial.get('phase') in CLOSED:
            return dict(read(folder / 'cleanup.json'), state='already-cleaned', phase=trial['phase'])
        collector = store.collector_for(record)
        result = {'schema': 'berman.support-trial-cleanup/1', 'at_utc': files.now(), 'run': run, 'reason': reason,
                  'support_registry_retired': None, 'registry_before': None, 'registry_after': None,
                  'open_turn_at_retirement': None, 'preserved_packet': trial.get('preserved_packet'), 'unused_packet': None}
        if trial.get('phase') in COLLECTED:
            result.update(retire(store, folder, trial, collector))
            phase = 'cleaned'
        else:
            result['unused_packet'] = unused(store, folder, trial, collector)
            phase = 'abandoned'
        result.update(disposable(folder, trial, record))
        result.update(opt_in_profile_retained=trial['profile'])
        if trial['adapter'] == 'berman-claude':
            result['limitations'] = 'Per-launch settings retained; no shared Claude settings were installed or edited.'
        else:
            result.update(limitations=LIMITATIONS,
                          codex_config_sha256={'at_preparation': trial.get('codex_config_sha256'),
                                               'at_cleanup': codex_config(store)})
        if (folder / 'cleanup.json').exists():
            result = read(folder / 'cleanup.json')  # An interrupted cleanup already recorded its result.
        else:
            keep(folder / 'cleanup.json', files.json_bytes(result))
        trial.update(phase=phase, cleanup='cleanup.json')
        write(folder / 'trial.json', trial)
    return dict(result, phase=phase)


def next_step(store, trial):
    """The command that continues a trial from its recorded phase."""
    rel, phase, adapter = trial['evidence'], trial.get('phase'), trial['adapter']
    if phase == 'reserved':
        return f'berman support-trial prepare --evidence {rel} --adapter {adapter}'
    if phase == 'prepared':
        if adapter == 'berman-claude':
            return f'berman support-trial launch --evidence {rel} --model haiku --max-budget-usd 2 (bounded model usage)'
        return f'berman support-trial launch --evidence {rel} (interactive, in a terminal)'
    if phase == 'launching':
        return f'berman support-trial collect --evidence {rel}, after the single reply and a normal exit'
    cleanup = f'berman support-trial cleanup --evidence {rel} --reason TEXT'
    if phase == 'collected-incomplete':
        return cleanup + '; this trial demonstrates no capture, so prepare another in a new folder'
    if phase != 'collected':
        return None
    if adapter == 'berman-claude':
        return (f'berman capture record-capture --trial {rel}; ' + cleanup + ' (an actual-CLI /hooks review is '
                f'optional: berman capture record-trust --adapter berman-claude --trial {rel} --evidence FILE '
                '--attest --statement TEXT)')
    if berman_capture.implementation(adapter) != trial.get('implementation_sha256'):
        return cleanup + '; capture code changed after this trial, so it cannot be linked. Prepare another in a new folder'
    steps = []
    try:
        berman_capture.readiness(store, adapter)
    except PROBLEMS:
        steps.append("with the researcher's approval, berman capture record-trust --evidence "
                     f'{store.root / rel / "native.terminal"} --attest')
    try:
        # Consulted even before a renewed review: record_trust keeps the link for unchanged bytes.
        capture = berman_capture.native_capture(store, adapter)
    except PROBLEMS:
        capture = None
    if capture is None:
        steps.append(f'berman capture record-capture --trial {rel}')
    elif capture.get('trial_receipt') != rel + '/trial.json':
        return (', then '.join(steps + [cleanup]) + f'; {capture.get("run") or capture["path"]} already records the native '
                'capture for these implementation bytes, so this trial is not linked')
    return ', then '.join(steps + [cleanup])


def status(store):
    """Trials not yet cleaned up, from the latest store.discover(); reads only."""
    result = []
    for run, found in sorted(store.support_trials.items()):
        record, problems = found['record'], list(found['problems'])
        folder, adapter = Path(record['support_trial']).parent.as_posix(), record['capture']['adapter']
        trial, step = {}, None
        if not (store.root / record['support_trial']).exists():
            problems.append(f'{run}: its marker names {record["support_trial"]}, which was never written')
            place = store.root / folder
            if not place.exists() or (place.is_dir() and not place.is_symlink() and not any(place.iterdir())):
                step = f'berman support-trial prepare --evidence {folder} --adapter {adapter}, which resumes {run}'
        else:
            try:
                trial = claim(store, store.root / folder, run)[0]
            except PROBLEMS as exc:
                problems.append(f'{run}: {exc}')
        if trial.get('phase') in CLOSED and not problems:
            continue
        packet = Path(record['packet'])
        try:
            capture = (store.collector_for(record).status(run)['state'] if packet.exists()
                       else 'preserved in ' + trial['preserved_packet'] if trial.get('preserved_packet') else 'no packet')
        except PROBLEMS as exc:
            capture = None
            problems.append(f'{run}: {exc}')
        if trial.get('phase') == 'prepared' and any((packet / name).exists() for name in SESSION_MATERIAL):
            step = (f'berman support-trial collect --evidence {folder} --allow-incomplete REASON; a session registered '
                    'without launch, so this trial demonstrates no capture')
        elif trial:
            step = next_step(store, trial)
        result.append({'run': run, 'adapter': adapter, 'phase': trial.get('phase'), 'evidence': folder,
                       'capture_state': capture, 'next': step, 'problems': problems})
    return result


def record_capture(store, path):
    """Link a verified native capture to its adapter's trust basis, for the bytes both were made with."""
    folder, trial, _ = receipt(store, path)
    if trial['adapter'] == 'berman-claude':
        from berman_claude_support import record_capture as claude_record_capture
        return claude_record_capture(store, path)
    adapter, run, rel = trial['adapter'], trial['run'], trial['evidence']
    if trial.get('phase') not in ('collected', 'cleaned') or trial.get('demonstrates_capture') is not True:
        raise Decision(f'{rel} demonstrates no native capture (phase {trial.get("phase")}); a capture is recorded only from '
                       'a complete collected trial. Run a new support trial')
    current = berman_capture.implementation(adapter)
    if trial['implementation_sha256'] != current:
        raise Decision(f'Capture code changed since {rel} ran; run a new support trial for these bytes')
    basis_path = store.root / berman_capture.ADAPTERS[adapter]['basis']
    collection = folder / 'collection'
    entry = next((r for r in read(collection / 'source-layout.json')['runs'] if r['run'] == run), None)
    if entry is None:
        raise Decision(f'{run} is not indexed in {collection}')
    packet = collection / 'runs' / run
    fieldwork.verify_moved(packet, entry)
    captures = packet / 'session/captures.jsonl'
    events = {json.loads(line)['event'] for line in files.regular(captures).read_text().splitlines()}
    binding = read(packet / 'session/binding.json')
    if not set(REQUIRED_EVENTS) <= events or (binding.get('run'), binding.get('session_id')) != (run, trial['session_id']):
        raise Decision(f'{relative(store, packet)} does not hold the capture {rel}/trial.json records')
    successful = {'path': relative(store, captures), 'required_events': list(REQUIRED_EVENTS), 'run': run,
                  'session_id': trial['session_id'], 'trial_receipt': rel + '/trial.json',
                  'implementation_sha256': current, 'basis': CAPTURE_BASIS}
    # record_trust rewrites the basis under this lock, so the basis is judged only while it is held.
    with store.lock('installation', 'codex-hooks', wait=10):
        try:
            berman_capture.readiness(store, adapter)
        except PROBLEMS as exc:
            raise Decision(f'{exc}. Record the actual CLI /hooks review for these bytes with berman capture record-trust '
                           f'first; this trial\'s terminal record is {folder / "native.terminal"}') from exc
        basis = read(basis_path)
        if (basis['implementation_sha256'] != current or basis['definition_sha256'] != trial['definition_sha256']
                or digest_json(read(store.root / basis['installed_hooks'])) != trial['installed_hooks_sha256']):
            raise Decision(f'{basis_path} records a review of other hooks than {rel} ran with; review /hooks for the '
                           'installed definition and run a new support trial')
        existing = basis.get('successful_capture')
        if existing == successful:
            return {'state': 'already-recorded', 'adapter': adapter, 'basis': str(basis_path), 'successful_capture': existing}
        if existing and existing.get('implementation_sha256') == current:
            raise Decision(f'{basis_path} already records a native capture for these implementation bytes '
                           f'({existing.get("run") or existing.get("path")}); it was not replaced')
        stamp = files.now().replace(':', '').replace('.', '-')
        keep(basis_path.parent / 'history' / f'{basis_path.stem}-before-capture-{stamp}.json', basis_path.read_bytes())
        meaning = (f'Actual CLI review plus successful native Stop and SessionEnd capture through these {adapter} '
                   f'implementation bytes (support {run}). Check each research session after its first reply.')
        write(basis_path, dict(basis, successful_capture=successful, meaning=meaning))
    return {'state': 'recorded', 'adapter': adapter, 'basis': str(basis_path), 'successful_capture': successful,
            'native_capture': berman_capture.native_capture(store, adapter),
            'next': 'hooks/verification is packaged; rebuild the archive once, then verify it'}
