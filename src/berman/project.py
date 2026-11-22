"""Observe the checkout at the boundaries of an episode."""

import os
import subprocess


def git_state(project):
    def git(*args, check=True):
        return subprocess.run(
            ['git', '-C', str(project), *args],
            check=check,
            capture_output=True,
            text=True,
            errors='backslashreplace',
            env=dict(os.environ, GIT_OPTIONAL_LOCKS='0'),
        )

    status = git('status', '--porcelain=v1', '--untracked-files=normal').stdout
    head = git('rev-parse', '--verify', '--quiet', 'HEAD', check=False)
    if head.returncode not in (0, 1):
        head.check_returncode()
    return {'head': head.stdout.strip() or None, 'status': status}
