"""Host-local executor for immutable worker assignments. Public calls return JSON receipts.

An engagement can have several sittings: one native process each, the later ones resuming the
bound parent session. Collection freezes what the host holds for one controller declaration and
records what it could not take as an omission; it does not fail the whole collection for one member.
"""

import argparse
import json
import os
import stat
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
        else ('default', 'manual', 'acceptEdits', 'plan', 'dontAsk', 'auto', 'bypassPermissions')
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


def launch_result(folder, prepared, number=1):
    """The receipt for one sitting. The first keeps its original shape; later ones name their number.

    Once a sitting has an exit its receipt is frozen on disk and returned as written. Before
    that it is derived, and says the exit is not yet observed.
    """
    paths = capture.sitting_files(folder, number)
    if paths['receipt'].exists():
        frozen = files.read(paths['receipt'])
        if frozen.get('exit') is not None:
            return frozen

    def saved(path):
        return files.read(path) if path.exists() else None

    return receipt(
        'launch',
        prepared,
        **({'sitting': number} if number > 1 else {}),
        intent=saved(paths['intent']),
        started=saved(paths['started']),
        exit=saved(paths['exit']),
        session_id=saved(folder / 'session-id.json'),
    )


def close_sitting(folder, prepared, number, outcome):
    """Record how a sitting ended and freeze its receipt. Later state never changes that receipt."""
    paths = capture.sitting_files(folder, number)
    if not paths['exit'].exists():
        files.write(paths['exit'], outcome)
    if not paths['receipt'].exists() or files.read(paths['receipt']).get('exit') is None:
        files.write(paths['receipt'], launch_result(folder, prepared, number))
    capture.sync_directory(paths['receipt'].parent)
    return files.read(paths['receipt'])


def sitting_receipts(spool, operation):
    """Every sitting's receipt, for the connector to report. Read-only."""
    folder = capture.operation_dir(spool, operation)
    with capture.locked(folder):
        prepared = files.read(folder / 'preparation.json')
        return [launch_result(folder, prepared, number) for number in capture.sittings(folder)]


def process_identity(pid):
    """When this pid's process started, as the system reports it. A reused pid reports another time."""
    try:
        done = subprocess.run(['ps', '-o', 'lstart=', '-p', str(pid)], capture_output=True, text=True, timeout=10)
    except OSError, subprocess.SubprocessError:
        return None
    return done.stdout.strip() or None


def settle(folder, prepared, number):
    """Close a sitting whose launcher is gone, saying what was and was not observed.

    The caller holds the launch lock, which every launcher keeps until its child exits. So no
    launcher is waiting on this sitting. The recorded process counts as still running only if
    the same pid has the start time recorded for it; a reused pid is another process.
    """
    paths = capture.sitting_files(folder, number)
    if paths['exit'].exists():
        return close_sitting(folder, prepared, number, None)
    started = files.read(paths['started']) if paths['started'].exists() else None
    if started is None:
        note = 'The launcher ended before a native process was observed to start.'
    else:
        pid, known = started.get('pid'), started.get('process_started')
        now = process_identity(pid) if pid else None
        if now and (known is None or now == known):
            which = 'is still running' if known else 'may still be running (an older record without its start time)'
            raise ValueError(
                f'Sitting {number} has no recorded exit and its process {pid} {which} without its launcher. '
                'Check that it is the native session, end it, then retry.'
            )
        note = 'The launcher ended before it observed the native process exit.'
    return close_sitting(
        folder, prepared, number, {'returncode': None, 'unobserved': note, 'observed_at_utc': files.now()}
    )


