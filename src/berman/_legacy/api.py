"""The names Berman's own modules take from the frozen collector, and nothing else.

capture_session.py is preserved evidence and part of the router's verified implementation; it is
never edited. The capture path (router, runtime, collector) loads it itself because those bytes
are verified too. These names come from that same loaded instance, so a process never executes
the frozen file twice.
"""
from berman.capture import collector as _collector

RUN = _collector.capture.RUN  # Run identities the collectors accept: run- and 3 to 6 digits.
SESSION = _collector.capture.SESSION  # Session identities the collectors and router accept from Codex.
CODEX_HOME = _collector.capture.CODEX_HOME  # Default Codex home for low-level recovery commands.
