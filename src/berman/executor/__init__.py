"""Host-local executor for immutable worker assignments. Public calls return JSON receipts."""

import argparse
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
from uuid import uuid4

from berman import evidence, files, protocol, snapshots, sources
from berman.executor import capture
from berman.installation import PACKAGE

IDENTITY_FIELDS = ('collection_id', 'run', 'operation', 'connection', 'harness')


def receipt(kind, source, **fields):
    return {
        'schema': f'berman.{kind}/1',
        **{k: source[k] for k in IDENTITY_FIELDS},
        'assignment_sha256': source['assignment_sha256'],
        **fields,
    }


def check_assignment(a, binding):
    fields = {'schema', *IDENTITY_FIELDS, 'instructions', 'documents', 'checks'}
    if not isinstance(a, dict) or set(a) != fields or a['schema'] != 'berman.assignment/1':
        raise ValueError('Expected the worker-facing fields of berman.assignment/1')
    for key in ('collection_id', 'operation'):
        protocol.identifier(a[key])
    if not re.fullmatch(r'run-[0-9]{3,6}', a['run']) or not re.fullmatch(r'[a-z][a-z0-9-]*', a['connection']):
        raise ValueError('Invalid run or connection')
    if not isinstance(binding, dict) or set(binding) - {'connection', 'project', 'home', 'executable', 'policy'}:
        raise ValueError('Invalid native binding')
    if a['connection'] != binding.get('connection') or a['harness'] not in capture.EVENTS:
        raise ValueError('Commission differs from the native connection or names an unsupported harness')
    if not isinstance(a['instructions'], str) or a['instructions'].count('{{notes_directory}}') != 1:
        raise ValueError('Instructions need one {{notes_directory}}')
    if not isinstance(a['documents'], list) or not all(isinstance(s, str) and s for s in a['documents']):
        raise ValueError('Invalid document selectors')
    if not isinstance(a['checks'], list) or not all(
        isinstance(c, dict)
        and isinstance(c.get('argv'), list)
        and c['argv']
        and all(isinstance(s, str) for s in c['argv'])
        for c in a['checks']
    ):
        raise ValueError('Invalid project check argv')
    normalized = {'connection': a['connection'], 'policy': binding.get('policy')}
    for key in ('project', 'home', 'executable'):
        path = Path(binding[key]).expanduser()
        if not path.is_absolute():
            raise ValueError(f'{key} must be an absolute host path')
        normalized[key] = str(path.resolve())
    modes = (
        ('read-only', 'workspace-write', 'danger-full-access')
        if a['harness'] == 'codex'
        else ('default', 'acceptEdits', 'plan', 'dontAsk', 'auto', 'bypassPermissions')
    )
    if normalized['policy'] is not None and normalized['policy'] not in modes:
        raise ValueError('Unknown native permission mode')
    return normalized


def environment(prepared):
    key = 'CODEX_HOME' if prepared['harness'] == 'codex' else 'CLAUDE_CONFIG_DIR'
    env = dict(os.environ)
    home = prepared['binding']['home']
    default = '.codex' if prepared['harness'] == 'codex' else '.claude'
    if key in env or Path(home) != (Path.home() / default).resolve():
        env[key] = home
    return env


def fingerprint(prepared, folder):
    b = prepared['binding']
    home, project, binary = (Path(b[k]) for k in ('home', 'project', 'executable'))
    if prepared['harness'] == 'codex':
        configs = [
            home / 'config.toml',
            home / 'hooks.json',
            Path('/etc/codex/config.toml'),
            Path('/etc/codex/requirements.toml'),
        ]
        configs.extend(p / '.codex/config.toml' for p in (project, *project.parents))
    else:
        configs = [home / 'settings.json', home / 'settings.local.json', folder / 'hooks.json']
        managed = Path('/Library/Application Support/ClaudeCode')
        configs += [managed / 'managed-settings.json', *sorted((managed / 'managed-settings.d').glob('*.json'))]
        configs.extend(
            p / '.claude' / name
            for p in (project, *project.parents)
            for name in ('settings.json', 'settings.local.json')
        )
    version = subprocess.run(
        [binary, '--version'],
        cwd=project,
        env=environment(prepared),
        capture_output=True,
        text=True,
        check=True,
        timeout=20,
    ).stdout.strip()
    return {
        'executable': str(binary),
        'binary_sha256': protocol.hash_file(binary),
        'version': version,
        'python': sys.executable,
        'python_version': sys.version,
        'configuration_sha256': {str(p): protocol.hash_file(p) if p.is_file() else None for p in configs},
        'implementation_sha256': {
            str(p.relative_to(PACKAGE)): protocol.hash_file(p) for p in sorted(PACKAGE.rglob('*.py'))
        },
    }


