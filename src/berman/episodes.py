"""An episode is opened and closed by its producer. Native process exits are sitting facts.

A sitting is attended in the producer's terminal, or unattended with the native output kept
beside its record and read for the response the harness reports.
"""

import contextlib
import os
from pathlib import Path
import shutil
import subprocess
from uuid import uuid4

from berman import capture, files, project, records
from berman.installation import JOB_TEMPLATES, PACKAGE


def install_template(destination):
    with files.new_directory(destination) as stage:
        for name in JOB_TEMPLATES:
            shutil.copyfile(PACKAGE / 'templates' / name, stage / name)
    return str(destination)


def open_episode(folder, checkout, actor, job_files):
    harness = capture.HARNESSES[actor]
    project_path = Path(
        subprocess.check_output(['git', '-C', str(checkout), 'rev-parse', '--show-toplevel'], text=True).strip()
    ).resolve()
    home = os.environ.get(harness.HOME_VARIABLE) or Path.home() / harness.DEFAULT_HOME
    native_home = Path(home).expanduser().resolve()
    for path in (project_path, native_home):
        if path.is_relative_to(folder) or folder.is_relative_to(path):
            raise ValueError('Keep the episode, checkout and harness home in separate directories')
    jobs = []
    for path in job_files:
        content = path.read_text(encoding='utf-8')
        if not content.strip():
            raise ValueError(f'The additional job needs opening instructions in {path}')
        jobs.append(content)
    if folder.exists():
        state = load(folder)
        if (state['project'], state['actor'], state['jobs'], state['home']) != (
            str(project_path),
            actor,
            jobs,
            str(native_home),
        ):
            raise ValueError('An episode with different instructions or settings already occupies this path')
        return state
    ident = str(uuid4())
    about = (
        f'This session is part of a Berman episode its producer opened in {project_path}. '
        'Berman keeps a record of the session for the producer.'
    )
    if jobs:
        assignment = 'this additional job' if len(jobs) == 1 else 'these additional jobs'
        instructions = '\n\n---\n\n'.join(jobs).rstrip()
        about += f'\n\nAlongside any other work in the session, carry out {assignment}:\n\n{instructions}'
    else:
        about += ' The producer has given no additional job.'
    opening = (
        f'Berman episode {ident}\n\n{about}\n\n'
        f'Return directory: {folder / "return"}\nKeep ordinary project changes in the checkout.\n'
    )
    state = {
        'schema': 'berman.episode/1',
        'id': ident,
        'opened_at': files.now(),
        'directory': str(folder),
        'actor': actor,
        'project': str(project_path),
        'home': str(native_home),
        'jobs': jobs,
        'opening': opening,
        'initial_project': project.git_state(project_path),
    }
    with files.new_directory(folder) as stage:
        (stage / 'return').mkdir()
        for name in ('.sitting.lock', '.capture.lock'):
            (stage / name).touch(mode=0o600)
        files.write(stage / 'episode.json', state)
        files.atomic(stage / 'opening.md', opening.encode())
    return state


def load(folder):
    state = files.read(folder / 'episode.json')
    if state.get('schema') != 'berman.episode/1':
        raise ValueError('Expected a Berman episode directory')
    if state['directory'] != str(folder):
        raise ValueError('Operate this episode at its opened path. Its record can be read from any copy.')
    return state


def bound_session(folder, state):
    """The native session bound to the episode, or the one Berman assigns before the first sitting."""
    binding = folder / 'session-id.json'
    if binding.exists():
        return files.read(binding)
    return capture.HARNESSES[state['actor']].assigned_session(state)


def status(folder):
    state = load(folder)
    ending = folder / 'ending.json'
    return {
        'episode': state['id'],
        'actor': state['actor'],
        'project': state['project'],
        'opening': str(folder / 'opening.md'),
        'return': str(folder / 'return'),
        'ending': files.read(ending) if ending.exists() else None,
        'sittings': [files.read(p) for p in sorted((folder / 'sittings').glob('*.json'))],
        'record': str(folder / 'record') if (folder / 'record').is_dir() else None,
    }


