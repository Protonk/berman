"""Standalone source, workspace routing and generic editions without native harness sessions."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import berman_capture
import berman_source
import berman_verify
from berman_files import Decision, read, write
from berman_records import ROOT, Store
from berman_workflow import Workflow
from test_berman import FixtureHarness, workspace, git_repository
from test_connections import orchard
from test_verify import Runner
import build_archive


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
        self.assertIsNone(berman_source.discover(child / 'nested'))
        (child / 'berman.json').write_text('{}')
        self.assertEqual(berman_source.discover(child / 'nested'), child)

    def test_initialization_owns_its_state_and_hook_arguments(self):
        project = self.base / 'project'
        project.mkdir()
        (project / 'sentinel').write_text('unchanged')
        root = self.base / 'study with spaces'
        result = berman_source.initialize(root, project, 'example', '/fixture/codex', '/fixture/claude')
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
        self.assertEqual(argv[4:], ['hook', '--analysis-root', str(root), '--codex-home', str(store.home)])
        self.assertEqual(Path(argv[3]), ROOT / 'tools/berman_capture_router.py')
        with self.assertRaisesRegex(Decision, 'already exists'):
            berman_source.initialize(root, project, 'example')
        with self.assertRaisesRegex(Decision, 'disjoint'):
            berman_source.initialize(project / 'nested-study', project, 'example')

    def test_software_verification_never_loads_a_study(self):
        with mock.patch.object(berman_verify, 'Store', side_effect=AssertionError('study loaded')):
            result = berman_verify.software(run=Runner())
        self.assertEqual(result['verdict'], 'passed')
        self.assertEqual(set(result['checks']), {'tests', 'launcher-tests', 'frozen-collector'})
        self.assertEqual(result['scope'], 'software')

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
        self.assertEqual(record['software'], berman_source.identity())
        source = home / 'sessions/synthetic.jsonl'
        source.parent.mkdir()
        source.write_text(json.dumps({'type': 'session_meta', 'payload': {'id': 'synthetic'}}) + '\n' +
                          json.dumps({'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant',
                                  'content': [{'text': 'synthetic reply'}]}}) + '\n')
        definition = berman_capture.definition(store, 'berman-codex')
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
        build = build_archive.select(root, 'connections', ['orchard'])
        generated, _ = build_archive.render(build)
        payload = build_archive.assemble(build, generated)
        self.assertIn('README.md', payload)
        self.assertNotIn('berman.json', payload)
        self.assertFalse(any(p.startswith(('tools/', 'hooks/', 'recipes/')) for p in payload))
        for tail in ([], ['--verify']):
            result = subprocess.run([sys.executable, '-I', '-B', str(ROOT / 'tools/build_archive.py'),
                                     '--root', str(root), *tail], text=True, capture_output=True, cwd=self.base)
            self.assertEqual(result.returncode, 0, result.stderr)
        result = subprocess.run([sys.executable, '-I', '-B', str(ROOT / 'berman'), '--root', str(root),
                                 '--json', 'status'], text=True, capture_output=True, cwd=self.base)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['archived'], 1)
        write(root / 'edition.json', {'schema': 'berman.edition/1', 'documents': ['../outside']})
        with self.assertRaisesRegex(ValueError, 'relative paths'):
            build_archive.assemble(build, generated)

    def test_other_installation_handlers_are_recognized_for_exclusive_replacement(self):
        self.assertEqual(berman_capture.capture_entry({'command':
            '/usr/bin/python3 -I -B /another/install/tools/berman_capture_router.py hook'}),
            'berman_capture_router.py')
