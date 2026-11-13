"""The names Berman's own modules take from the frozen legacy collector, and nothing else.

capture_session.py is preserved evidence and part of the router's verified implementation; it is
never edited. The capture path (router, runtime, berman_collector) loads it itself because those
bytes are verified too. These names come from that same loaded instance, so a process never
executes the frozen file twice. Routed legacy capture is berman_capture_runtime.LegacyCollector.
"""
import berman_collector as _collector

MARKER = _collector.capture.MARKER  # The legacy opening line: Stewart-Fieldwork-Run: run-NNN
RUN = _collector.capture.RUN  # Run identities both collectors accept: run- and 3 to 6 digits.
SESSION = _collector.capture.SESSION  # Session identities the collectors and router accept from Codex.
CODEX_HOME = _collector.capture.CODEX_HOME  # Default Codex home for low-level recovery commands.
