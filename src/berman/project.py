"""Observe the checkout at the boundaries of an episode.

An observation shows the checkout at one time. Anyone may change it between observations.
"""

import os
import subprocess

from berman import files


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
    return {'observed_at': files.now(), 'head': head.stdout.strip() or None, 'status': status}