def prepare(spool, assignment, binding):
    from berman import recipes
    from berman.checks import project_checks

    b = check_assignment(assignment, binding)
    project, home = Path(b['project']), Path(b['home'])
    spool = Path(spool).expanduser().resolve()
    if any(spool.is_relative_to(p) or p.is_relative_to(spool) for p in (project, home)):
        raise ValueError('The executor spool must be separate from the checkout and harness home')
    folder = capture.operation_dir(spool, assignment['operation'])
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    with capture.locked(folder):
        files.keep(folder / 'assignment.json', protocol.json_bytes(assignment))
        files.keep(folder / 'binding.json', files.json_bytes(b))
        if (folder / 'preparation.json').exists():
            return files.read(folder / 'preparation.json')
        if not project.is_dir() or not home.is_dir():
            raise ValueError('The selected checkout and native home must exist')
        packet = folder / 'packet'
        (packet / 'notes').mkdir(parents=True, exist_ok=True)
        instruction = assignment['instructions'].replace('{{notes_directory}}', str(packet / 'notes') + '/')
        files.keep(packet / 'INSTRUCTIONS.md', instruction.encode())
        context = recipes.snapshot(project, assignment['documents'], folder / 'initial-project')
        prompt = (
            f'{capture.MARKER}{assignment["operation"]}\n\nRead {packet / "INSTRUCTIONS.md"} '
            f'and follow it in {project}. The checkout was at {context["head"]} during preparation.'
        )
        files.keep(packet / 'ASSIGNMENT.md', prompt.encode())
        claude = assignment['harness'] == 'claude'
        files.write(folder / 'hooks.json', capture.definition(spool, home, assignment['operation'] if claude else None))
        checks = []
        with tempfile.TemporaryDirectory(dir=folder) as fixture:
            for check in project_checks(
                assignment['checks'],
                {'python': sys.executable, 'project': str(project), 'fixture': fixture, 'packet': str(packet)},
            ):
                done = subprocess.run(
                    check['argv'],
                    cwd=project,
                    timeout=check['timeout'],
                    capture_output=True,
                    env=environment(dict(assignment, binding=b)),
                )
                ok = done.returncode == 0 and check.get('stdout_contains', '').encode() in done.stdout
                checks.append(
                    dict(
                        check,
                        passed=ok,
                        returncode=done.returncode,
                        stdout_sha256=files.digest(done.stdout),
                        stderr_sha256=files.digest(done.stderr),
                    )
                )
                if not ok:
                    raise ValueError(f'Project check failed: {check["id"]} (exit {done.returncode})')
        prepared = receipt(
            'preparation',
            dict(assignment, assignment_sha256=files.digest_json(assignment)),
            binding=b,
            packet=str(packet),
            prompt=prompt,
            context=context,
            checks=checks,
            observed_at_utc=files.now(),
            reserved_session_id=str(uuid4()) if claude else None,
            instructions_sha256=files.digest(instruction.encode()),
        )
        prepared['installation'] = fingerprint(prepared, folder)
        files.write(folder / 'preparation.json', prepared)
        return prepared


def verify_hooks(installed, expected):
    from berman.capture.hooks import capture_entry

    for event in set(installed.get('hooks', {})) | set(expected['hooks']):
        found = []
        for group in installed.get('hooks', {}).get(event, []):
            for hook in group.get('hooks', []):
                argv = shlex.split(hook.get('command', ''))
                if capture_entry(hook) or any(s.startswith(('berman.executor', 'berman.capture.')) for s in argv):
                    found.append(group)
        if found != expected['hooks'].get(event, []):
            raise ValueError(f'{event}: review the exclusive executor router in the selected Codex hooks.json')


def launch_result(folder, prepared):
    def saved(name):
        path = folder / name
        return files.read(path) if path.exists() else None

    return receipt(
        'launch',
        prepared,
        intent=saved('launch-intent.json'),
        started=saved('started.json'),
        exit=saved('exit.json'),
        session_id=saved('session-id.json'),
    )


def launch(spool, operation, one_turn=False):
    folder = capture.operation_dir(spool, operation)
    with capture.locked(folder, '.launch-lock'):
        return launch_once(folder, spool, operation, one_turn)


