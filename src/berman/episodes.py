"""An episode is opened and closed by its producer. Native process exits are sitting facts."""

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
    instructions = '\n\n---\n\n'.join(jobs)
    assignment = 'this additional job' if len(jobs) == 1 else 'these additional jobs'
    opening = (
        f'Berman episode {ident}\n\nContinue the ordinary work in {project_path}. '
        f'Alongside that work, carry out {assignment}:\n\n{instructions.rstrip()}\n\n'
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


def launch(folder, request=None, prompt=None):
    with files.lock(folder, '.sitting.lock', blocking=False) as lease:
        state = load(folder)
        if (folder / 'ending.json').exists():
            raise ValueError('The producer has closed this episode')
        sittings = folder / 'sittings'
        recent = [files.read(p) for p in sorted(sittings.glob('*.json'))]
        for sitting in recent:
            if sitting['request'] == request:
                if sitting['prompt'] != prompt:
                    raise ValueError('This request already started a sitting with different instructions')
                return sitting
        if request is not None and not recent:
            raise ValueError('Start the first sitting before resuming')
        if (folder / 'opening.md').read_text() != state['opening']:
            raise ValueError('opening.md differs from the opening recorded in episode.json')
        actor = state['actor']
        harness = capture.HARNESSES[actor]
        executable = shutil.which(actor)
        if not executable:
            raise ValueError(f'Install {actor} in the Linux development environment before launching')
        session = bound_session(folder, state)
        if request is not None and session is None:
            raise ValueError('No Codex session was bound. Review the episode hook in the native CLI.')
        files.safe_directory(folder / 'return')
        argv = [executable, *harness.arguments(folder, session, request is not None, capture.hook_settings(actor))]
        text = prompt if request is not None else state['opening']
        if text:
            argv.extend(['--', text])
        number = len(recent) + 1
        sitting = {
            'number': number,
            'request': request,
            'prompt': prompt,
            'started_at': files.now(),
            'executable': executable,
            'exit': None,
        }
        path = sittings / f'{number:06}.json'
        files.write(path, sitting)
        env = dict(os.environ, BERMAN_EPISODE=str(folder))
        env[harness.HOME_VARIABLE] = state['home']
        try:
            # Keep the lease in the child as well if the launcher is interrupted.
            process = subprocess.Popen(argv, cwd=state['project'], env=env, pass_fds=(lease.fileno(),))
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
                # The interactive process receives terminal interrupts directly.
                continue
        sitting.update(exit=code, exited_at=files.now())
        files.write(path, sitting)
        return sitting


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
