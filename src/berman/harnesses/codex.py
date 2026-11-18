"""Codex fieldwork launch and native checkout diagnostics."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import uuid
import xml.etree.ElementTree as ET

from berman.capture import adapters
from berman import files
from berman.files import Decision, keep, read, write
from berman.installation import PACKAGE

COMMON_CHECKS = {'project_read', 'workspace_write', 'cat_positive_control', 'rg_positive_control', 'git_read',
                 'git_disposable_commit', 'assigned_instructions_readable', 'assigned_notes_writable'}
PLACEHOLDER = re.compile(r'\{(python|project|fixture|packet|executable:[a-z][a-z0-9-]*)\}')


def launch_options(record):
    if record['harness'].get('profile'):
        raise Decision('This preparation selected a historical Berman profile. Use its pinned installation.')
    return ['-C', record['project'], '--add-dir', record['packet']]


def permission_context(output):
    """Take the harness's environment message, which includes its rendered permission profile."""
    messages = json.loads(output)
    if not isinstance(messages, list):
        raise ValueError('Codex prompt input must be a message list')
    contexts = [block['text'] for message in messages if isinstance(message, dict) and message.get('role') == 'user'
                for block in message.get('content', []) if isinstance(block, dict)
                and isinstance(block.get('text'), str) and block['text'].startswith('<environment_context>')]
    if len(contexts) != 1:
        raise ValueError('Codex did not report one unambiguous environment context')
    return contexts[0]


