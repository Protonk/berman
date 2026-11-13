"""Capture adapters: markers, packet layouts, hook definitions, and their verification bases.

Each adapter's readiness rests on a recorded verification basis: the hook definition it
qualifies, the whole installed hooks.json observed when trust was reviewed in the actual CLI,
that observation, and the collector implementation bytes including local dependencies. Any
change to the definition, the installed file, or the implementation invalidates readiness.
"""
import json
import os
from pathlib import Path
import shlex
import subprocess

from berman.capture import collector
from berman import files
from berman.files import Decision, digest_json, keep, read, write
from berman._legacy.api import MARKER, RUN
from berman.installation import PACKAGE, command as module_command
from berman.records import closed_by

EVENTS = ('UserPromptSubmit', 'Stop', 'Interrupt', 'SessionEnd')
ROUTER_IMPLEMENTATION = ('capture/router.py', 'capture/runtime.py',
                         'capture/collector.py', '_legacy/capture_session.py')
ADAPTERS = {
    'legacy-codex': {
        'harness': 'codex', 'collector': 'capture_session', 'marker': MARKER, 'layout': 'legacy',
        'entry': 'capture/router.py', 'definition': 'hooks/berman-capture.hooks.json',
        'basis': 'hooks/verification/routed-legacy-codex.json', 'implementation': ROUTER_IMPLEMENTATION},
    'berman-codex': {
        'harness': 'codex', 'collector': collector.OWNER, 'marker': collector.MARKER, 'layout': 'notes',
        'entry': 'capture/router.py', 'definition': 'hooks/berman-capture.hooks.json',
        'basis': 'hooks/verification/berman-codex.json',
        'implementation': ROUTER_IMPLEMENTATION},
    'berman-claude': {
        'harness': 'claude', 'collector': 'berman-claude/1', 'marker': 'Berman-Claude-Fieldwork-Run: ',
        'layout': 'notes', 'entry': 'capture/claude_router.py',
        'definition': 'harnesses/claude.py', 'basis': 'hooks/verification/berman-claude.json',
        # The bytes that run in a fieldworker's session plus the module that generates its settings.
        # Readiness fingerprints the launcher helpers it imports by function source (berman_claude.helpers).
        'implementation': ('harnesses/claude.py', 'capture/claude_router.py',
                           'capture/claude.py', 'capture/runtime.py',
                           'capture/collector.py', 'files.py', '_legacy/api.py',
                           '_legacy/capture_session.py')},
}


def implementation(name):
    return {rel: files.digest((PACKAGE / rel).read_bytes()) for rel in ADAPTERS[name]['implementation']}


def reservation(name):
    a = ADAPTERS[name]
    return {'adapter': name, 'harness': a['harness'], 'collector': a['collector'], 'marker': a['marker'],
            'layout': a['layout'], 'definition': a['definition'], 'implementation_sha256': implementation(name)}


def definition(store, name):
    """A new adapter's hook definition, generated from resolved installation settings."""
    if name == 'berman-claude':
        from berman.harnesses.claude import definition as claude_definition
        return claude_definition()
    # The established quoting and timeouts of the legacy definition.
    command = shlex.join(module_command('hook', '--analysis-root', store.root, '--codex-home', store.home,
                                        python=store.python, module='berman.capture.router'))
    timeouts = {'UserPromptSubmit': 3, 'Stop': 10, 'Interrupt': 3, 'SessionEnd': 3}
    return {'description': 'Berman exclusive capture router. Routes Stewart-Fieldwork-Run and Berman-Fieldwork-Run '
                           'to their respective collectors; one owner per session.',
            'hooks': {event: [{'hooks': [{'type': 'command', 'command': command, 'timeout': timeouts[event]}]}]
                      for event in EVENTS}}


def installed_groups(installed, template):
    return all(group in installed.get('hooks', {}).get(event, []) for event in EVENTS
               for group in template['hooks'][event])


def capture_entry(hook):
    """Recognize our execution targets, including obsolete directly installed collectors."""
    try:
        argv = shlex.split(hook.get('command', ''))
    except ValueError:
        return None
    if any(argv[i:i + 2] == ['-m', 'berman.capture.router'] for i in range(len(argv) - 1)):
        return 'berman.capture.router'
    for name in ('capture_session.py', 'berman_collector.py', 'berman_capture_router.py'):
        if any(Path(arg).is_absolute() and Path(arg).name == name for arg in argv):
            return name
    return None


