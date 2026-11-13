"""Source identity and explicit, side-effect-free workspace selection."""
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import uuid

from berman.installation import PACKAGE, version



def identity():
    paths = sorted(PACKAGE.rglob('*.py'))
    hashes = {p.relative_to(PACKAGE).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in paths if not p.name.startswith('test_')}
    return {'schema': 'berman.software/1', 'version': version(),
            'source_sha256': hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest(),
            'files_sha256': hashes}


def discover(start=None):
    """Find a workspace without crossing into a containing Git repository."""
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
    except (OSError, ValueError, AttributeError):
        return 'connections'
    return value if value in ('connections', 'stewart') else 'connections'


def initialize(destination, project, connection, codex=None, claude=None):
    """Create an unqualified workspace; install no hooks and assign no engagement."""
    from berman.capture import adapters
    from berman.config import identity as check_identity
    from berman.files import Decision, write
    from berman.records import Store
    check_identity(connection, 'connection')
    root = Path(destination).expanduser().absolute()
    project = Path(project).expanduser().resolve()
    if root.exists() or root.is_symlink():
        raise Decision(f'Workspace destination already exists: {root}')
    if not project.is_dir():
        raise Decision(f'Project directory is missing: {project}')
    root = root.parent.resolve() / root.name
    packet = root.parent / (root.name + '-packets')
    if any(a == b or a in b.parents or b in a.parents for a, b in ((root, project), (packet, project))):
        raise Decision('Workspace, project and packet locations must be disjoint')
    root.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.berman-init-', dir=root.parent))
    try:
        config = {'schema': 'berman.workspace/1', 'collection_id': str(uuid.uuid4()),
                  'packet_root': str(packet), 'codex_home': str(root / '.state/codex'),
                  'claude_home': str(root / '.state/claude'), 'python': sys.executable,
                  'runtime_python': sys.executable,
                  'codex': str(Path(codex or shutil.which('codex') or '/unconfigured/codex').expanduser().absolute()),
                  'claude': str(Path(claude or shutil.which('claude') or '/unconfigured/claude').expanduser().absolute()),
                  'default_connection': connection, 'legacy_connection': None,
                  'catalogue': 'connections/catalogue.json',
                  'connections': {connection: {'definition': f'connections/{connection}/connection.json',
                     'checkout': str(project), 'capture': 'berman-codex', 'plan': None}}}
        write(stage / 'berman.json', config)
        write(stage / f'connections/{connection}/connection.json', {
            'schema': 'berman.connection/1', 'id': connection, 'name': connection, 'version': 1,
            'documents': ['README.md', 'AGENTS.md'], 'commission': 'COMMISSION.md',
            'revision': 'Project work; preparation-time HEAD {head}', 'checks': [], 'readers': []})
        (stage / f'connections/{connection}/COMMISSION.md').write_text(
            'Agree the development work and research instructions before assigning an engagement.\n')
        write(stage / 'connections/catalogue.json', {'schema': 'berman.run-catalogue/1', 'runs': [], 'support_collections': []})
        write(stage / 'source-layout.json', {'runs': []})
        write(stage / 'software.json', identity())
        write(stage / 'edition.json', {'schema': 'berman.edition/1', 'documents': ['README.md']})
        (stage / 'README.md').write_text('# Fieldwork workspace\n\nNo engagement is assigned. Configure a recipe and research plan before preparation.\n')
        (stage / '.gitignore').write_text('.state/\n.berman.lock\n.berman-locks/\n.claude-fieldwork/\narchive/editions/\n')
        for directory in ('runs', 'operations', 'recipes', 'plans', '.state/codex', '.state/claude'):
            (stage / directory).mkdir(parents=True, exist_ok=True)
        # Definition generation uses the installed code location, never workspace/tools.
        store = Store(stage)
        store.root = root  # Hook arguments name the committed destination, not staging.
        write(stage / 'hooks/berman-capture.hooks.json', adapters.definition(store, 'berman-codex'))
        stage.rename(root)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return {'workspace': str(root), 'collection_id': config['collection_id'], 'software': identity(),
            'meaning': 'Workspace created with private harness homes. No hooks installed, capture qualified or fieldwork assigned.'}
