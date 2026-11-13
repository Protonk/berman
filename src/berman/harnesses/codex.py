"""Codex fieldwork access, reusable native checks, and launch verification.

Common access checks cover Berman, the shared packet root, an unassigned packet location and the
assigned-packet exception. A connection's own project checks are declared as data in its
description and run inside the same sandbox invocation.
"""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import uuid

import berman_capture
import berman_files as files
from berman_files import Decision, keep, read, write
from berman_records import ROOT

PROTECTED = ('.git', '.agents', '.codex', '.aws')
CANARIES = ('berman', 'unassigned_packet')
COMMON_CHECKS = {'project_read', 'workspace_write', 'cat_positive_control', 'rg_positive_control', 'git_read',
                 'git_disposable_commit', 'assigned_instructions_readable', 'assigned_notes_writable',
                 *(f'{kind}_{label}' for label in CANARIES for kind in (
                     'python_read_denied', 'cat_read_denied', 'recursive_search_denied', 'symlink_read_denied'))}
PLACEHOLDER = re.compile(r'\{(python|project|fixture|packet|executable:[a-z][a-z0-9-]*)\}')


def profile_name(store, run):
    """Distinct per workspace and run, so another workspace or connection cannot replace it."""
    return f'berman-{store.tag}-{run}'


def profile(store, run, project):
    permission = f'berman_{store.tag}_{run.replace("-", "_")}'
    access = {str(store.root): 'deny', str(store.field): 'deny', str(store.field / run): 'write'}
    access.update({str(project / name): 'read' for name in PROTECTED})
    return '\n'.join([
        '# Berman fieldwork profile; selected only by its explicit profile name.',
        f'default_permissions = "{permission}"', '', '[tui]',
        'terminal_title = ["app-name", "thread", "project"]', '',
        f'[permissions.{permission}]', 'extends = ":workspace"', '',
        f'[permissions.{permission}.filesystem]',
        *(f'{json.dumps(path)} = {json.dumps(mode)}' for path, mode in access.items()), ''])


def permission_context(output):
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


def project_checks(declared, values):
    """Resolve a connection's declared checks into explicit argument lists."""
    resolved = []
    for check in declared:
        ident = check['id']
        if ident in COMMON_CHECKS or not re.fullmatch(r'[a-z][a-z0-9_]{0,62}', ident):
            raise ValueError(f'Invalid project check identity: {ident}')

        def substitute(match):
            key = match.group(1)
            if key.startswith('executable:'):
                found = shutil.which(key.split(':', 1)[1])
                if not found:
                    raise Decision(f'The native check needs the {key.split(":", 1)[1]} executable for {ident}')
                return found
            return values[key]
        argv = [PLACEHOLDER.sub(substitute, str(arg)) for arg in check['argv']]
        if any(re.search(r'\{[^}]*\}', arg) for arg in argv):
            raise ValueError(f'Unknown placeholder in project check {ident}')
        timeout = int(check.get('timeout', 30))
        if not 0 < timeout <= 120:
            raise ValueError(f'Project check {ident} needs a timeout between 1 and 120 seconds')
        row = {'id': ident, 'argv': argv, 'timeout': timeout}
        if 'stdout_contains' in check:
            row['stdout_contains'] = str(check['stdout_contains'])
        resolved.append(row)
    return resolved