def launch(spool, operation, one_turn=False, resume=False, prompt=None, request=None):
    """Start the first sitting, or with resume=True the next one.

    Repeating a plain launch never spawns again. A resumed sitting carries the caller's
    `request` label, and repeating a resume with the same label returns that sitting.
    """
    if prompt is not None and (not isinstance(prompt, str) or not prompt.strip() or prompt.startswith('-')):
        raise ValueError('A sitting prompt is non-empty text that does not begin with a dash')
    if resume and (not isinstance(request, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}', request)):
        raise ValueError('A resumed sitting needs a short request label; reuse it when repeating the command')
    folder = capture.operation_dir(spool, operation)
    with capture.locked(folder, '.launch-lock'):
        return launch_once(folder, spool, operation, one_turn, resume, prompt, request)


def launch_once(folder, spool, operation, one_turn, resume, prompt, request):
    with capture.locked(folder):
        p = files.read(folder / 'preparation.json')
        numbers = capture.sittings(folder)
        if numbers:
            settle(folder, p, numbers[-1])
            if not resume:
                result = launch_result(folder, p, 1)
                if result['intent'] and result['intent'].get('one_turn') != one_turn:
                    raise ValueError('Launch already requested with another mode')
                return result
            for number in numbers:
                earlier = launch_result(folder, p, number)
                if (earlier['intent'] or {}).get('request') == request:
                    return earlier
        elif resume:
            raise ValueError('Nothing to resume: launch the first sitting')
        number = numbers[-1] + 1 if numbers else 1
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
        bound = files.read(folder / 'session-id.json') if (folder / 'session-id.json').exists() else None
        policy = b.get('policy')
        if claude:
            # Claude resumes its reserved session only once a native transcript exists for it.
            native = False
            if resume:
                try:
                    capture.find_parent(Path(b['home']), 'claude', p['reserved_session_id'])
                    native = True
                except OSError, ValueError:
                    native = False
            argv = [b['executable'], '--settings', str(folder / 'hooks.json')]
            argv += ['--resume' if native else '--session-id', p['reserved_session_id']]
            if one_turn:
                argv += ['--print']
            if policy:
                argv += ['--permission-mode', policy]
            argv += ['--add-dir', str(packet)]
            text = prompt if native else p['prompt']
            if not native and prompt:
                raise ValueError(
                    'No native session exists yet. This sitting opens with the prepared prompt; omit --prompt.'
                )
            if one_turn and not text:
                raise ValueError('A one-turn resumed sitting needs --prompt')
            if text:
                argv += ['--', text]
        else:
            verify_hooks(files.read(Path(b['home']) / 'hooks.json'), template)
            native = bool(resume and bound)
            options = ['-C', b['project'], *(['--sandbox', policy] if policy else []), '--add-dir', str(packet)]
            if native and one_turn:
                if not prompt:
                    raise ValueError('A one-turn resumed sitting needs --prompt')
                argv = [b['executable'], 'exec', *options, 'resume', bound, prompt]
            elif native:
                argv = [b['executable'], 'resume', *options, bound, *([prompt] if prompt else [])]
            else:
                if prompt:
                    raise ValueError(
                        'No native session is bound yet. This sitting opens with the prepared prompt; omit --prompt.'
                    )
                argv = [b['executable'], *(['exec'] if one_turn else []), *options, '--', p['prompt']]
        intent = {
            'observed_at_utc': files.now(),
            'argv': argv,
            'one_turn': one_turn,
            'cwd': b['project'],
            'installation': fingerprint(p, folder),
        }
        if number > 1:
            intent.update(resumed_native_session=native, request=request)
        paths = capture.sitting_files(folder, number)
        files.write(paths['intent'], intent)
        for directory in (paths['intent'].parent, paths['intent'].parent.parent, folder):
            capture.sync_directory(directory)
        try:
            child = subprocess.Popen(
                argv,
                cwd=b['project'],
                env=environment(p),
                stdin=subprocess.DEVNULL if one_turn else None,
                stdout=sys.stderr if one_turn else None,
            )
        except OSError as exc:
            close_sitting(folder, p, number, {'error': str(exc), 'observed_at_utc': files.now()})
            raise
        files.write(
            paths['started'],
            {'pid': child.pid, 'process_started': process_identity(child.pid), 'observed_at_utc': files.now()},
        )
    code = child.wait()
    with capture.locked(folder):
        return close_sitting(folder, p, number, {'returncode': code, 'observed_at_utc': files.now()})


def members(directory):
    """Yield (path, problem) for everything under a directory. Only regular files have no problem.

    A directory that cannot be listed and an entry that cannot be examined are yielded with
    their problem, so the collection names them instead of passing over them.
    """
    files.safe_directory(directory)
    unreadable = []
    for parent, folders, names in os.walk(directory, onerror=unreadable.append, followlinks=False):
        folders.sort()
        for name in sorted(folders + names):
            path = Path(parent) / name
            try:
                mode = path.lstat().st_mode
            except OSError as exc:
                yield path, ('unreadable', exc.strerror or str(exc))
                continue
            if stat.S_ISLNK(mode):
                yield path, ('symlink', f'symbolic link to {os.readlink(path)}')
            elif stat.S_ISREG(mode):
                yield path, None
            elif not stat.S_ISDIR(mode):
                yield path, ('special', 'not a regular file')
    for error in unreadable:
        yield Path(error.filename), ('unreadable', f'directory could not be listed: {error.strerror}')


def event_summary(folder):
    """Keep only attribution and coverage fields in memory while indexing raw callbacks."""
    result = []
    for path in (folder / 'events').glob('*.json'):
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


def frozen_dir(folder, supplement=None):
    if supplement is None:
        return folder / 'collection'
    if type(supplement) is not int or supplement < 1:
        raise ValueError('A supplement is numbered from 1')
    return folder / 'supplements' / f'{supplement:04d}'


def collect(spool, operation, declaration_id, supplement=None):
    """Freeze one observation for a controller declaration, with explicit coverage gaps.

    The first collection answers the researcher's ending. A numbered supplement answers a later
    supplement declaration and holds what was staged after the previous freeze. A member that
    cannot be taken (a link, a special file, a file over a bound, a file that keeps changing)
    is listed in `omitted` and in the limitations. It never prevents the freeze.
    """
    from berman import recipes

    protocol.identifier(declaration_id)
    folder = capture.operation_dir(spool, operation)
    target = frozen_dir(folder, supplement)
    key = 'ending_id' if supplement is None else 'supplement_id'
    with capture.locked(folder):
        if target.exists():
            manifest = files.read(target / 'manifest.json')
            if manifest[key] != declaration_id:
                raise ValueError(
                    'Collection already frozen for another ending'
                    if supplement is None
                    else 'Supplement already frozen for another declaration'
                )
            return manifest
        p = files.read(folder / 'preparation.json')
        b = p['binding']
        if supplement is None:
            area, ending_id = folder, declaration_id
        else:
            if not (folder / 'collection').exists():
                raise ValueError('A supplement follows a frozen collection')
            if supplement > 1 and not frozen_dir(folder, supplement - 1).exists():
                raise ValueError('Freeze the earlier supplement first')
            ending_id = files.read(folder / 'collection' / 'manifest.json')['ending_id']
            # Close the late area for this supplement. Later callbacks start a new one.
            area = folder / f'late-{supplement:04d}'
            if not area.exists():
                if (folder / 'late').exists():
                    (folder / 'late').rename(area)
                else:
                    area.mkdir()
                capture.sync_directory(folder)
        numbers = capture.sittings(folder)
        receipts = [launch_result(folder, p, number) for number in numbers]
        launched = receipts[0] if receipts else launch_result(folder, p, 1)
        sid = launched['session_id']
        limitations = []
        if not (Path(p['packet']) / 'notes').is_dir():
            limitations.append('Assigned notes directory unavailable.')
        if not receipts or any(not r['exit'] for r in receipts):
            limitations.append('Native process exit was not observed before collection.')
        elif any(r['exit'].get('returncode') is None and 'unobserved' in r['exit'] for r in receipts):
            limitations.append('A sitting ended without its launcher observing the native process exit.')
        with tempfile.TemporaryDirectory(dir=folder, prefix='.collect-') as temporary:
            stage = Path(temporary)
            frozen = stage / 'frozen'
            payload = frozen / 'payload'
            payload.mkdir(parents=True)
            events = event_summary(area)
            if sid:
                supplied = next((r['transcript_path'] for r in reversed(events) if r['transcript_path']), None)
                try:
                    capture.observe(
                        area,
                        p,
                        sid,
                        {'hook_event_name': 'closeout', 'transcript_path': supplied},
                        known=capture.known_source(folder),
                    )
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
            latest = snapshots.read(area / 'session')
            event_names = [r['name'] for r in events]
            if not latest:
                limitations.append('No verified parent transcript observed.')
            else:
                snapshots.recover(area / 'session', area / 'scratch')
                meta = latest['metadata']
                if meta['omitted_partial_tail_bytes'] or meta['unparsed_jsonl_lines']:
                    limitations.append('Parent transcript contains partial or unparsed records.')
                if meta['last_assistant_message_present'] is False:
                    limitations.append('Expected assistant message absent from the latest parent observation.')
            if any(not r['generation'] for r in events):
                limitations.append('Some callbacks lack a verified transcript generation; see events.')
            if supplement is not None and not events:
                limitations.append(
                    'No callback was staged after the previous collection. This is a closeout observation only.'
                )
            else:
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
            omitted = []
            total = 0

            def omit(name, kind, detail):
                omitted.append({'path': name, 'kind': kind, 'detail': detail})

            def retain(name, src):
                nonlocal total
                try:
                    evidence.relative(name)
                except ValueError:
                    return omit(name, 'name', 'this name cannot be carried in a portable recording')
                if len(name.encode()) > 4000 or len(Path(name).parts) > 60:
                    return omit(name, 'bound', 'path exceeds the portable recording bound')
                # One place stays free for the record of omissions itself.
                if len(rows) >= protocol.MEMBER_LIMIT - 1:
                    return omit(name, 'bound', f'the collection already holds {len(rows)} members')
                try:
                    size = src.stat().st_size
                except OSError as exc:
                    return omit(name, 'unstable', str(exc))
                if size > protocol.FILE_LIMIT:
                    detail = f'{size} bytes exceeds the {protocol.FILE_LIMIT}-byte member bound'
                    return omit(name, 'bound', f'{detail}; SHA-256 {protocol.hash_file(src)}')
                if total + size > protocol.TOTAL_LIMIT:
                    return omit(
                        name,
                        'bound',
                        f'{size} more bytes would exceed the {protocol.TOTAL_LIMIT}-byte collection bound',
                    )
                data = None
                for _ in range(2):
                    try:
                        data = capture.stable_bytes(src, protocol.FILE_LIMIT)
                        break
                    except (OSError, ValueError) as exc:
                        problem = str(exc)
                if data is None:
                    return omit(name, 'unstable', problem)
                files.keep(payload / name, data)
                rows.append({'path': name, 'size': len(data), 'sha256': files.digest(data)})
                total += len(data)

            files.write(stage / 'launch.json', launched)
            retain('launch.json', stage / 'launch.json')
            if len(receipts) > 1:
                files.write(stage / 'sittings.json', receipts)
                retain('sittings.json', stage / 'sittings.json')
            for name in ('assignment.json', 'preparation.json', 'hooks.json'):
                retain(name, folder / name)
            # Receipts, the parent transcript and the worker's packet come first, so that a
            # member bound is spent on callbacks and context last.
            selection = [(area / 'session', 'session')] if latest else []
            selection.append((folder / 'packet', 'packet'))
            for name in ('associated', 'final-project'):
                if (stage / name).is_dir():
                    selection.append((stage / name, name))
            if supplement is None:
                selection.append((folder / 'initial-project', 'initial-project'))
            selection.append((area / 'events', 'events'))
            for root, prefix in selection:
                if root.exists():
                    for path, problem in members(root):
                        name = prefix + '/' + path.relative_to(root).as_posix()
                        if problem:
                            omit(name, *problem)
                        else:
                            retain(name, path)
            if omitted:
                # The record of omissions is itself a member and must fit the same bounds. Give
                # up the most recently taken members until it does; each is then named in it.
                while True:
                    data = files.json_bytes(omitted)
                    if total + len(data) <= protocol.TOTAL_LIMIT or not rows:
                        break
                    last = rows.pop()
                    (payload / last['path']).unlink()
                    total -= last['size']
                    omit(last['path'], 'bound', 'left out so that the record of omissions fits the collection bound')
                if total + len(data) > protocol.TOTAL_LIMIT:
                    kept = omitted[: max(1, protocol.TOTAL_LIMIT // 512)]
                    data = files.json_bytes(
                        [*kept, {'path': '', 'kind': 'bound', 'detail': f'{len(omitted)} omissions in all'}]
                    )
                for row in omitted[:12]:
                    limitations.append(f'Omitted from this collection: {row["path"]} ({row["detail"]}).')
                if len(omitted) > 12:
                    limitations.append(f'{len(omitted) - 12} further members omitted; see omitted.json.')
                files.keep(payload / 'omitted.json', data)
                rows.append({'path': 'omitted.json', 'size': len(data), 'sha256': files.digest(data)})
            extra = {} if supplement is None else {'supplement_id': declaration_id, 'sequence': supplement}
            manifest = receipt(
                'collection',
                p,
                observed_at_utc=files.now(),
                ending_id=ending_id,
                **extra,
                session_id=sid,
                limitations=limitations,
                members=sorted(rows, key=lambda row: row['path']),
                events=event_names,
                parent_sha256=latest['metadata']['sha256'] if latest else None,
                associated_sessions=[s['session_id'] for s in associated['sources']] if associated else None,
                **({'omitted': omitted} if omitted else {}),
            )
            files.write(frozen / 'manifest.json', manifest)
            target.parent.mkdir(exist_ok=True)
            frozen.rename(target)
            capture.sync_directory(target.parent)
            capture.sync_directory(folder)
            return manifest


def verify_payload(collection, row):
    path = files.regular(collection / 'payload' / evidence.relative(row['path']))
    if path.stat().st_size != row['size'] or protocol.hash_file(path) != row['sha256']:
        raise ValueError(f'Frozen payload differs: {row["path"]}')
    return path


def frozen_manifest(spool, operation, declaration_id, supplement=None):
    collection = frozen_dir(capture.operation_dir(spool, operation), supplement)
    manifest = files.read(collection / 'manifest.json')
    key = 'ending_id' if supplement is None else 'supplement_id'
    if manifest[key] != protocol.identifier(declaration_id):
        raise ValueError('Different ending identity' if supplement is None else 'Different supplement identity')
    return collection, manifest


def payload_path(spool, operation, declaration_id, path, supplement=None):
    collection, manifest = frozen_manifest(spool, operation, declaration_id, supplement)
    evidence.relative(path)
    for row in manifest['members']:
        if row['path'] == path:
            return verify_payload(collection, row)
    raise ValueError(f'Unlisted collection member: {path}')


def damaged(spool, operation, declaration_id, supplement=None):
    """Frozen members whose staged bytes are now absent or different, with the reason for each."""
    collection, manifest = frozen_manifest(spool, operation, declaration_id, supplement)
    result = []
    for row in manifest['members']:
        try:
            verify_payload(collection, row)
        except (OSError, ValueError) as exc:
            result.append({'path': row['path'], 'problem': str(exc)})
    return result


def qualify(spool, operation, ending_id, expected_child=None):
    """Evaluate this launch against its own staged evidence. A support trial may name a known child."""
    collection, manifest = frozen_manifest(spool, operation, ending_id)
    for row in manifest['members']:
        verify_payload(collection, row)
    payload = collection / 'payload'
    prepared = files.read(payload / 'preparation.json')
    launched = files.read(payload / 'launch.json')
    sittings = files.read(payload / 'sittings.json') if (payload / 'sittings.json').exists() else [launched]
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
        # What the harness itself reported. This is its own account of the mode in force.
        'native_policy_observed': bool(modes),
        # Asked only when the binding selected a mode. None means no mode was selected, so
        # this trial says nothing about whether a selected mode is the one exercised.
        'selected_policy_exercised': modes == {policy} if policy is not None else None,
        'capture_intact': bool(
            session
            and not session['tail']
            and not session['metadata']['unparsed_jsonl_lines']
            and session['metadata']['last_assistant_message_present'] is not False
        ),
    }
    if len(sittings) > 1:
        checks['later_sittings_observed'] = all((s['exit'] or {}).get('returncode') == 0 for s in sittings[1:])
    if expected_child is not None:
        checks['expected_associated_session'] = expected_child in (manifest['associated_sessions'] or [])
    return receipt(
        'native-qualification',
        prepared,
        ending_id=ending_id,
        session_id=manifest['session_id'],
        passed=all(value for value in checks.values() if value is not None),
        checks=checks,
        selected_policy=policy,
        observed_policy=sorted(modes),
        sittings=len(sittings),
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
    for action in ('launch', 'collect', 'qualify', 'payload-path', 'damaged'):
        cmd = commands.add_parser(action)
        cmd.add_argument('operation')
        if action == 'launch':
            cmd.add_argument('--one-turn', action='store_true')
            cmd.add_argument('--resume', action='store_true', help='start the next sitting of this engagement')
            cmd.add_argument('--prompt', help='text for a resumed sitting; required with --one-turn')
            cmd.add_argument('--request', help='label for a resumed sitting; repeating it returns that sitting')
        else:
            cmd.add_argument('declaration_id', help='the ending UUID, or the supplement UUID with --supplement')
        if action in ('collect', 'payload-path', 'damaged'):
            cmd.add_argument('--supplement', type=int, help='supplement number, from 1')
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
            result = launch(
                args.spool,
                args.operation,
                one_turn=args.one_turn,
                resume=args.resume,
                prompt=args.prompt,
                request=args.request,
            )
        elif args.action == 'collect':
            result = collect(args.spool, args.operation, args.declaration_id, args.supplement)
        elif args.action == 'damaged':
            result = damaged(args.spool, args.operation, args.declaration_id, args.supplement)
        elif args.action == 'qualify':
            result = qualify(args.spool, args.operation, args.declaration_id, args.expected_child)
        elif args.action == 'payload-path':
            result = str(payload_path(args.spool, args.operation, args.declaration_id, args.path, args.supplement))
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
