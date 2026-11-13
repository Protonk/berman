"""Source-only paths for tests; never imported by the installed application."""
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
