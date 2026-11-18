"""A disposable Git checkout for native preparation tests."""

import os
import subprocess


def git_repository(path, files, env=None):
    path.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (path / name).parent.mkdir(parents=True, exist_ok=True)
        (path / name).write_text(text)
    env = dict(os.environ, **(env or {}))
    for argv in (
        ['init', '-q', '-b', 'main'],
        ['add', '.'],
        [
            '-c',
            'user.name=Test',
            '-c',
            'user.email=test@invalid',
            '-c',
            'commit.gpgsign=false',
            '-c',
            'core.hooksPath=/dev/null',
            'commit',
            '-qm',
            'initial',
        ],
    ):
        subprocess.run(['git', '-C', str(path), *argv], check=True, env=env, capture_output=True)
