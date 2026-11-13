"""Install opt-in launchers without editing either harness's shared configuration."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import sys
import tomllib


def versioned_release(path: Path, codex_homes: tuple[Path, ...]) -> bool:
    """Whether a tool path names a Codex release directory, which a Codex update deletes."""
    for codex_home in codex_homes:
        packages = codex_home / 'packages'
        if path.is_relative_to(packages) and 'releases' in path.relative_to(packages).parts:
            return True
    return False


def stable_tool(name: str, codex_homes: tuple[Path, ...]) -> str:
    """The first PATH match that a Codex update will not remove.

    A Codex session puts its bundled tools, ripgrep among them, on PATH from a
    versioned release directory. A path recorded from there disappears with the
    next update, and every later check then fails.
    """
    found = shutil.which(name)
    if not found:
        raise ValueError(f'{name} is not on PATH')
    path = Path(found).absolute()  # retain the updater's stable symlink
    if not versioned_release(path, codex_homes):
        return str(path)
    for directory in os.environ.get('PATH', os.defpath).split(os.pathsep):
        candidate = shutil.which(name, path=directory) if directory else None
        if candidate and not versioned_release(Path(candidate).absolute(), codex_homes):
            return str(Path(candidate).absolute())
    raise ValueError(f'the only {name} on PATH is inside a versioned Codex release directory, which a Codex '
                     f'update removes: {path}\nPut another {name} on PATH, or install from an ordinary terminal')


def install(repo: Path, analysis: Path, fieldwork: Path, home: Path) -> dict:
    source = Path(__file__).resolve().parent
    root = home / '.local/share/stewart-dev'
    bin_dir = home / '.local/bin'
    config = {'repo': str(repo.resolve()), 'denied': [str(analysis.resolve()), str(fieldwork.resolve())],
              'python': str(Path(sys.executable).absolute()), 'codex_profile_name': 'stewart-dev',
              'claude_settings_file': str(root / 'claude-settings.json')}
    named_codex_home = Path(os.environ.get('CODEX_HOME', str(home / '.codex'))).expanduser().absolute()
    codex_home = named_codex_home.resolve()
    profile_path = codex_home / (config['codex_profile_name'] + '.config.toml')
    if profile_path.exists() and not profile_path.read_text().startswith('# stewart-development-launcher-profile\n'):
        raise ValueError(f'refusing to replace an unrelated Codex profile: {profile_path}')
    for key in ('codex', 'claude', 'rg'):
        config[key] = stable_tool(key, (named_codex_home, codex_home))
    manifest = repo / 'service/Cargo.toml'
    if manifest.is_file() and tomllib.loads(manifest.read_text()).get('package', {}).get('name') == 'stewart':
        cargo = shutil.which('cargo')
        if not cargo:
            raise ValueError('cargo is not on PATH')
        config['cargo'] = str(Path(cargo).absolute())
    elif not (repo / 'src/stewart/_bootstrap.py').is_file():
        raise ValueError('repo must be the Stewart source checkout')
    if repo.resolve() in map(Path, config['denied']):
        raise ValueError('the development repo cannot be a denied root')
    for path in (analysis, fieldwork):
        if not path.is_dir():
            raise ValueError(f'missing research directory: {path}')
        if repo.resolve().is_relative_to(path.resolve()):
            raise ValueError('the development checkout cannot be inside a denied root')
    marker = root / 'installation.json'
    if root.exists() and not marker.is_file():
        raise ValueError(f'refusing to replace an unrecognized installation: {root}')
    for name in ('codex', 'claude'):
        destination = bin_dir / f'stewart-dev-{name}'
        if destination.exists() and ('# stewart-development-launcher\n' not in destination.read_text()):
            raise ValueError(f'refusing to replace an unrelated command: {destination}')
    root.mkdir(parents=True, exist_ok=True)
    bin_dir.mkdir(parents=True, exist_ok=True)
    for name in ('launch.py', 'probe.py'):
        shutil.copyfile(source / name, root / name)
    spec = importlib.util.spec_from_file_location('stewart_dev_launch', source / 'launch.py')
    launch = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launch)
    codex_home.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(launch.codex_profile(config))
    settings_path = Path(config['claude_settings_file'])
    settings_path.write_text(json.dumps(launch.claude_settings(config), indent=2) + '\n')
    (root / 'config.json').write_text(json.dumps(config, indent=2) + '\n')
    installed = [root / name for name in ('launch.py', 'probe.py', 'config.json')]
    installed += [profile_path, settings_path]
    for name in ('codex', 'claude'):
        destination = bin_dir / f'stewart-dev-{name}'
        destination.write_text('#!/bin/sh\n# stewart-development-launcher\nexec '
                               + shlex.join([config['python'], '-I', '-B', str(root / 'launch.py'), name])
                               + ' "$@"\n')
        destination.chmod(0o755)
        installed.append(destination)
        (root / f'{name}-verified.json').unlink(missing_ok=True)
    record = {'schema': 'stewart.development-launcher-install/1', 'source': str(source),
              'installed': {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in installed},
              'configuration': config}
    marker.write_text(json.dumps(record, indent=2) + '\n')
    return record


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo', type=Path, required=True)
    p.add_argument('--analysis', type=Path, required=True)
    p.add_argument('--fieldwork', type=Path, required=True)
    a = p.parse_args()
    print(json.dumps(install(a.repo, a.analysis, a.fieldwork, Path.home()), indent=2))
