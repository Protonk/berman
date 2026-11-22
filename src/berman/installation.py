"""Installed package resources and distribution version."""

from importlib import metadata, resources
from pathlib import Path

PACKAGE = Path(str(resources.files('berman')))
JOB_TEMPLATES = ('job.md', 'note-taking.md', 'handover.md')


def version():
    return metadata.version('berman')


def verify_wheel(members):
    """Check installed package resources against the distribution's recorded hashes."""
    import base64
    import json

    dist = metadata.distribution('berman')
    location = dist.read_text('direct_url.json')
    if location and json.loads(location).get('dir_info', {}).get('editable') is True:
        return 'Editable checkout; source digest computed from present files.'
    expected = {}
    for entry in dist.files or ():
        name = str(entry)
        if name.startswith('berman/') and Path(name).suffix in ('.py', '.md'):
            if entry.hash is None or entry.hash.mode != 'sha256':
                raise ValueError(f'No wheel SHA-256 for {name}')
            expected[name.removeprefix('berman/')] = base64.urlsafe_b64decode(entry.hash.value + '===').hex()
    if expected != members:
        raise ValueError('Installed resources differ from the wheel inventory')
    return 'Installed files agree with the wheel inventory.'