class Codex:
    def __init__(self, store):
        self.store = store
        self.binary = store.codex
        self.python = store.python

    def context(self, run):
        """Everything a check needs resolves through the run record, not the current default."""
        record = self.store.record(run)
        if not record:
            raise Decision(f'{run} has no operation record')
        self.store.check_installation(record)
        if record.get('connection'):
            checks = record['connection']['checks']
        else:
            checks = self.store.connection(self.store.legacy_connection).checks()
        return record, Path(record['project']), self.store.adapter(record), checks

    def invoke(self, argv, cwd, **kwargs):
        return subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                              env=dict(os.environ, CODEX_HOME=str(self.store.home)), **kwargs)

    def preflight(self, connection):
        """Checks that do not depend on a new packet, before ending another engagement."""
        hooks = self.hooks(connection.capture)
        if not os.access(self.python, os.X_OK) or not connection.checkout.is_dir():
            raise Decision(f'Configured Python or the {connection.id} checkout is unavailable')
        self.invoke([str(self.binary), '--version'], connection.checkout, check=True, timeout=20)
        return hooks

    def hooks(self, adapter):
        return berman_capture.readiness(self.store, adapter)

    def fingerprint(self, run, project, adapter, checks):
        store = self.store
        version = self.invoke([str(self.binary), '--version'], project, check=True, timeout=20).stdout.strip()
        paths = [store.home / 'config.toml', store.home / 'hooks.json',
                 store.home / (profile_name(store, run) + '.config.toml'),
                 Path('/etc/codex/config.toml'), Path('/etc/codex/requirements.toml'),
                 ROOT / 'tools/berman_harness.py', ROOT / 'tools/berman_probe.py',
                 *(ROOT / rel for rel in berman_capture.ADAPTERS[adapter]['implementation'])]
        paths += [p / '.codex/config.toml' for p in (project, *project.parents)]
        st = self.binary.resolve().stat()
        return {'codex': str(self.binary.resolve()), 'version': version, 'size': st.st_size,
                'mtime_ns': st.st_mtime_ns, 'python': str(self.python.resolve()),
                'project': str(project), 'packet': str(store.field / run), 'capture_adapter': adapter,
                'project_checks': checks,
                'files': {str(p): files.digest(p.read_bytes()) if p.is_file() else None for p in paths}}

    def install_profile(self, run, project, evidence_root):
        config = profile(self.store, run, project).encode()
        # A bounded, resource-specific lock for the shared harness home; no check runs under it.
        with self.store.lock('installation', 'codex-profiles', wait=10):
            keep(self.store.home / (profile_name(self.store, run) + '.config.toml'), config)
        keep(evidence_root / 'profile.config.toml', config)

    def ensure(self, run, evidence_root):
        record, project, adapter, checks = self.context(run)
        self.install_profile(run, project, evidence_root)
        return self.access_check(run, evidence_root, self.hooks(adapter))

    def access_check(self, run, evidence_root, hooks=None):
        store = self.store
        record, project, adapter, checks = self.context(run)
        packet = store.field / run
        if record.get('packet', str(packet)) != str(packet) or packet.parent != store.field or packet.is_symlink():
            raise Decision(f'{run}: the assigned packet must be directly within the shared packet root {store.field}')
        before = self.fingerprint(run, project, adapter, checks)
        receipt = evidence_root / 'readiness.json'
        if receipt.exists():
            saved = read(receipt)
            if not isinstance(saved, dict):
                raise Decision(f'Invalid native verification receipt: {receipt}')
            if saved.get('passed') is True and saved.get('fingerprint') == before:
                return dict(saved, reused=True)
        evidence = evidence_root / ('checks/' + uuid.uuid4().hex)
        evidence.mkdir(parents=True)
        token = 'berman-probe-' + uuid.uuid4().hex
        result = {'passed': False, 'fingerprint': before, 'hooks': hooks, 'checked_at_utc': files.now(),
                  'scope': 'Native sandbox and rendered permission context; no model request or fieldwork registration.',
                  'evidence': str(evidence)}
        try:
            with tempfile.TemporaryDirectory(prefix='.berman-check-', dir=project) as temporary, \
                 tempfile.TemporaryDirectory(prefix='.berman-unassigned-', dir=store.field) as other:
                fixture = Path(temporary)
                canaries = {'berman': evidence / 'denied-marker.txt', 'unassigned_packet': Path(other) / 'denied-marker.txt'}
                for path in canaries.values():
                    path.write_text(token + '\n')
                subprocess.run(['git', 'init', '-q', str(fixture / 'git-probe')], check=True, capture_output=True)
                shutil.copyfile(ROOT / 'tools/berman_probe.py', fixture / 'probe.py')
                rg = shutil.which('rg')
                if not rg:
                    raise Decision('The native check needs the existing rg executable')
                values = {'python': str(self.python), 'project': str(project), 'fixture': str(fixture), 'packet': str(packet)}
                payload = {'repo': str(project), 'packet': str(packet), 'fixture': str(fixture), 'token': token,
                           'canaries': {k: str(v) for k, v in canaries.items()}, 'rg': rg,
                           'reads': [d for d in record['documents'] if (project / d).is_file()],
                           'project_checks': project_checks(checks, values)}
                write(fixture / 'spec.json', payload)
                base = [str(self.binary), '-p', profile_name(store, run), '-C', str(project)]
                argv = [*base, 'sandbox', '--', str(self.python), '-I', '-B', str(fixture / 'probe.py'), str(fixture / 'spec.json')]
                write(evidence / 'invocation.json', argv)
                write(evidence / 'spec.json', payload)
                p = self.invoke(argv, project, timeout=max(90, 30 + sum(c['timeout'] for c in payload['project_checks'])))
                keep(evidence / 'shell.stdout', p.stdout.encode())
                keep(evidence / 'shell.stderr', p.stderr.encode())
                observed = json.loads(p.stdout.splitlines()[-1])
                expected = COMMON_CHECKS | {c['id'] for c in payload['project_checks']}
                if (not isinstance(observed, dict) or not isinstance(observed.get('checks'), dict)
                        or observed.get('token') != token or set(observed['checks']) != expected
                        or any(type(v) is not bool for v in observed['checks'].values())):
                    raise Decision('Native probe result is incomplete or belongs to another invocation')
                checks_seen = observed['checks']
                checks_seen['shell_exit_zero'] = p.returncode == 0
                p = self.invoke([*base, 'debug', 'prompt-input'], project, timeout=60)
                context = permission_context(p.stdout) if p.returncode == 0 else ''
                keep(evidence / 'permission-context.txt', context.encode())
                keep(evidence / 'context.stderr', p.stderr.encode())
                checks_seen['rendered_denials'] = all(f'<entry access="deny" escalatable="false"><path>{path}</path></entry>' in context
                                                      for path in (store.root, store.field))
                checks_seen['rendered_packet_write'] = f'<entry access="write"><path>{packet}</path></entry>' in context
                checks_seen['protected_configuration_paths'] = all(f'<entry access="read"><path>{project / name}</path></entry>' in context
                                                                   for name in PROTECTED)
                checks_seen['configuration_unchanged'] = before == self.fingerprint(run, project, adapter, checks)
                result.update(checks=checks_seen, passed=all(checks_seen.values()))
        except (OSError, ValueError, IndexError, subprocess.SubprocessError) as exc:
            result['error'] = f'{type(exc).__name__}: {exc}'
        write(evidence / 'result.json', result)
        write(receipt, result)
        if not result['passed']:
            raise Decision(f'Native access check did not pass. Details: {evidence / "result.json"}')
        return dict(result, reused=False)

    def argv(self, run):
        record, project, adapter, checks = self.context(run)
        return [str(self.binary), '-p', profile_name(self.store, run), '-C', str(project),
                files.regular(self.store.field / run / 'ASSIGNMENT.md').read_text()]


class Harnesses:
    """Select through the reservation so changing a connection never changes an existing run."""
    def __init__(self, store):
        self.store = store

    def selected(self, adapter):
        if adapter == 'berman-claude':
            from berman_claude import Claude
            return Claude(self.store)
        return Codex(self.store)

    def preflight(self, connection):
        return self.selected(connection.capture).preflight(connection)

    def ensure(self, run, evidence_root):
        return self.selected(self.store.adapter(self.store.record(run))).ensure(run, evidence_root)

    def argv(self, run):
        return self.selected(self.store.adapter(self.store.record(run))).argv(run)