def exclusive_definition(installed, template):
    for event in set(installed.get('hooks', {})) | set(template['hooks']):
        groups = installed.get('hooks', {}).get(event, [])
        actual = [hook for group in groups for hook in group.get('hooks', []) if capture_entry(hook)]
        expected = [hook for group in template['hooks'].get(event, []) for hook in group['hooks']]
        if actual != expected:
            raise Decision(f'{event}: capture routing is not exclusive; install the reviewed single-router definition')


def installed(store, name):
    """The installed hooks and the adapter's definition, when the router is installed exclusively from it.

    Says nothing about trust: a support trial needs only this, because new bytes are what it verifies.
    """
    if name == 'berman-claude':
        template = definition(store, name)
        return {'adapter': name, 'config_path': None, 'installed': template, 'definition': template,
                'scope': 'Per-launch template; exact settings are generated and checked for each reservation.'}
    a = ADAPTERS[name]
    path = store.home / 'hooks.json'
    if not path.exists():
        raise Decision(f'No Codex hooks are installed at {path}; {name} capture is unavailable')
    hooks = read(path)
    if not (store.root / a['definition']).is_file():
        raise Decision(f'The {name} hook definition is missing from this workspace: {store.root / a["definition"]}')
    template = read(store.root / a['definition'])
    for event in EVENTS:
        expected = template['hooks'][event]
        if not expected or any(group not in hooks.get('hooks', {}).get(event, []) for group in expected):
            raise Decision(f'Installed {event} {name} capture hook differs; inspect it before launching. '
                           'To install the router, use berman capture install --adapter berman-codex')
        for group in expected:
            for hook in group['hooks']:
                argv = shlex.split(hook['command'])
                if (argv != module_command('hook', '--analysis-root', store.root, '--codex-home', store.home,
                                           python=store.python, module='berman.capture.router')
                        or not os.access(argv[0], os.X_OK) or not (PACKAGE / a['entry']).is_file()):
                    raise Decision(f'{event} collector or interpreter is unavailable')
    exclusive_definition(hooks, template)
    return {'adapter': name, 'config_path': str(path), 'installed': hooks, 'definition': template}


def readiness(store, name):
    if name == 'berman-claude':
        from berman.harnesses.claude import readiness as claude_readiness
        # Each connection using the adapter is checked against its own checkout; harness drift is a warning.
        projects = [c.checkout for c in store.connections.values() if c.capture == name]
        results = [claude_readiness(store, project) for project in projects] or [claude_readiness(store)]
        return dict(results[0], warnings=[w for r in results for w in r['warnings']])
    a = ADAPTERS[name]
    found = installed(store, name)
    hooks, template = found['installed'], found['definition']
    basis_path = store.root / a['basis']
    if not basis_path.exists():
        raise Decision(f'{name} capture has no recorded CLI trust review. Review /hooks in the actual Codex CLI, then '
                       f'record that observation with berman capture record-trust --evidence FILE --attest')
    basis = read(basis_path)
    if basis.get('schema') != 'berman.capture-verification/1' or basis.get('adapter') != name:
        raise Decision(f'Invalid capture verification basis: {basis_path}')
    if basis['definition_sha256'] != digest_json(template):
        raise Decision(f'The {name} hook definition changed after its trust review; review /hooks again and record it')
    if basis['implementation_sha256'] != implementation(name):
        raise Decision(f'The {name} collector implementation changed after its verification; renewed verification is required')
    trust = read(store.root / basis['trust_observation'])
    if any(trust.get('observed', {}).get(event) != {'enabled': True, 'trust': 'Trusted', 'source': '~/.codex/hooks.json'}
           for event in EVENTS):
        raise Decision('The retained CLI observation does not establish trust for all four hooks')
    evidence = Path(basis['trust_observation']).parent / trust['evidence']
    if files.digest((store.root / evidence).read_bytes()) != trust['evidence_sha256']:
        raise Decision('The retained CLI trust evidence differs from its receipt')
    if read(store.root / basis['installed_hooks']) != hooks:
        raise Decision('Capture trust needs actual CLI review and an updated verification basis; definitions alone do not establish it')
    successful = basis.get('successful_capture')
    if successful:
        events = [json.loads(line)['event'] for line in (store.root / successful['path']).read_text().splitlines()]
        if not set(successful['required_events']) <= set(events):
            raise Decision(f'The recorded successful capture for {name} lacks its required terminal events')
    return {'adapter': name, 'events': list(EVENTS), 'basis': str(basis_path), 'meaning': basis['meaning']}


