"""Opt-in Claude fieldwork launch settings and tiered qualification receipts.

new/launch never silently spend model tokens on a preflight. Native qualification is an
explicit support trial. What its receipt fingerprints is tiered by whether a change could
alter what the fieldworker can see or what gets recorded (researcher's decision, 2 November
2026, experiments/claude-receipt-tiers-2026-11-02):

- stop: the hook template, the in-session implementation bytes, the native trial and probe
  implementation, the policy keys of every local settings file, and the retained trial evidence;
- warn: the Claude CLI binary and version, reported and recorded, with capture registration
  after the first reply as the positive check;
- context: whole settings files and the project's declared checks, retained and not compared.

No interactive /hooks review is required: the installed CLI's browser omits flag-supplied hooks,
so the trial's native hook events are the execution evidence (docs/limitations.md).
"""
from berman.installation import PACKAGE, command as module_command
import os
from pathlib import Path
import shlex
import subprocess

from berman import files
from berman.files import Decision, digest_json, read, write
from berman.capture.claude import EVENTS

BASIS = 'hooks/verification/berman-claude.json'
SCHEMA = 'berman.claude-verification/3'
CONFIGURATION = 'berman.claude-configuration/3'
# Settings keys that can change tool access or capture hooks; the rest of a file is context.
POLICY_KEYS = ('permissions', 'sandbox', 'hooks', 'disableAllHooks', 'allowManagedHooksOnly',
               'strictPluginOnlyCustomization')
HOOK_DISABLING = ('disableAllHooks', 'allowManagedHooksOnly', 'strictPluginOnlyCustomization')
TIERS = {'stop': 'hook template, in-session implementation bytes, readiness helpers, settings policy keys '
                 '(' + ', '.join(POLICY_KEYS) + ') and retained trial evidence',
         'warn': 'Claude CLI binary path, version, size and modification time',
         'context': 'whole settings files and the declared project checks, recorded and not compared'}
LIMITATION = ("The installed CLI's /hooks browser omits flag-supplied hooks, so no interactive review is required "
              'or attested; the native hook events of the support trial are the execution evidence (docs/limitations.md).')


def definition():
    return {'schema': 'berman.claude-hook-template/2', 'events': list(EVENTS),
            'entry': 'capture/claude_router.py',
            'installation': 'Per-launch --settings; run and workspace arguments bind each hook.',
            'capture': 'Only the reserved parent session; no stop continuation or completion inference.'}


def settings_path(store, run):
    return store.folder(run) / 'preparation/claude-settings.json'


def settings(store, record):
    root = str(store.root)
    command = module_command(python=store.runtime_python, module='berman.capture.claude_router')
    suffix = ['--root', root, '--run', record['run']]
    hooks = {event: [{'hooks': [{'type': 'command', 'command': shlex.join(command + ['capture'] + suffix),
                               'timeout': 20}]}] for event in EVENTS}
    return {'hooks': hooks}


def claude_sources(home, project):
    managed = Path('/Library/Application Support/ClaudeCode')
    sources = [home / 'settings.json', home / 'settings.local.json', managed / 'managed-settings.json',
               *sorted((managed / 'managed-settings.d').glob('*.json'))]
    for directory in (project, *project.parents):
        sources.extend(directory / '.claude' / name for name in ('settings.json', 'settings.local.json'))
    return list(dict.fromkeys(sources))


def helpers():
    """Qualification also depends on the exact probe run inside the Claude session."""
    return {name: files.digest((PACKAGE / name).read_bytes())
            for name in ('harnesses/probe.py', 'harnesses/codex.py', 'harnesses/claude_support.py')}


def policy_digest(value):
    """The digest of a settings file's policy keys, or None when it carries none (the same as no file)."""
    subset = {key: value[key] for key in POLICY_KEYS if key in value}
    return digest_json(subset) if subset else None


def configuration(store, project):
    # Exclude our generated per-run file: its exact bytes are checked separately; this
    # fingerprint is reusable only for the same project, policy and ambient settings.
    sources = claude_sources(store.claude_home, Path(project))
    if store.claude_home != Path(os.environ.get('CLAUDE_CONFIG_DIR', str(Path.home() / '.claude'))).expanduser().resolve():
        raise Decision('CLAUDE_CONFIG_DIR differs from berman.json; launch from the recorded Claude installation')
    issues = []
    present = {path: read(path) for path in sources if path.is_file()}
    for path, value in present.items():
        if any(value.get(key) for key in HOOK_DISABLING):
            issues.append(f'{path}: hooks from the per-launch settings may be disabled')
    if issues:
        raise Decision('Conflicting Claude configuration: ' + '; '.join(issues))
    version = subprocess.run([str(store.claude), '--version'], check=True, capture_output=True,
                             text=True, timeout=20).stdout.strip()
    binary = store.claude.resolve()
    stat = binary.stat()
    return {'schema': CONFIGURATION,
            'policy': {'project': str(project), 'workspace': str(store.root), 'packet_root': str(store.field),
                       'config_dir_environment': os.environ.get('CLAUDE_CONFIG_DIR'),
                       'claude_home': str(store.claude_home), 'python': str(store.runtime_python), 'template': definition(),
                       'settings': {str(path): policy_digest(present[path]) if path in present else None
                                    for path in sources}},
            'harness': {'binary': str(binary), 'version': version,
                        'binary_size': stat.st_size, 'binary_mtime_ns': stat.st_mtime_ns},
            'context': {'project_checks': {c.id: c.checks() for c in store.connections.values()
                                           if c.checkout == Path(project)},
                        'settings_sha256': {str(path): files.digest(files.regular(path).read_bytes())
                                            if path in present else None for path in sources}}}


