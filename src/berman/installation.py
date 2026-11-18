"""Installed package resources and distribution version."""
from importlib import metadata, resources
from pathlib import Path

# pip installs wheels as ordinary files. The executor's preparation and launch
# receipts fingerprint these exact installed bytes.
PACKAGE = Path(str(resources.files('berman')))


def version():
    return metadata.version('berman')