def launch_once(folder, spool, operation, one_turn):
    with capture.locked(folder):
        p = files.read(folder / 'preparation.json')
        if (folder / 'launch-intent.json').exists():
            result = launch_result(folder, p)
            if result['intent']['one_turn'] != one_turn:
                raise ValueError('Launch already requested with another mode')
            if not (folder / 'launch.json').exists():
                files.write(folder / 'launch.json', result)
            return files.read(folder / 'launch.json')
        if (folder / 'collection').exists():
            raise ValueError('Collection already frozen')
        b = p['binding']
        packet = Path(p['packet'])
        if protocol.hash_file(files.regular(packet / 'INSTRUCTIONS.md')) != p['instructions_sha256']:
            raise ValueError('Prepared instructions changed')
        if files.regular(packet / 'ASSIGNMENT.md').read_text() != p['prompt']:
            raise ValueError('Prepared opening changed')
        claude = p['harness'] == 'claude'
        template = capture.definition(spool, b['home'], operation if claude else None)
        if files.read(folder / 'hooks.json') != template:
            raise ValueError('Prepared hook definition changed')
        if claude:
            argv = [b['executable'], '--settings', str(folder / 'hooks.json'), '--session-id', p['reserved_session_id']]
        else:
            verify_hooks(files.read(Path(b['home']) / 'hooks.json'), template)
            argv = [b['executable'], *(['exec'] if one_turn else []), '-C', b['project']]
        if claude and one_turn:
            argv += ['--print', '--max-turns', '4']
        if b.get('policy'):
            argv += ['--permission-mode' if claude else '--sandbox', b['policy']]
        argv += ['--add-dir', str(packet), '--', p['prompt']]
        intent = {
            'observed_at_utc': files.now(),
            'argv': argv,
            'one_turn': one_turn,
            'cwd': b['project'],
            'installation': fingerprint(p, folder),
        }
        files.write(folder / 'launch-intent.json', intent)
        capture.sync_directory(folder)
        try:
            child = subprocess.Popen(
                argv,
                cwd=b['project'],
                env=environment(p),
                stdin=subprocess.DEVNULL if one_turn else None,
                stdout=sys.stderr if one_turn else None,
            )
        except OSError as exc:
            files.write(folder / 'exit.json', {'error': str(exc), 'observed_at_utc': files.now()})
            raise
        files.write(folder / 'started.json', {'pid': child.pid, 'observed_at_utc': files.now()})
    code = child.wait()
    with capture.locked(folder):
        files.write(folder / 'exit.json', {'returncode': code, 'observed_at_utc': files.now()})
        result = launch_result(folder, p)
        files.write(folder / 'launch.json', result)
        return result


def members(directory):
    files.safe_directory(directory)
    for path in directory.rglob('*'):
        if path.is_symlink():
            raise ValueError(f'Symlink in evidence selection: {path}')
        if not path.is_dir():
            yield files.regular(path)


def event_summary(folder):
    """Keep only attribution and coverage fields in memory while indexing raw callbacks."""
    result = []
    for path in (folder / 'events').glob('*.json'):
        if len(result) >= protocol.MEMBER_LIMIT:
            raise ValueError('Callback count exceeds collection member limit')
        record = files.read(path)
        event = record['event']
        result.append(
            {
                'name': event['hook_event_name'],
                'at': record['observed_at_utc'],
                'transcript_path': event.get('transcript_path'),
                'generation': record.get('generation'),
                'permission_mode': event.get('permission_mode'),
            }
        )
    return sorted(result, key=lambda r: r['at'])


