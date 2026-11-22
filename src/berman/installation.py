"""Installed package resources and their agreement with the wheel."""

import base64
from importlib import metadata, resources
import json
from pathlib import Path

from berman import files

PACKAGE = Path(str(resources.files('berman')))
JOB_TEMPLATES = ('job.md', 'note-taking.md', 'handover.md')


def verify():
    """Identify the installed build and check its files against the wheel inventory."""
    paths = sorted(p for p in PACKAGE.rglob('*') if p.is_file() and p.suffix in ('.py', '.md'))
    members = {str(p.relative_to(PACKAGE)): files.digest(p.read_bytes()) for p in paths}
    if any(f'templates/{name}' not in members for name in JOB_TEMPLATES):
        raise ValueError('Incomplete installation: the job templates are missing')
    return {
        'version': metadata.version('berman'),
        'source_sha256': files.digest_json(members),
        'files_sha256': members,
        'verification': inventory(members),
    }


def inventory(members):
    """Compare installed resources with the distribution's recorded hashes."""
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