def project_checks(declared, values):
    """Resolve a connection's declared checks into explicit argument lists."""
    resolved = []
    reserved = COMMON_CHECKS | {'shell_exit_zero', 'launch_policy_observed', 'configuration_unchanged',
                                'file_project_read', 'file_packet_read', 'exact_execution_probe_observed'}
    for check in declared:
        ident = check['id']
        if ident in reserved or not re.fullmatch(r'[a-z][a-z0-9_]{0,62}', ident):
            raise ValueError(f'Invalid project check identity: {ident}')
        reserved.add(ident)

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
        if (not os.access(self.python, os.X_OK) or not os.access(self.store.runtime_python, os.X_OK)
                or not connection.checkout.is_dir()):
            raise Decision(f'Configured Python or the {connection.id} checkout is unavailable')
        self.invoke([str(self.binary), '--version'], connection.checkout, check=True, timeout=20)
        return hooks

    def hooks(self, adapter):
        return adapters.readiness(self.store, adapter)

    def fingerprint(self, run, project, adapter, checks):
        store = self.store
        version = self.invoke([str(self.binary), '--version'], project, check=True, timeout=20).stdout.strip()
        paths = [store.home / 'config.toml', store.home / 'hooks.json',
                 Path('/etc/codex/config.toml'), Path('/etc/codex/requirements.toml'),
                 PACKAGE / 'harnesses/codex.py', PACKAGE / 'harnesses/probe.py',
                 *(PACKAGE / rel for rel in adapters.ADAPTERS[adapter]['implementation'])]
        paths += [p / '.codex/config.toml' for p in (project, *project.parents)]
        paths += [rule for folder in {p.parent / 'rules' for p in paths if p.suffix == '.toml'}
                  for rule in sorted(folder.glob('*.rules'))]
        st = self.binary.resolve().stat()
        return {'codex': str(self.binary.resolve()), 'version': version, 'size': st.st_size,
                'mtime_ns': st.st_mtime_ns, 'python': str(self.python.resolve()),
                'project': str(project), 'packet': str(store.field / run), 'capture_adapter': adapter,
                'project_checks': checks,
                'files': {str(p): files.digest(p.read_bytes()) if p.is_file() else None for p in paths}}

    def ensure(self, run, evidence_root):
        store = self.store
        record, project, adapter, checks = self.context(run)
        base = [str(self.binary), *launch_options(record)]
        hooks = self.hooks(adapter)
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
                  'scope': 'Host checkout, tools and packet, with Codex rendering the launch policy. Live tool use requires a session.',
                  'evidence': str(evidence)}
        try:
            with tempfile.TemporaryDirectory(prefix='.berman-check-', dir=project) as temporary:
                fixture = Path(temporary)
                shutil.copyfile(PACKAGE / 'harnesses/probe.py', fixture / 'probe.py')
                rg = shutil.which('rg')
                if not rg:
                    raise Decision('The native check needs the existing rg executable')
                values = {'python': str(self.python), 'project': str(project), 'fixture': str(fixture), 'packet': str(packet)}
                payload = {'repo': str(project), 'packet': str(packet), 'fixture': str(fixture), 'token': token,
                           'rg': rg,
                           'reads': [d for d in record['documents'] if (project / d).is_file()],
                           'project_checks': project_checks(checks, values)}
                write(fixture / 'spec.json', payload)
                argv = [str(self.python), '-I', '-B', str(fixture / 'probe.py'), str(fixture / 'spec.json')]
                write(evidence / 'invocation.json', argv)
                write(evidence / 'spec.json', payload)
                p = self.invoke(argv, project, timeout=max(90, 30 + sum(c['timeout'] for c in payload['project_checks'])))
                keep(evidence / 'shell.stdout', p.stdout.encode())
                keep(evidence / 'shell.stderr', p.stderr.encode())
                observed = json.loads(p.stdout.splitlines()[-1])
                expected = COMMON_CHECKS | {c['id'] for c in payload['project_checks']}
                if (not isinstance(observed, dict) or not isinstance(observed.get('checks'), dict)
                        or observed.get('schema') != 'berman.execution-probe/1' or observed.get('token') != token or set(observed['checks']) != expected
                        or any(type(v) is not bool for v in observed['checks'].values())):
                    raise Decision('Native probe result is incomplete or belongs to another invocation')
                checks_seen = observed['checks']
                checks_seen['shell_exit_zero'] = p.returncode == 0
                policy_argv = [*base, 'debug', 'prompt-input']
                write(evidence / 'policy-invocation.json', policy_argv)
                p = self.invoke(policy_argv, project, timeout=60)
                context = permission_context(p.stdout) if p.returncode == 0 else ''
                keep(evidence / 'permission-context.txt', context.encode())
                keep(evidence / 'context.stderr', p.stderr.encode())
                environment = ET.fromstring(context)
                policy = environment.find('filesystem/permission_profile')
                checks_seen['launch_policy_observed'] = (
                    environment.tag == 'environment_context' and environment.findtext('cwd') == str(project)
                    and str(packet) in [node.text for node in environment.findall('filesystem/workspace_roots/root')]
                    and policy is not None and bool(policy.get('type')))
                checks_seen['configuration_unchanged'] = before == self.fingerprint(run, project, adapter, checks)
                result.update(checks=checks_seen, passed=all(checks_seen.values()))
        except (OSError, ValueError, IndexError, ET.ParseError, subprocess.SubprocessError) as exc:
            result['error'] = f'{type(exc).__name__}: {exc}'
        write(evidence / 'result.json', result)
        write(receipt, result)
        if not result['passed']:
            raise Decision(f'Native access check did not pass. Details: {evidence / "result.json"}')
        return dict(result, reused=False)

    def argv(self, run):
        record, project, adapter, checks = self.context(run)
        return [str(self.binary), *launch_options(record),
                files.regular(self.store.field / run / 'ASSIGNMENT.md').read_text()]


class Harnesses:
    """Select through the reservation so changing a connection never changes an existing run."""
    def __init__(self, store):
        self.store = store

    def selected(self, adapter):
        if adapter == 'berman-claude':
            from berman.harnesses.claude import Claude
            return Claude(self.store)
        return Codex(self.store)

    def preflight(self, connection):
        return self.selected(connection.capture).preflight(connection)

    def ensure(self, run, evidence_root):
        return self.selected(self.store.adapter(self.store.record(run))).ensure(run, evidence_root)

    def argv(self, run):
        return self.selected(self.store.adapter(self.store.record(run))).argv(run)