def collect(spool, operation, ending_id):
    """Freeze an observation for one controller ending, including explicit coverage gaps."""
    from berman import recipes

    protocol.identifier(ending_id)
    folder = capture.operation_dir(spool, operation)
    collection = folder / 'collection'
    with capture.locked(folder):
        if collection.exists():
            manifest = files.read(collection / 'manifest.json')
            if manifest['ending_id'] != ending_id:
                raise ValueError('Collection already frozen for another ending')
            for item in manifest['members']:
                verify_payload(collection, item)
            return manifest
        p = files.read(folder / 'preparation.json')
        b = p['binding']
        launched = launch_result(folder, p)
        sid = launched['session_id']
        limitations = []
        if not (Path(p['packet']) / 'notes').is_dir():
            limitations.append('Assigned notes directory unavailable.')
        if not launched['exit']:
            limitations.append('Native process exit was not observed before collection.')
        with tempfile.TemporaryDirectory(dir=folder, prefix='.collect-') as temporary:
            stage = Path(temporary)
            frozen = stage / 'frozen'
            payload = frozen / 'payload'
            payload.mkdir(parents=True)
            events = event_summary(folder)
            if sid:
                supplied = next((r['transcript_path'] for r in reversed(events) if r['transcript_path']), None)
                try:
                    capture.observe(folder, p, sid, {'hook_event_name': 'closeout', 'transcript_path': supplied})
                except (OSError, ValueError) as exc:
                    limitations.append(f'Closeout parent observation: {exc}')
                try:
                    associated = sources.collect(Path(b['home']), sid, p['harness'], stage / 'associated')
                    if not (Path(b['home']) / ('projects' if p['harness'] == 'claude' else 'sessions')).is_dir():
                        limitations.append('Associated session store unavailable.')
                except (OSError, ValueError) as exc:
                    limitations.append(f'Associated session discovery: {exc}')
                    associated = None
            else:
                associated = None
                limitations.append('No native parent was attributed. Associated discovery skipped.')
            latest = snapshots.read(folder / 'session')
            event_names = [r['name'] for r in events]
            if not latest:
                limitations.append('No verified parent transcript observed.')
            else:
                snapshots.recover(folder / 'session', folder / 'scratch')
                meta = latest['metadata']
                if meta['omitted_partial_tail_bytes'] or meta['unparsed_jsonl_lines']:
                    limitations.append('Parent transcript contains partial or unparsed records.')
                if meta['last_assistant_message_present'] is False:
                    limitations.append('Expected assistant message absent from the latest parent observation.')
            if any(not r['generation'] for r in events):
                limitations.append('Some callbacks lack a verified transcript generation; see events.')
            if 'Stop' not in event_names:
                limitations.append('No successful Stop callback observed.')
            if event_names and event_names[-1] == 'UserPromptSubmit':
                limitations.append('Submitted prompt has no later terminal callback.')
            try:
                recipes.snapshot(
                    Path(b['project']), p['context']['selectors'], stage / 'final-project', require_stable_git=False
                )
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                limitations.append(f'Final project snapshot: {exc}')

            rows = []
            total = 0

            def retain(name, src):
                nonlocal total
                evidence.relative(name)
                if len(rows) >= protocol.MEMBER_LIMIT:
                    raise ValueError('Collection exceeds member count limit')
                data = capture.stable_bytes(src, min(protocol.FILE_LIMIT, protocol.TOTAL_LIMIT - total))
                files.keep(payload / name, data)
                rows.append({'path': name, 'size': len(data), 'sha256': files.digest(data)})
                total += len(data)

            files.write(stage / 'launch.json', launched)
            retain('launch.json', stage / 'launch.json')
            for name in ('assignment.json', 'preparation.json', 'hooks.json'):
                retain(name, folder / name)
            selection = [(folder / name, name) for name in ('initial-project', 'events', 'packet')]
            if latest:
                selection.append((folder / 'session', 'session'))
            for name in ('associated', 'final-project'):
                if (stage / name).is_dir():
                    selection.append((stage / name, name))
            for root, prefix in selection:
                if root.exists():
                    for path in members(root):
                        retain(prefix + '/' + path.relative_to(root).as_posix(), path)
            manifest = receipt(
                'collection',
                p,
                observed_at_utc=files.now(),
                ending_id=ending_id,
                session_id=sid,
                limitations=limitations,
                members=sorted(rows, key=lambda row: row['path']),
                events=event_names,
                parent_sha256=latest['metadata']['sha256'] if latest else None,
                associated_sessions=[s['session_id'] for s in associated['sources']] if associated else None,
            )
            files.write(frozen / 'manifest.json', manifest)
            frozen.rename(collection)
            capture.sync_directory(folder)
            return manifest


def verify_payload(collection, row):
    path = files.regular(collection / 'payload' / evidence.relative(row['path']))
    if path.stat().st_size != row['size'] or protocol.hash_file(path) != row['sha256']:
        raise ValueError(f'Frozen payload differs: {row["path"]}')
    return path


def payload_path(spool, operation, ending_id, path):
    collection = capture.operation_dir(spool, operation) / 'collection'
    manifest = files.read(collection / 'manifest.json')
    if manifest['ending_id'] != protocol.identifier(ending_id):
        raise ValueError('Different ending identity')
    evidence.relative(path)
    for row in manifest['members']:
        if row['path'] == path:
            return verify_payload(collection, row)
    raise ValueError(f'Unlisted collection member: {path}')


