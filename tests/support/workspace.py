"""Disposable projects and synthetic co-located workspaces, as release 0.26.3 configured them."""

import os
import shutil
import subprocess
import sys

from berman.files import read, write
from tests.support.paths import REPO


def state(root):
    """Every regular file beneath root with its bytes, mode and mtime: what a read must leave unchanged."""
    return {
        str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mode, p.stat().st_mtime_ns)
        for p in root.rglob('*')
        if p.is_file() and not p.is_symlink()
    }


def workspace(root, base, project, documents=None, catalogue_runs=()):
    """A disposable co-located workspace with the Stewart connection and recipes and its own test sequence."""
    (root / 'runs').mkdir(parents=True)
    write(root / 'source-layout.json', {'runs': []})
    shutil.copytree(REPO / 'tests/fixtures/workspace/recipes', root / 'recipes')
    shutil.copytree(REPO / 'tests/fixtures/workspace/connections/stewart', root / 'connections/stewart')
    # This fixture owns its sequence; completed workspace plans can be retired.
    write(
        root / 'plans/stewart-fieldwork-roles/plan.json',
        {
            'schema': 'berman.research-plan/1',
            'id': 'stewart-fieldwork-roles',
            'connection': 'stewart',
            'steps': [
                {'id': 'repeat-after-008', 'recipe': 'work-accounts'},
                {'id': 'inquiry-after-repeat', 'recipe': 'participant-inquiry'},
            ],
            'commitment': 'Disposable test sequence; not a live research commitment.',
            'relationships': [],
        },
    )
    if documents is not None:
        definition = read(root / 'connections/stewart/connection.json')
        definition['documents'] = documents
        write(root / 'connections/stewart/connection.json', definition)
    write(
        root / 'connections/catalogue.json',
        {
            'schema': 'berman.run-catalogue/1',
            'support_collections': [],
            'runs': [
                {'run': r, 'connection': 'stewart', 'relationship': 'predates research plans', 'basis': 'fixture'}
                for r in catalogue_runs
            ],
        },
    )
    write(
        root / 'berman.json',
        {
            'schema': 'berman.workspace/1',
            'collection_id': '7fafc98c-11e2-4acd-9035-408d9199fddd',
            'packet_root': str(base / 'fieldwork'),
            'codex_home': str(base / 'codex'),
            'python': sys.executable,
            'codex': '/unconfigured/codex',
            'default_connection': 'stewart',
            'legacy_connection': 'stewart',
            'catalogue': 'connections/catalogue.json',
            'connections': {
                'stewart': {
                    'definition': 'connections/stewart/connection.json',
                    'checkout': str(project),
                    'plan': 'plans/stewart-fieldwork-roles/plan.json',
                    'capture': 'berman-codex',
                }
            },
        },
    )


def git_repository(path, files, env=None):
    path.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (path / name).parent.mkdir(parents=True, exist_ok=True)
        (path / name).write_text(text)
    env = dict(os.environ, **(env or {}))
    for argv in (
        ['init', '-q', '-b', 'main'],
        ['add', '.'],
        [
            '-c',
            'user.name=Test',
            '-c',
            'user.email=test@invalid',
            '-c',
            'commit.gpgsign=false',
            '-c',
            'core.hooksPath=/dev/null',
            'commit',
            '-qm',
            'initial',
        ],
    ):
        subprocess.run(['git', '-C', str(path), *argv], check=True, env=env, capture_output=True)
