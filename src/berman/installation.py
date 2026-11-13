"""Installed code resources, independent of any checkout or fieldwork workspace."""
from importlib import metadata, resources
from pathlib import Path
import sys

# pip installs wheels as ordinary files. Probes must be copyable into disposable
# sandboxes, and qualification records fingerprint these exact installed bytes.
PACKAGE = Path(str(resources.files('berman')))
FROZEN = '_legacy/capture_session.py'
FROZEN_SHA256 = 'f87241a6b9506590fffa4e2a964f37bb7f7c719e508f4024661b45c57f983a99'


def version():
    return metadata.version('berman')


def command(*arguments, python=None, module='berman'):
    """Pin generated commands to the package's interpreter, including its venv."""
    return [str(python or sys.executable), '-I', '-B', '-m', module, *map(str, arguments)]