def policy_differences(current, qualified):
    """Policy entries whose values differ, naming settings files individually."""
    names = []
    for key in sorted(set(current) | set(qualified)):
        if key == 'settings':
            now, then = current.get('settings') or {}, qualified.get('settings') or {}
            names += [f'settings:{path}' for path in sorted(set(now) | set(then)) if now.get(path) != then.get(path)]
        elif current.get(key) != qualified.get(key):
            names.append(key)
    return names


def harness_warnings(current, qualified):
    if current == qualified:
        return []
    return [f'Claude CLI changed since qualification: {qualified.get("version")} at {qualified.get("binary")} is now '
            f'{current.get("version")} at {current.get("binary")}. Preparation proceeds on the qualified policy; '
            'capture registration after the first reply is the positive check, and only a new support trial '
            'qualifies tool use on this CLI.']


def readiness(store, project=None):
    from berman.capture import adapters
    path = store.root / BASIS
    if not path.is_file():
        raise Decision('Claude fieldwork has no qualified per-launch hooks; prepare and qualify a berman-claude support trial')
    basis = read(path)
    if basis.get('adapter') != 'berman-claude' or basis.get('schema') not in (SCHEMA, 'berman.claude-verification/1', 'berman.claude-verification/2'):
        raise Decision('Invalid Claude fieldwork verification basis')
    if basis.get('schema') != SCHEMA:
        raise Decision('Claude qualification predates capture-only launch settings; qualify the current installation in a new support trial')
    if basis.get('implementation_sha256') != adapters.implementation('berman-claude'):
        raise Decision('Claude in-session implementation changed; qualify the current bytes in a new support trial')
    if basis.get('helpers_sha256') != helpers():
        raise Decision('Claude readiness helpers changed; qualify them in a new support trial')
    for name, digest in basis['evidence_sha256'].items():
        if files.digest(files.regular(store.root / name).read_bytes()) != digest:
            raise Decision('Claude qualification evidence changed: ' + name)
    if not basis.get('successful_capture'):
        raise Decision('Claude fieldwork needs a native capture from a support trial')
    result = {'adapter': 'berman-claude', 'events': list(EVENTS), 'basis': str(path),
              'meaning': basis['meaning'], 'tiers': basis.get('tiers', TIERS), 'warnings': []}
    if project is not None:
        current, qualified = configuration(store, Path(project)), basis['configuration']
        changed = policy_differences(current['policy'], qualified['policy'])
        if changed:
            raise Decision('Claude settings policy or hook template changed after qualification ('
                           + ', '.join(changed) + '); run a new support trial')
        result['warnings'] = harness_warnings(current['harness'], qualified['harness'])
        result['harness'] = {'qualified': qualified['harness'], 'current': current['harness']}
    return result


class Claude:
    def __init__(self, store):
        self.store = store

    def preflight(self, connection):
        return readiness(self.store, connection.checkout)

    def prepare_settings(self, run, evidence_root):
        record = self.store.record(run)
        if record['harness'].get('profile'):
            raise Decision('This preparation selected historical launch settings. Use its pinned installation.')
        expected = files.json_bytes(settings(self.store, record))
        files.keep(settings_path(self.store, run), expected)
        files.keep(evidence_root / 'claude-settings.json', expected)

    def ensure(self, run, evidence_root):
        record = self.store.record(run)
        self.store.check_installation(record)
        self.prepare_settings(run, evidence_root)
        qualified = readiness(self.store, record['project'])
        prior = evidence_root / 'readiness.json'
        result = {'passed': True, 'harness': 'claude', 'qualification': qualified,
                  'warnings': qualified['warnings'],
                  'settings_sha256': files.digest(settings_path(self.store, run).read_bytes()),
                  'configuration': configuration(self.store, record['project']),
                  'scope': 'Same qualified policy and project; exact per-launch hooks checked. No model request.'}
        reused = prior.exists() and read(prior) == result
        write(prior, result)
        return dict(result, reused=reused)

    def argv(self, run, prompt=True):
        record = self.store.record(run)
        self.store.check_installation(record)
        actual = files.regular(settings_path(self.store, run)).read_bytes()
        if actual != files.json_bytes(settings(self.store, record)):
            raise Decision('Claude launch settings differ from the reserved policy')
        argv = [str(self.store.claude), '--settings', str(settings_path(self.store, run)),
                '--add-dir', record['packet'], '--session-id', record['harness']['session_id'], '--name', f'Berman fieldwork {run}']
        if record['harness'].get('model'):
            argv += ['--model', record['harness']['model']]
        if prompt:
            argv += [files.regular(Path(record['packet']) / 'ASSIGNMENT.md').read_text()]
        return argv
