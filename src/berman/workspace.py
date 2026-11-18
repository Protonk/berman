"""Source identity, and side-effect-free selection of a co-located workspace to read.

A co-located workspace is a directory with berman.json made by release 0.26.3 or earlier. This
version creates none: a study is set up in the research guest (docs/guest-deployment.md).
"""

import hashlib
import json
from pathlib import Path
import platform

from berman.installation import PACKAGE, version
from berman.environment import inspect as inspect_environment

SOURCE = ('.py', '.json')  # Modules and the packaged record schemas.


def dependencies():
    """Installed versions required in this environment; None means required but missing."""
    return inspect_environment()[0]


def identity():
    """Digests of Berman's own installed files, and beside them the environment they run in.

    source_sha256 and files_sha256 identify bytes. environment names the interpreter and
    dependency releases found at this moment: recorded context, not a fingerprint. A dependency
    resolved at install time can differ between two installations of one wheel.
    """
    paths = sorted(p for p in PACKAGE.rglob('*') if p.suffix in SOURCE and p.is_file())
    hashes = {
        p.relative_to(PACKAGE).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in paths
        if not p.name.startswith('test_')
    }
    return {
        'schema': 'berman.software/1',
        'version': version(),
        'source_sha256': hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest(),
        'files_sha256': hashes,
        'environment': {'python': platform.python_version(), 'dependencies': dependencies()},
    }


def discover(start=None):
    """Find a co-located workspace without crossing into a containing Git repository."""
    start = Path(start or Path.cwd()).resolve()
    for path in (start, *start.parents):
        if (path / 'berman.json').is_file():
            return path
        if (path / '.git').exists():
            break
    return None


def edition(root):
    """The reading edition a workspace maintains, read without loading the workspace."""
    try:
        value = json.loads((Path(root) / 'berman.json').read_text()).get('edition', 'connections')
    except OSError, ValueError, AttributeError:
        return 'connections'
    return value if value in ('connections', 'stewart') else 'connections'