def qualify(spool, operation, ending_id, expected_child=None):
    """Evaluate this launch against its own staged evidence. A support trial may name a known child."""
    collection = capture.operation_dir(spool, operation) / 'collection'
    manifest = files.read(collection / 'manifest.json')
    if manifest['ending_id'] != protocol.identifier(ending_id):
        raise ValueError('Different ending identity')
    for row in manifest['members']:
        verify_payload(collection, row)
    payload = collection / 'payload'
    prepared = files.read(payload / 'preparation.json')
    launched = files.read(payload / 'launch.json')
    session = snapshots.read(payload / 'session')
    events = event_summary(payload)
    modes = set()
    if session and prepared['harness'] == 'codex':
        for line in session['raw'].splitlines():
            try:
                record = json.loads(line)
            except ValueError, UnicodeError:
                continue
            if isinstance(record, dict) and record.get('type') == 'turn_context':
                context = record.get('payload')
                value = context.get('sandbox_policy') if isinstance(context, dict) else None
                if isinstance(value, dict) and isinstance(value.get('type'), str):
                    modes.add(value['type'])
    elif prepared['harness'] == 'claude':
        modes = {
            e['permission_mode'] for e in events if isinstance(e.get('permission_mode'), str) and e['permission_mode']
        }
    policy = prepared['binding']['policy']
    checks = {
        'native_launch': bool(launched['started']) and (launched['exit'] or {}).get('returncode') == 0,
        'project_observed': bool(prepared['context']['head']),
        'project_checks': all(c['passed'] for c in prepared['checks']),
        'offline_hook_snapshot': any(event['generation'] for event in events),
        'closeout_parent_observed': bool(session and session['metadata']['event'] == 'closeout'),
        'opening_and_stop': {'UserPromptSubmit', 'Stop'} <= set(manifest['events']),
        'attributed_transcript': bool(session and session['metadata']['session_id'] == manifest['session_id']),
        'native_policy_observed': bool(modes) and (policy is None or modes == {policy}),
        'capture_intact': bool(
            session
            and not session['tail']
            and not session['metadata']['unparsed_jsonl_lines']
            and session['metadata']['last_assistant_message_present'] is not False
        ),
    }
    if expected_child is not None:
        checks['expected_associated_session'] = expected_child in (manifest['associated_sessions'] or [])
    return receipt(
        'native-qualification',
        prepared,
        ending_id=ending_id,
        session_id=manifest['session_id'],
        passed=all(checks.values()),
        checks=checks,
        observed_policy=sorted(modes),
        expected_child=expected_child,
        limitations=manifest['limitations'],
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--spool', type=Path, required=True)
    commands = parser.add_subparsers(dest='action', required=True)
    prepare_cmd = commands.add_parser('prepare')
    prepare_cmd.add_argument('assignment', type=Path)
    prepare_cmd.add_argument('binding', type=Path)
    for action in ('launch', 'collect', 'qualify', 'payload-path'):
        cmd = commands.add_parser(action)
        cmd.add_argument('operation')
        if action == 'launch':
            cmd.add_argument('--one-turn', action='store_true')
        else:
            cmd.add_argument('ending_id')
        if action == 'qualify':
            cmd.add_argument('--expected-child')
        if action == 'payload-path':
            cmd.add_argument('path')
    hook = commands.add_parser('hook')
    hook.add_argument('--home', type=Path, required=True)
    hook.add_argument('--operation')
    template = commands.add_parser('codex-hooks')
    template.add_argument('--home', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.action == 'prepare':
            result = prepare(args.spool, files.read(args.assignment.resolve()), files.read(args.binding.resolve()))
        elif args.action == 'launch':
            result = launch(args.spool, args.operation, one_turn=args.one_turn)
        elif args.action == 'collect':
            result = collect(args.spool, args.operation, args.ending_id)
        elif args.action == 'qualify':
            result = qualify(args.spool, args.operation, args.ending_id, args.expected_child)
        elif args.action == 'payload-path':
            result = str(payload_path(args.spool, args.operation, args.ending_id, args.path))
        elif args.action == 'codex-hooks':
            result = capture.definition(args.spool, args.home)
        else:
            data = sys.stdin.buffer.read(capture.MAX_EVENT + 1)
            if len(data) > capture.MAX_EVENT:
                raise ValueError('Callback exceeds byte limit')
            result = capture.route(args.spool, args.home, json.loads(data), args.operation)
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as exc:
        print(f'Executor: {exc}', file=sys.stderr)
        if args.action == 'hook':
            print(json.dumps({'systemMessage': f'Berman capture: {exc}'}))
            return 0
        return 1
    print(json.dumps(result))
    return 1 if args.action == 'qualify' and not result['passed'] else 0
