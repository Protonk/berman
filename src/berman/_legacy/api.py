"""The names Berman's own modules take from the frozen collector, and nothing else.

capture_session.py is preserved evidence and is never edited. berman.capture.collector loads it,
and these names come from that same loaded instance, so a process never executes the frozen file
twice.
"""

from berman.capture import collector as _collector

RUN = _collector.capture.RUN  # Run identities the collectors accepted: run- and 3 to 6 digits.
SESSION = _collector.capture.SESSION  # Session identities the collectors accepted from Codex.
