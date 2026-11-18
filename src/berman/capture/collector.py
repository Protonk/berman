"""The one place the frozen collector is loaded.

capture_session.py is the Codex collector as releases up to 0.26.3 ran it. Its bytes are preserved
evidence and are never edited. This version installs none of its hooks and runs none of its
commands: live capture belongs to the executor (berman.executor.capture). What is still used is
its reader, `capture.render`, which derives the reading copy of a Codex transcript, and the run
and session identity patterns that berman._legacy.api publishes from this same loaded instance.
"""

from berman._legacy import capture_session as capture

__all__ = ['capture']
