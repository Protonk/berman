"""Installed code resources, independent of any checkout or fieldwork workspace."""
from importlib import metadata, resources
from pathlib import Path
import sys

# pip installs wheels as ordinary files. The executor's preparation and launch
# receipts fingerprint these exact installed bytes.
PACKAGE = Path(str(resources.files('berman')))


def version():
    return metadata.version('berman')


def command(*arguments, python=None, module='berman'):
    """Pin generated commands to the package's interpreter, including its venv."""
    return [str(python or sys.executable), '-I', '-B', '-m', module, *map(str, arguments)]
