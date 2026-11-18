"""Disposable projects, synthetic workspaces and simulated access checks."""
import os
import shutil
import subprocess
import sys

from berman.files import Decision, read, write
from berman.installation import PACKAGE as ROOT
from tests.support.paths import REPO

def direct_hooks():
    """Direct collector handlers as an installation that predates the router left them; never installed outside tests."""
    command = f'"{sys.executable}" -I -B "{ROOT / "_legacy/capture_session.py"}" hook'
    return {'description': 'Synthetic direct-handler fixture; never installed outside tests.',
            'hooks': {event: [{'hooks': [{'type': 'command', 'command': command, 'timeout': timeout}]}]
                      for event, timeout in [('UserPromptSubmit', 3), ('Stop', 10), ('Interrupt', 3), ('SessionEnd', 3)]}}


def codex_environment(project, packet):
    """Native debug prompt-input message shape, including XML path escaping."""
    from xml.sax.saxutils import escape
    context = (f'<environment_context><cwd>{escape(str(project))}</cwd><filesystem><workspace_roots>'
               f'<root>{escape(str(project))}</root><root>{escape(str(packet))}</root></workspace_roots>'
               '<permission_profile type="managed"/></filesystem></environment_context>')
    return [{'role': 'user', 'content': [{'type': 'input_text', 'text': context}]}]


def workspace(root, base, project, documents=None, catalogue_runs=()):
    """A disposable workspace with the Stewart connection/recipes and its own test sequence."""
    (root / 'runs').mkdir(parents=True)
    write(root / 'source-layout.json', {'runs': []})
    shutil.copytree(REPO / 'tests/fixtures/workspace/recipes', root / 'recipes')
    shutil.copytree(REPO / 'tests/fixtures/workspace/connections/stewart', root / 'connections/stewart')
    # This fixture owns its sequence; completed workspace plans can be retired.
    write(root / 'plans/stewart-fieldwork-roles/plan.json', {
        'schema': 'berman.research-plan/1', 'id': 'stewart-fieldwork-roles', 'connection': 'stewart',
        'steps': [{'id': 'repeat-after-008', 'recipe': 'work-accounts'},
                  {'id': 'inquiry-after-repeat', 'recipe': 'participant-inquiry'}],
        'commitment': 'Disposable test sequence; not a live research commitment.',
        'relationships': []})
    if documents is not None:
        definition = read(root / 'connections/stewart/connection.json')
        definition['documents'] = documents
        write(root / 'connections/stewart/connection.json', definition)
    write(root / 'connections/catalogue.json', {'schema': 'berman.run-catalogue/1', 'support_collections': [],
                                                'runs': [{'run': r, 'connection': 'stewart', 'relationship': 'predates research plans',
                                                          'basis': 'fixture'} for r in catalogue_runs]})
    binary = base / 'fixture-bin/codex'
    binary.parent.mkdir(exist_ok=True)
    binary.write_text('#!/bin/sh\n[ "$1" = "--version" ] || exit 2\nprintf "codex synthetic-fixture\\n"\n')
    binary.chmod(0o755)
    real = {'python': sys.executable, 'codex': str(binary)}
    write(root / 'berman.json', {
        'schema': 'berman.workspace/1', 'collection_id': '7fafc98c-11e2-4acd-9035-408d9199fddd',
        'packet_root': str(base / 'fieldwork'), 'codex_home': str(base / 'codex'),
        'python': real['python'], 'codex': real['codex'], 'default_connection': 'stewart', 'legacy_connection': 'stewart',
        'catalogue': 'connections/catalogue.json',
        'connections': {'stewart': {'definition': 'connections/stewart/connection.json', 'checkout': str(project),
                                    'plan': 'plans/stewart-fieldwork-roles/plan.json', 'capture': 'berman-codex'}}})


def git_repository(path, files, env=None):
    path.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (path / name).parent.mkdir(parents=True, exist_ok=True)
        (path / name).write_text(text)
    env = dict(os.environ, **(env or {}))
    for argv in (['init', '-q', '-b', 'main'], ['add', '.'],
                 ['-c', 'user.name=Test', '-c', 'user.email=test@invalid', '-c', 'commit.gpgsign=false',
                  '-c', 'core.hooksPath=/dev/null', 'commit', '-qm', 'initial']):
        subprocess.run(['git', '-C', str(path), *argv], check=True, env=env, capture_output=True)


class FixtureHarness:
    def __init__(self, store):
        self.store = store
        self.calls = 0
        self.fail = False
        self.preflight_fail = False

    def preflight(self, connection=None):
        if self.preflight_fail:
            raise Decision('fixture hook check failed')

    def ensure(self, run, folder):
        self.calls += 1
        if self.fail:
            raise Decision('fixture native check failed')
        reused = (folder / 'readiness.json').exists()
        receipt = {'passed': True, 'fixture_only': True}
        write(folder / 'readiness.json', receipt)
        return dict(receipt, reused=reused)

    def argv(self, run):
        return ['/fixture/codex', run]


