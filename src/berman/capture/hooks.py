"""Recognize Berman's own commands in a harness's installed hook definition.

The executor's router must be the only Berman handler in a Codex home (berman.executor.verify_hooks).
A home that an earlier release operated can still hold that release's router or a directly
installed collector; those are named here so they are found and refused rather than run beside it.
"""

from pathlib import Path
import shlex


def capture_entry(hook):
    """Recognize our execution targets, including obsolete directly installed collectors."""
    try:
        argv = shlex.split(hook.get('command', ''))
    except ValueError:
        return None
    if any(argv[i : i + 2] == ['-m', 'berman.executor'] and 'hook' in argv[i + 2 :] for i in range(len(argv) - 1)):
        return 'berman.executor'
    if any(argv[i : i + 2] == ['-m', 'berman.capture.router'] for i in range(len(argv) - 1)):
        return 'berman.capture.router'
    for name in ('capture_session.py', 'berman_collector.py', 'berman_capture_router.py'):
        if any(Path(arg).is_absolute() and Path(arg).name == name for arg in argv):
            return name
    return None