def registrations(store):
    """Each recorded session, the collector that owns it, and whether a callback is pending.

    A damaged registry leaves both unknown (None), which never counts as closed.
    """
    rows = []
    for name, collector in store.collectors.items():
        if not collector.state.exists():
            continue
        for path in sorted(collector.state.glob('*.json')):
            try:
                state = read(path)
                run = state.get('run')
                if run and (not isinstance(run, str) or not RUN.fullmatch(run)):
                    raise ValueError('Registry has an invalid run identity')
            except (OSError, ValueError, TypeError, AttributeError) as exc:
                rows.append({'session_id': path.stem, 'run': None, 'collector': name, 'open': None,
                             'pending_callback': None, 'unknown': [f'cannot inspect {path}: {exc}']})
                continue
            if state.get('workspace') not in (None, str(store.root)) or not run:
                continue
            packet = store.field / run
            open_capture = packet.is_dir() and not closed_by(packet)
            rows.append({'session_id': state.get('session_id'), 'run': run, 'collector': name,
                         'open': open_capture and not state.get('retired'),
                         'pending_callback': bool(state.get('active_turn')) and not state.get('retired')})
    return rows


def install(store, name):
    """Quiescent cutover: replace our direct collectors with one router, retaining unrelated hooks."""
    with store.lock('installation', 'codex-hooks', wait=10):
        sessions = registrations(store)
        template = read(store.root / ADAPTERS[name]['definition'])
        if template != definition(store, name):
            raise Decision(f'{ADAPTERS[name]["definition"]} differs from the definition generated from berman.json')
        path = store.home / 'hooks.json'
        previous = files.regular(path).read_bytes() if path.exists() else None
        current = json.loads(previous) if previous else {'hooks': {}}
        if installed_groups(current, template):
            try:
                exclusive_definition(current, template)
            except Decision:
                pass  # Repair duplicate direct handlers only through the same quiescent cutover below.
            else:
                return {'state': 'already-installed', 'adapter': name, 'config_path': str(path)}
        active = [r for r in sessions if r['open'] is not False]
        if active:
            raise Decision('Capture cutover requires closed captures, including idle sessions: '
                           + ', '.join(f'{r["run"]} ({r["collector"]})' if r['open'] else
                                       f'{r["session_id"]} ({r["collector"]}: state unknown; {"; ".join(r["unknown"])})'
                                       for r in active)
                           + '. Finish or preserve them before replacing their hook')
        merged = json.loads(json.dumps(current))
        for event, groups in merged.setdefault('hooks', {}).items():
            retained = []
            for group in groups:
                hooks = [h for h in group.get('hooks', []) if not capture_entry(h)]
                if hooks:
                    retained.append(dict(group, hooks=hooks))
            merged['hooks'][event] = retained
        for event in EVENTS:
            groups = merged.setdefault('hooks', {}).setdefault(event, [])
            for group in template['hooks'][event]:
                if group not in groups:
                    groups.append(group)
        exclusive_definition(merged, template)
        stamp = files.now().replace(':', '').replace('.', '-')
        folder = store.root / 'hooks/installations' / f'{stamp}-{name}'
        if previous is not None:
            keep(folder / 'previous-hooks.json', previous)
        files.atomic(path, files.json_bytes(merged))
        receipt = {'schema': 'berman.hook-installation/1', 'adapter': name, 'installed_at_utc': files.now(),
                   'config_path': str(path), 'previous_sha256': files.digest(previous) if previous else None,
                   'installed_sha256': files.digest(path.read_bytes()), 'definition': ADAPTERS[name]['definition'],
                   'registrations_at_installation': sessions,
                   'ownership': 'One installed router owns both markers; direct legacy and new hooks were removed. '
                                'Exclusive session registration and per-session/per-packet locks apply to both collectors.',
                   'trust': 'Not granted by installer. Review /hooks in the actual Codex CLI, then run berman capture record-trust.'}
        write(folder / 'receipt.json', receipt)
        return dict(receipt, receipt=str(folder / 'receipt.json'))


