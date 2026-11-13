"""Standalone source, workspace routing and generic editions without native harness sessions."""
from tests.support.paths import REPO
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from berman.capture import adapters
from berman import workspace as workspaces
from berman import verify
from berman.files import Decision, read, write
from berman.records import Store
from berman.installation import PACKAGE as ROOT
from berman.workflow import Workflow
from tests.support.workspace import FixtureHarness, workspace, git_repository
from tests.integration.test_connections import orchard
from berman.archive import builder


class StandaloneTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()

    def test_discovery_stops_at_repository_boundaries(self):
        (self.base / 'berman.json').write_text('{}')
        child = self.base / 'child'
        (child / '.git').mkdir(parents=True)
        (child / 'nested').mkdir()
        self.assertIsNone(workspaces.discover(child / 'nested'))
        (child / 'berman.json').write_text('{}')
        self.assertEqual(workspaces.discover(child / 'nested'), child)

    def test_initialization_owns_its_state_and_hook_arguments(self):
        project = self.base / 'project'
        project.mkdir()
        (project / 'sentinel').write_text('unchanged')
        root = self.base / 'study with spaces'
        result = workspaces.initialize(root, project, 'example', '/fixture/codex', '/fixture/claude')
        store = Store(root)
        self.assertEqual(store.home, root / '.state/codex')
        self.assertEqual(store.claude_home, root / '.state/claude')
        self.assertIsNone(store.connection().plan)
        self.assertEqual(read(root / 'source-layout.json'), {'runs': []})
        self.assertEqual([p.name for p in project.iterdir()], ['sentinel'])
        self.assertFalse((store.home / 'hooks.json').exists())
        self.assertEqual(result['software'], read(root / 'software.json'))
        command = read(root / 'hooks/berman-capture.hooks.json')['hooks']['Stop'][0]['hooks'][0]['command']
        argv = shlex.split(command)
        self.assertEqual(argv[5:], ['hook', '--analysis-root', str(root), '--codex-home', str(store.home)])
        self.assertEqual(argv[1:5], ['-I', '-B', '-m', 'berman.capture.router'])
        with self.assertRaisesRegex(Decision, 'already exists'):
            workspaces.initialize(root, project, 'example')
        with self.assertRaisesRegex(Decision, 'disjoint'):
            workspaces.initialize(project / 'nested-study', project, 'example')

    def test_software_verification_never_loads_a_study(self):
        with mock.patch.object(verify, 'Store', side_effect=AssertionError('study loaded')):
            result = verify.software()
        self.assertEqual(result['verdict'], 'passed')
        self.assertEqual(set(result['checks']), {'installation', 'frozen-collector'})
        self.assertEqual(result['scope'], 'software')

    def test_changing_probe_python_keeps_the_initialized_package_interpreter(self):
        project = self.base / 'project'
        project.mkdir()
        root = self.base / 'study'
        workspaces.initialize(root, project, 'example')
        original = adapters.definition(Store(root), 'berman-codex')
        config = read(root / 'berman.json')
        config['python'] = '/probe-only/python'
        write(root / 'berman.json', config)
        store = Store(root)
        self.assertEqual(store.python, Path('/probe-only/python'))
        self.assertEqual(store.runtime_python, Path(sys.executable))
        self.assertEqual(adapters.definition(store, 'berman-codex'), original)

    def test_external_workspace_generated_hook_and_generic_edition(self):
        root = self.base / 'study'
        home = self.base / 'codex'
        home.mkdir()
        project = self.base / 'stewart'
        git_repository(project, {'README.md': 'Synthetic legacy project\n'})
        workspace(root, self.base, project, documents=['README.md'])
        orchard(root, self.base, default='orchard')
        store = Store(root)
        workflow = Workflow(store, FixtureHarness(store))
        row = workflow.new()
        record = store.record(row['run'])
        self.assertEqual(record['software'], workspaces.identity())
        source = home / 'sessions/synthetic.jsonl'
        source.parent.mkdir()
        source.write_text(json.dumps({'type': 'session_meta', 'payload': {'id': 'synthetic'}}) + '\n' +
                          json.dumps({'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant',
                                  'content': [{'text': 'synthetic reply'}]}}) + '\n')
        definition = adapters.definition(store, 'berman-codex')
        for event in ('UserPromptSubmit', 'Stop', 'SessionEnd'):
            command = definition['hooks'][event][0]['hooks'][0]['command']
            payload = {'session_id': 'synthetic', 'turn_id': 'one', 'transcript_path': str(source),
                       'hook_event_name': event, 'prompt': 'Berman-Fieldwork-Run: ' + row['run']}
            result = subprocess.run(shlex.split(command), input=json.dumps(payload), text=True,
                                    capture_output=True, cwd=self.base, check=True)
            self.assertNotIn('systemMessage', json.loads(result.stdout), result.stdout)
        workflow.finish('Synthetic engagement completed', row['run'])
        (root / 'README.md').write_text('Selected context only.\n')
        write(root / 'edition.json', {'schema': 'berman.edition/1', 'documents': ['README.md']})
        build = builder.select(root, 'connections', ['orchard'])
        generated, _ = builder.render(build)
        payload = builder.assemble(build, generated)
        self.assertIn('README.md', payload)
        self.assertNotIn('berman.json', payload)
        self.assertFalse(any(p.startswith(('tools/', 'hooks/', 'recipes/')) for p in payload))
        for tail in ([], ['--verify']):
            result = subprocess.run([sys.executable, '-I', '-B', '-m', 'berman.archive.builder',
                                     '--root', str(root), *tail], text=True, capture_output=True, cwd=self.base)
            self.assertEqual(result.returncode, 0, result.stderr)
        result = subprocess.run([sys.executable, '-I', '-B', '-m', 'berman', '--root', str(root),
                                 '--json', 'status'], text=True, capture_output=True, cwd=self.base)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['archived'], 1)
        write(root / 'edition.json', {'schema': 'berman.edition/1', 'documents': ['../outside']})
        with self.assertRaisesRegex(ValueError, 'relative paths'):
            builder.assemble(build, generated)

    def test_other_installation_handlers_are_recognized_for_exclusive_replacement(self):
        self.assertEqual(adapters.capture_entry({'command':
            '/usr/bin/python3 -I -B /another/install/tools/berman_capture_router.py hook'}),
            'berman_capture_router.py')