def launch(folder, request=None, prompt=None, attended=True):
    with files.lock(folder, '.sitting.lock', blocking=False) as lease:
        state = load(folder)
        if (folder / 'ending.json').exists():
            raise ValueError('The producer has closed this episode')
        actor = state['actor']
        harness = capture.HARNESSES[actor]
        sittings = folder / 'sittings'
        recent = [files.read(p) for p in sorted(sittings.glob('*.json'))]
        for sitting in recent:
            if sitting['request'] == request:
                if (sitting['prompt'], sitting.get('attended', True)) != (prompt, attended):
                    raise ValueError('This request already started a sitting with different instructions')
                if not attended and sitting.get('pid') and sitting['exit'] is None and 'response' not in sitting:
                    # The launcher stopped before the exit. Holding the lease, no process still writes this output.
                    sitting.update(account(folder, state, harness, sitting))
                    sitting['limitations'].insert(0, 'The launcher did not observe the process exit.')
                    files.write(sittings / f'{sitting["number"]:06}.json', sitting)
                return sitting
        if request is not None and not recent:
            raise ValueError('Start the first sitting before resuming')
        if (folder / 'opening.md').read_text() != state['opening']:
            raise ValueError('opening.md differs from the opening recorded in episode.json')
        if not Path(state['project']).is_dir():
            raise ValueError(f'The checkout is unavailable at {state["project"]}')
        executable = shutil.which(actor)
        if not executable:
            raise ValueError(f'Install {actor} in the Linux development environment before launching')
        session = bound_session(folder, state)
        if request is not None and session is None:
            raise ValueError('No Codex session was bound. Review the episode hook in the native CLI.')
        files.safe_directory(folder / 'return')
        hooks = capture.hook_settings(actor)
        argv = [executable, *harness.arguments(folder, session, request is not None, hooks, attended)]
        text = prompt if request is not None else capture.first_message(state, prompt)
        if text:
            argv.extend(['--', text])
        number = len(recent) + 1
        sitting = {
            'number': number,
            'request': request,
            'prompt': prompt,
            'attended': attended,
            'started_at': files.now(),
            'executable': executable,
            'exit': None,
        }
        if not attended:
            sitting.update(stdout=f'sittings/{number:06}.stdout', stderr=f'sittings/{number:06}.stderr')
        path = sittings / f'{number:06}.json'
        files.write(path, sitting)
        env = dict(os.environ, BERMAN_EPISODE=str(folder))
        env[harness.HOME_VARIABLE] = state['home']
        try:
            with contextlib.ExitStack() as streams:
                output = {}
                if not attended:
                    # An unattended actor has no terminal. Its output stays beside the sitting record.
                    output['stdin'] = subprocess.DEVNULL
                    for name in ('stdout', 'stderr'):
                        output[name] = streams.enter_context((folder / sitting[name]).open('xb'))
                # Keep the lease in the child as well if the launcher is interrupted.
                process = subprocess.Popen(argv, cwd=state['project'], env=env, pass_fds=(lease.fileno(),), **output)
        except OSError as exc:
            sitting['error'] = str(exc)
            files.write(path, sitting)
            raise
        sitting['pid'] = process.pid
        files.write(path, sitting)
        while True:
            try:
                code = process.wait()
                break
            except KeyboardInterrupt:
                # The actor process receives terminal interrupts directly.
                continue
        sitting.update(exit=code, exited_at=files.now())
        if not attended:
            sitting.update(account(folder, state, harness, sitting))
        files.write(path, sitting)
        return sitting


def account(folder, state, harness, sitting):
    """Report what an unattended sitting's native output says, without supplying what it lacks."""
    try:
        result = harness.outcome(files.stable_bytes(folder / sitting['stdout'], files.FILE_LIMIT))
    except (OSError, ValueError) as exc:
        result = {'session': None, 'response': None, 'denials': [], 'limitations': [f'Native output unread: {exc}']}
    limitations = list(result['limitations'])
    reported = result['session']
    if reported is not None:
        with files.lock(folder, '.capture.lock'):
            bound = bound_session(folder, state)
            if bound is None and capture.IDENTITY.fullmatch(reported):
                # No callback bound the first sitting. The process Berman launched named its own session.
                files.keep(folder / 'session-id.json', files.json_bytes(reported))
            elif reported != bound:
                limitations.append(f'The native output names session {reported}, not the bound session {bound}.')
    if code := sitting['exit']:
        ending = f'The actor process ended on signal {-code}.' if code < 0 else f'The actor exited with status {code}.'
        try:
            lines = files.stable_bytes(folder / sitting['stderr'], files.FILE_LIMIT).decode(errors='replace')
            last = next((line.strip() for line in reversed(lines.splitlines()) if line.strip()), '')
        except (OSError, ValueError):
            last = ''
        limitations.append(ending + (f' Its error output ends: {last[:300]}' if last else ''))
    return {
        'native_session': reported,
        'response': result['response'],
        'denials': result['denials'],
        'limitations': limitations,
    }


def close(folder, reason):
    if not reason.strip():
        raise ValueError('The producer must supply an ending reason')
    with files.lock(folder, '.sitting.lock', blocking=False), files.lock(folder, '.capture.lock'):
        state = load(folder)
        ending = folder / 'ending.json'
        if ending.exists():
            if files.read(ending)['reason'] != reason:
                raise ValueError('A different ending is already recorded')
        else:
            files.write(ending, {'reason': reason, 'at': files.now()})
        if (folder / 'record').exists():
            return records.verify(folder / 'record')
        limitations = []
        session = bound_session(folder, state)
        if session and (folder / 'sittings').is_dir():
            try:
                capture.observe(folder / 'telemetry', state, session, {'hook_event_name': 'closeout'})
            except (OSError, ValueError, TypeError, KeyError) as exc:
                limitations.append(f'Final parent transcript observation: {exc}')
        return records.seal(folder, state, limitations)