def record_trust(store, evidence, statement):
    """Record an operator's actual CLI /hooks review for every adapter whose definition is installed."""
    with store.lock('installation', 'codex-hooks', wait=10):
        installed_bytes = files.regular(store.home / 'hooks.json').read_bytes()
        installed = json.loads(installed_bytes)
        exclusive_definition(installed, definition(store, 'berman-codex'))
        evidence_bytes = files.regular(Path(evidence)).read_bytes()
        version = subprocess.run([str(store.codex), '--version'], check=True, capture_output=True, text=True,
                                 timeout=20).stdout.strip()
        stamp = files.now().replace(':', '').replace('.', '-')
        folder = store.root / 'hooks/verification/observations' / stamp
        keep(folder / 'installed-hooks.json', installed_bytes)
        keep(folder / 'hooks.terminal', evidence_bytes)
        observation = {'schema': 'berman.hook-trust-observation/1', 'recorded_at_utc': files.now(),
                       'codex_version': version, 'evidence': 'hooks.terminal',
                       'evidence_sha256': files.digest(evidence_bytes), 'statement': statement,
                       'method': 'Operator attests to reviewing /hooks in the actual CLI; the supplied terminal record is retained.',
                       'observed': {event: {'enabled': True, 'trust': 'Trusted', 'source': '~/.codex/hooks.json'} for event in EVENTS}}
        write(folder / 'hook-trust.json', observation)
        written = []
        for name, a in ADAPTERS.items():
            if a['harness'] != 'codex':
                continue
            template = read(store.root / a['definition'])
            if not installed_groups(installed, template):
                continue
            basis_path = store.root / a['basis']
            previous = read(basis_path) if basis_path.exists() else {}
            if basis_path.exists():
                keep(basis_path.parent / 'history' / f'{basis_path.stem}-{stamp}.json', basis_path.read_bytes())
            current_implementation = implementation(name)
            same_capture_code = previous.get('implementation_sha256') == current_implementation
            successful = previous.get('successful_capture') if same_capture_code else None
            meaning = (previous.get('meaning') if successful else None) or (
                'Definitions reviewed in the actual CLI. No successful capture is recorded for these implementation '
                'bytes yet; verify with a native support trial before using an untested capture path for research.')
            basis = {'schema': 'berman.capture-verification/1', 'adapter': name, 'definition': a['definition'],
                     'definition_sha256': digest_json(template), 'implementation_sha256': current_implementation,
                     'installed_hooks': str((folder / 'installed-hooks.json').relative_to(store.root)),
                     'trust_observation': str((folder / 'hook-trust.json').relative_to(store.root)),
                     'successful_capture': successful, 'meaning': meaning,
                     'limitation': 'Operator-attested CLI observation; not a fresh inspection at each launch.'}
            write(basis_path, basis)
            written.append(str(basis_path))
        return {'observation': str(folder / 'hook-trust.json'), 'bases': written}


def native_capture(store, name):
    """The recorded native capture for these implementation bytes with its required events, or None.

    Call after readiness passes. Trust alone never implies this: record_trust carries a capture
    forward only for unchanged bytes, and the capture must name the bytes it ran with.
    """
    successful = read(store.root / ADAPTERS[name]['basis']).get('successful_capture')
    if not successful or successful.get('implementation_sha256') != implementation(name):
        return None
    events = [json.loads(line)['event'] for line in (store.root / successful['path']).read_text().splitlines()]
    if not set(successful['required_events']) <= set(events):
        return None
    return {'run': successful.get('run'), 'session_id': successful.get('session_id'),
            'events': sorted(successful['required_events']), 'path': successful['path'],
            'trial_receipt': successful.get('trial_receipt')}


def changed_implementation(store, name):
    """Fingerprinted files whose current bytes differ from, or are missing from, what the adapter's basis records.

    None when no basis records implementation bytes: with nothing reviewed there is nothing to compare.
    Files a basis recorded that the adapter no longer fingerprints are not changes.
    """
    path = store.root / ADAPTERS[name]['basis']
    recorded = read(path).get('implementation_sha256') if path.exists() else None
    if not recorded:
        return None
    return sorted(rel for rel, digest in implementation(name).items() if recorded.get(rel) != digest)
