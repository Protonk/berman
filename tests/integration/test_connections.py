"""Repository connections: rendering compatibility, a second connection, isolation, and concurrency."""
from tests.support.paths import REPO
import json
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock
import zipfile

from berman.records import Decision, Store
from berman.installation import PACKAGE as ROOT
from berman import snapshots, files
from berman.files import read, write
from berman.capture.collector import MARKER
from berman.workflow import Workflow
from berman.harnesses.codex import COMMON_CHECKS, Codex
from tests.support.workspace import codex_environment, FixtureHarness, git_repository, workspace, direct_hooks
from berman.capture import adapters
from berman.capture import collector as capture_collector
from berman import recovery as fieldwork

BASELINE = REPO / 'tests/fixtures/rendering-baseline'
INQUIRY_RELATIONSHIP = ('Independent: orchard fieldwork neither consumes, resets, nor reinterprets the Stewart '
                        'repeat-then-Astra commitment.')


def orchard(root, base, relationship=INQUIRY_RELATIONSHIP, default='stewart'):
    """A disposable second project configured only through the connection interface."""
    git_repository(base / 'orchard', {'GUIDE.md': 'How the orchard project works.\n',
                                      'notes/plan.txt': 'Planting plan.\n'})
    write(root / 'connections/orchard/connection.json', {
        'schema': 'berman.connection/1', 'id': 'orchard', 'name': 'Orchard', 'version': 1,
        'description': 'A disposable second repository with no Stewart bootstrap or auditor.',
        'documents': ['GUIDE.md', 'notes/plan.txt', 'HANDOFF.md'], 'checks': [], 'readers': []})
    recipe = root / 'recipes/orchard-accounts'
    write(recipe / 'recipe.json', {'schema': 'berman.recipe/2', 'id': 'orchard-accounts', 'connections': ['orchard'],
                                   'coverage': ['notes'], 'recording_protocol': 'work-accounts/1',
                                   'instructions': 'INSTRUCTIONS.md',
                                   'source': 'fixture', 'meaning': 'fixture'})
    (recipe / 'INSTRUCTIONS.md').write_text('# Orchard accounts\n\nWrite accounts of the work in `{{notes_directory}}`.\n')
    write(root / 'plans/orchard-study/plan.json', {
        'schema': 'berman.research-plan/1', 'id': 'orchard-study', 'connection': 'orchard',
        'steps': [{'id': 'orchard-first', 'recipe': 'orchard-accounts'}, {'id': 'orchard-second', 'recipe': 'orchard-accounts'}],
        'relationships': [{'plan': 'stewart-fieldwork-roles', 'relationship': relationship}] if relationship else []})
    config = read(root / 'berman.json')
    config['default_connection'] = default
    config['connections']['orchard'] = {'definition': 'connections/orchard/connection.json', 'checkout': str(base / 'orchard'),
                                        'plan': 'plans/orchard-study/plan.json', 'capture': 'berman-codex'}
    write(root / 'berman.json', config)


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name).resolve()
        self.root = self.base / 'berman'
        self.home = self.base / 'codex'
        self.home.mkdir()
        workspace(self.root, self.base, self.base / 'stewart', documents=['README.md', 'COORDINATOR-HANDOFF.md'])
        git_repository(self.base / 'stewart', {'README.md': 'Stewart\n', 'COORDINATOR-HANDOFF.md': 'Handoff\n'})
        orchard(self.root, self.base)
        self.reload()

    def reload(self):
        self.store = Store(self.root)
        self.harness = FixtureHarness(self.store)
        self.workflow = Workflow(self.store, self.harness)
        self.berman = self.store.collectors['berman-codex']

    def start(self, run, collector, prompt=None, stop=True, sid=None):
        sid = sid or 'session-' + run
        source = self.home / 'sessions' / (sid + '.jsonl')
        source.parent.mkdir(exist_ok=True)
        rows = [{'type': 'session_meta', 'payload': {'id': sid}},
                {'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant', 'content': [{'text': 'done'}]}}]
        source.write_text(''.join(json.dumps(r) + '\n' for r in rows))
        event = {'session_id': sid, 'transcript_path': str(source), 'turn_id': 'one'}
        collector.hook(dict(event, hook_event_name='UserPromptSubmit', prompt=prompt or MARKER + run))
        if stop:
            collector.hook(dict(event, hook_event_name='Stop'))
        return source, event

    def default(self, name):
        config = read(self.root / 'berman.json')
        config['default_connection'] = name
        write(self.root / 'berman.json', config)
        self.reload()


class RenderingBaselineTests(unittest.TestCase):
    """Complete rendered packets match the saved baseline, except the fixture base path."""

    def render(self):
        inputs = read(BASELINE / 'inputs.json')
        with tempfile.TemporaryDirectory(prefix='berman-render-') as temporary:
            base = Path(temporary).resolve()
            project = base / 'stewart'
            project.mkdir()
            for name, text in inputs['documents'].items():
                (project / name).write_text(text)
            env = dict(os.environ, **inputs['git_identity_and_dates'])
            for argv in (['init', '-q', '-b', 'main'], ['add', '.'],
                         ['-c', 'commit.gpgsign=false', '-c', 'core.hooksPath=/dev/null', 'commit', '-qm', 'Fixed baseline input']):
                subprocess.run(['git', '-C', str(project), *argv], check=True, env=env, capture_output=True)
            head = subprocess.run(['git', '-C', str(project), 'rev-parse', 'HEAD'], check=True, capture_output=True, text=True).stdout.strip()
            self.assertEqual(head, inputs['fixture_head'], 'fixture input differs; the comparison would be meaningless')
            (base / 'codex').mkdir()
            root = base / 'berman'
            workspace(root, base, project, documents=sorted(inputs['documents']))
            store = Store(root)
            workflow = Workflow(store, FixtureHarness(store))
            rendered = {}

            def keep(row):
                packet = Path(row['packet'])
                rendered[row['recipe']] = {f: (packet / f).read_text().replace(str(base), '<BASE>')
                                           for f in ('ASSIGNMENT.md', 'INSTRUCTIONS.md')}
                rendered[row['recipe']]['run'] = row['run']
                rendered[row['recipe']]['protocol'] = read(packet / 'run.json')['recording_protocol']
            first = workflow.new()
            keep(first)
            sid = 'baseline-' + first['run']
            source = base / 'codex/sessions' / (sid + '.jsonl')
            source.parent.mkdir(parents=True)
            source.write_text(json.dumps({'type': 'session_meta', 'payload': {'id': sid}}) + '\n' + json.dumps(
                {'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant', 'content': [{'text': 'done'}]}}) + '\n')
            event = {'session_id': sid, 'transcript_path': str(source), 'turn_id': 'one'}
            store.collector.hook(dict(event, hook_event_name='UserPromptSubmit', prompt=MARKER + first['run']))
            store.collector.hook(dict(event, hook_event_name='Stop'))
            workflow.finish('Baseline fixture engagement concluded', first['run'])
            keep(workflow.new())
            return inputs, rendered

    def compare(self):
        inputs, rendered = self.render()
        self.assertEqual(set(rendered), set(inputs['runs']))
        for name, packet in rendered.items():
            self.assertEqual(packet['run'], inputs['runs'][name])
            self.assertEqual(packet['protocol'], inputs['recording_protocols'][name])
            for f in ('ASSIGNMENT.md', 'INSTRUCTIONS.md'):
                with self.subTest(recipe=name, file=f):
                    self.assertEqual(packet[f], (BASELINE / name / f).read_text())

    def test_workspace_configuration_renders_the_baseline_byte_for_byte(self):
        self.compare()

    def test_the_single_project_configuration_is_refused_with_its_replacement(self):
        with tempfile.TemporaryDirectory(prefix='berman-old-format-') as temporary:
            root = Path(temporary).resolve()
            write(root / 'berman.json', {'schema': 'berman.configuration/1', 'project': str(root / 'project')})
            with self.assertRaisesRegex(ValueError, 'berman.workspace/1'):
                Store(root)

    def test_recipe_placeholder_is_resolved_in_the_saved_baseline(self):
        repeat = (BASELINE / 'work-accounts/INSTRUCTIONS.md').read_text()
        original = (REPO / 'tests/fixtures/workspace/recipes/work-accounts/INSTRUCTIONS.md').read_text()
        self.assertEqual(repeat, original.replace('{{notes_directory}}', '<BASE>/fieldwork/run-001/notes/'))


class SecondConnectionTests(Base):
    def test_lifecycle_keeps_documents_instructions_plans_and_sessions_separate(self):
        stewart = self.workflow.new()
        stewart_source, stewart_event = self.start(stewart['run'], self.berman)
        self.assertEqual(stewart['connection'], 'stewart')
        one = self.workflow.new(connection='orchard')
        self.assertEqual((stewart['run'], one['run']), ('run-001', 'run-002'))
        packet = Path(one['packet'])
        self.assertEqual(packet.parent, self.store.field)
        assignment = (packet / 'ASSIGNMENT.md').read_text()
        self.assertTrue(assignment.startswith('Berman-Fieldwork-Run: run-002\n'))
        head = read(self.store.folder(one['run']) / 'preparation/initial-project/snapshot.json')['head']
        self.assertEqual(assignment.split('\n\n')[1:], [
            f'Your working directory is `{self.base / "orchard"}`. This assignment was prepared when Orchard was at '
            f'commit {head}; the project may have moved since, so work from the checkout as you find it.',
            f'Your run directory is `{packet}`. You can read and write everything in it, although the folder '
            'containing it is otherwise closed to you.',
            f'As a fieldworker you have specific instructions, in `{packet}/INSTRUCTIONS.md`. Read them now, directly '
            'rather than asking for a copy, and follow them as you work.\n'])
        self.assertNotIn('Stewart', assignment)
        record = self.store.record(one['run'])
        self.assertNotIn('commission', record)
        self.assertFalse({'commission', 'revision_template'} & set(record['connection']))
        self.assertFalse((self.store.folder(one['run']) / 'preparation/COMMISSION.md').exists())
        self.assertIn(str(packet / 'notes') + '/', (packet / 'INSTRUCTIONS.md').read_text())
        self.assertFalse((packet / 'narrative.md').exists() or (packet / 'scratch').exists())
        meta = read(packet / 'run.json')
        self.assertEqual((meta['schema'], meta['connection']), ('berman.run-preparation/1', 'orchard'))
        self.assertEqual((meta['manager']['project'], meta['manager']['head']), ('Orchard', head))
        self.assertFalse({'assignment_sha256'} & set(meta) or 'revision' in meta['manager'])
        snap = read(self.store.folder(one['run']) / 'preparation/initial-project/snapshot.json')
        self.assertEqual([r['path'] for r in snap['files']], ['GUIDE.md', 'notes/plan.txt', 'HANDOFF.md'])
        self.assertTrue(snap['files'][-1]['missing'])
        record = self.store.record(one['run'])
        self.assertEqual((record['connection']['id'], record['plan']['id'], record['capture']['adapter']),
                         ('orchard', 'orchard-study', 'berman-codex'))
        # Each plan advances only through its own connection's started runs.
        status = self.workflow.status()
        self.assertEqual(status['connections']['stewart']['next_recipe'], 'participant-inquiry')
        self.assertEqual(status['connections']['orchard']['next_recipe'], 'orchard-accounts')
        orchard_source, orchard_event = self.start(one['run'], self.berman, prompt=assignment)
        binding = read(packet / 'session/binding.json')
        self.assertEqual((binding['collector'], binding['marker']), (capture_collector.OWNER, 'Berman-Fieldwork-Run: run-002'))
        self.assertEqual(self.workflow.status()['connections']['orchard']['next_recipe'], 'orchard-accounts')
        # Changing the default connection redirects neither callbacks nor selection of an earlier run.
        self.default('orchard')
        with orchard_source.open('a') as stream:
            stream.write(json.dumps({'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant',
                                                                           'content': [{'text': 'later'}]}}) + '\n')
        self.berman.hook(dict(orchard_event, hook_event_name='Stop'))
        self.assertEqual(read(snapshots.resolve(packet / 'session') / 'capture.json')['saved_bytes'], orchard_source.stat().st_size)
        self.berman.hook(dict(stewart_event, hook_event_name='Stop'))
        self.assertTrue((snapshots.resolve(self.store.field / stewart['run'] / 'session') / 'capture.json').exists())
        self.store.collectors['berman-codex'].command('finalize', one['run'])
        with orchard_source.open('a') as stream:
            stream.write(json.dumps({'type': 'response_item', 'payload': {'type': 'message', 'role': 'user',
                                                                           'content': [{'text': 'continued'}]}}) + '\n')
        supplement = self.workflow.supplement('Retain orchard continuation')
        self.assertIn(one['run'], supplement['path'])
        # An interrupted migration resumes its recorded target.
        atomic = files.atomic
        def interrupted(path, data):
            if path == self.root / 'source-layout.json':
                raise OSError('index update interrupted')
            return atomic(path, data)
        with mock.patch.object(files, 'atomic', side_effect=interrupted):
            with self.assertRaisesRegex(OSError, 'interrupted'):
                self.workflow.finish('The orchard engagement ended')
        self.assertTrue((self.root / 'runs' / one['run']).exists())
        result = self.workflow.finish('The orchard engagement ended')
        self.assertEqual((result['run'], result['state']), (one['run'], 'archived'))
        self.assertEqual(result['package']['namespace'], '')
        with zipfile.ZipFile(result['package']['path']) as archive:
            self.assertIn(f'runs/{one["run"]}/ASSIGNMENT.md', archive.namelist())
        archived = self.root / 'runs' / one['run']
        self.assertEqual((archived / 'context/berman/closing-project/GUIDE.md').read_text(), 'How the orchard project works.\n')
        self.assertEqual(len(list((archived / 'session-supplements').iterdir())), 1)
        # Stewart's engagement stayed open and untouched throughout.
        self.assertTrue((self.store.field / stewart['run'] / 'session/binding.json').exists())
        self.assertNotIn('ending', self.store.record(stewart['run']))
        self.default('stewart')
        self.workflow.finish('The Stewart engagement ended')
        runs = [r['run'] for r in read(self.root / 'source-layout.json')['runs']]
        self.assertEqual(sorted(runs), ['run-001', 'run-002'])
        two = self.workflow.new(connection='orchard')
        self.assertEqual(two['run'], 'run-003')
        self.assertEqual(self.store.record('run-003')['step'], 'orchard-second')

    def test_another_connections_runs_neither_advance_nor_invalidate_stewart(self):
        support = self.root / 'support-trial'
        write(support / 'source-layout.json', {'runs': [{'run': 'run-900101', 'current_root': 'runs/run-900101'}]})
        write(support / 'runs/run-900101/session/binding.json', {'run': 'run-900101', 'session_id': 'support-session'})
        write(self.home / 'berman-capture/support-session.json', {'run': 'run-900101', 'session_id': 'support-session'})
        self.store.catalogue.support = ['support-trial']
        for _ in range(2):
            row = self.workflow.new(connection='orchard')
            self.start(row['run'], self.berman)
            self.workflow.finish('done', connection='orchard')
        status = self.workflow.status()
        self.assertEqual(status['connections']['stewart']['next_recipe'], 'work-accounts')
        self.assertIsNone(status['connections']['orchard']['next_recipe'])
        self.assertIn('sequence is complete', status['connections']['orchard']['next_note'])
        self.assertEqual(self.workflow.new()['run'], 'run-003')
        # An encounter without a plan association neither advances nor obstructs Stewart's plan.
        instructions = self.base / 'unplanned-instructions.md'
        instructions.write_text(f'Write accounts in `{self.store.field}/run-010/notes/`.\n')
        fieldwork.prepare(self.berman, 'run-010', self.store.connections['stewart'].checkout,
                          ['notes'], instructions, marker=MARKER, connection='stewart')
        # A session bound outside any reservation: the collector itself would refuse it without a record.
        write(self.store.field / 'run-010/session/binding.json', {'run': 'run-010', 'session_id': 'unplanned-session'})
        step = self.store.connections['stewart'].plan.next_step(self.store.discover())
        self.assertEqual(step['recipe'], 'work-accounts')

    def test_explicit_run_determines_its_connection(self):
        one = self.workflow.new(connection='orchard')
        with self.assertRaisesRegex(Decision, 'belongs to connection orchard, not stewart'):
            self.workflow.launch(one['run'], connection='stewart')
        with mock.patch('os.execv') as execute:
            self.workflow.launch(one['run'])
        execute.assert_called_once()
        self.default('stewart')
        record = self.store.record(one['run'])
        self.assertEqual(record['project'], str(self.base / 'orchard'))
        self.assertEqual(Codex(self.store).argv(one['run'])[:5],
                         [str(self.store.codex), '-C', str(self.base / 'orchard'), '--add-dir', one['packet']])

    def test_blocked_operation_is_visible_without_blocking_another_connection(self):
        one = self.workflow.new(connection='orchard')
        shutil.rmtree(Path(one['packet']))
        status = self.workflow.status()
        self.assertIn('packet is missing', status['connections']['orchard']['blocked_by'][0])
        self.assertEqual(status['connections']['stewart']['blocked_by'], [])
        with self.assertRaisesRegex(Decision, 'packet is missing'):
            self.workflow.new(connection='orchard')
        self.assertEqual(self.workflow.new()['run'], 'run-002')

    def test_ordinary_development_creates_no_assignment_or_plan_step(self):
        launcher = self.base / 'bin/stewart-dev-codex'
        launcher.parent.mkdir()
        launcher.write_text('#!/bin/sh\n')
        launcher.chmod(0o755)
        config = read(self.root / 'berman.json')
        config['connections']['stewart']['development'] = {'codex': str(launcher)}
        write(self.root / 'berman.json', config)
        self.reload()
        before = sorted(p.relative_to(self.root) for p in self.root.rglob('*'))
        self.assertEqual(self.workflow.dev('codex', ['--model', 'fixture']),
                         {'argv': [str(self.store.codex), '--model', 'fixture'], 'connection': 'stewart',
                          'cwd': str(self.store.connection('stewart').checkout)})
        sheet = self.workflow.cheatsheet()['connections'][0]
        self.assertEqual([d['harness'] for d in sheet['development']], ['codex', 'claude'])
        self.assertEqual(sheet['development'][0]['available'], True)
        self.assertEqual(shlex.split(sheet['development'][0]['command']),
                         ['berman', '--root', str(self.root), 'dev', '--harness', 'codex'])
        self.assertEqual(sorted(p.relative_to(self.root) for p in self.root.rglob('*')), before)
        self.assertEqual(self.workflow.status()['connections']['stewart']['next_recipe'], 'work-accounts')
        self.assertEqual(self.workflow.dev('codex', connection='orchard')['cwd'], str(self.base / 'orchard'))


class ResearchPlanRelationshipTests(Base):
    def test_fieldwork_elsewhere_does_not_require_a_relationship_to_an_unfulfilled_commitment(self):
        orchard(self.root, self.base / 'second', relationship=None)
        self.reload()
        result = self.workflow.new(connection='orchard')
        self.assertEqual(result['connection'], 'orchard')
        self.assertEqual(result['assignment_origin'], 'plan')
        self.assertEqual(self.workflow.status()['connections']['stewart']['next_recipe'], 'work-accounts')

    def test_a_connection_without_a_plan_requires_a_recipe_choice(self):
        config = read(self.root / 'berman.json')
        del config['connections']['orchard']['plan']
        write(self.root / 'berman.json', config)
        self.reload()
        with self.assertRaisesRegex(Decision, 'No assignment selected for connection orchard; choose a recipe'):
            self.workflow.new(connection='orchard')
        self.assertFalse(self.store.operations.exists() and any(self.store.operations.glob('run-*')))
        result = self.workflow.new(connection='orchard', recipe_name='orchard-accounts')
        self.assertEqual(result['assignment_origin'], 'direct')
        record = self.store.record(result['run'])
        self.assertNotIn('plan', record)
        self.assertNotIn('step', record)

    def test_recipe_worded_for_stewart_cannot_serve_another_connection(self):
        plan = read(self.root / 'plans/orchard-study/plan.json')
        plan['steps'][0]['recipe'] = 'work-accounts'
        write(self.root / 'plans/orchard-study/plan.json', plan)
        self.reload()
        with self.assertRaisesRegex(Decision, 'worded for stewart, not orchard'):
            self.workflow.new(connection='orchard')


class AccessTests(Base):
    def test_external_packet_roots_are_rejected_before_preparation(self):
        config = read(self.root / 'berman.json')
        config['connections']['orchard']['packet_root'] = str(self.base / 'orchard-packets')
        write(self.root / 'berman.json', config)
        with self.assertRaisesRegex(Decision, 'shared packet root'):
            Store(self.root)
        del config['connections']['orchard']['packet_root']
        write(self.root / 'berman.json', config)
        definition = read(self.root / 'connections/orchard/connection.json')
        definition['fieldwork'] = str(self.base / 'elsewhere')
        write(self.root / 'connections/orchard/connection.json', definition)
        with self.assertRaisesRegex(Decision, 'shared packet root'):
            Store(self.root)

    def test_recorded_packet_outside_the_shared_root_is_refused(self):
        with mock.patch.object(self.workflow.harness, 'ensure', side_effect=Decision('stop before checks')):
            with self.assertRaises(Decision):
                self.workflow.new(connection='orchard')
        record = self.store.record('run-001')
        record['packet'] = str(self.base / 'elsewhere/run-001')
        self.store.save(record)
        with self.assertRaisesRegex(Decision, 'outside the shared packet root'):
            self.workflow.new(connection='orchard')

    def test_project_checks_cannot_replace_another_observation(self):
        from berman.harnesses.codex import project_checks
        for ids in [('launch_policy_observed',), ('file_packet_read',), ('project_tool', 'project_tool')]:
            with self.subTest(ids=ids), self.assertRaisesRegex(ValueError, 'Invalid project check identity'):
                project_checks([{'id': ident, 'argv': ['true']} for ident in ids], {})

    def test_probe_runs_in_selected_checkout_with_only_assigned_packet(self):
        one = self.workflow.new(connection='orchard')
        project = self.base / 'orchard'
        harness = Codex(self.store)
        seen = {}
        def invoke(argv, cwd=None, **kwargs):
            if '-I' in argv:
                spec = read(Path(argv[-1]))
                seen['spec'], seen['cwd'] = spec, cwd
                checks = {k: True for k in COMMON_CHECKS | {c['id'] for c in spec['project_checks']}}
                return subprocess.CompletedProcess(argv, 0, json.dumps({'schema': 'berman.execution-probe/1', 'token': spec['token'], 'checks': checks}), '')
            return subprocess.CompletedProcess(argv, 0, json.dumps(codex_environment(project, one["packet"])), '')
        folder = self.store.folder(one['run']) / 'preparation'
        with mock.patch.object(harness, 'hooks', return_value={'fixture': True}), \
             mock.patch.object(harness, 'fingerprint', side_effect=lambda *_: {'fixed': True}), \
             mock.patch.object(harness, 'invoke', side_effect=invoke), \
             mock.patch('berman.harnesses.codex.shutil.which', return_value='/fixture/rg'):
            self.assertTrue(harness.ensure(one['run'], folder)['passed'])
        spec = seen['spec']
        self.assertEqual(seen['cwd'], project)
        self.assertNotIn('canaries', spec)
        self.assertEqual(spec['packet'], one['packet'])
        self.assertEqual(spec['project_checks'], [])
        self.assertEqual(spec['reads'], ['GUIDE.md', 'notes/plan.txt'])
        # Stewart's declared project checks resolve to explicit argument lists.
        stewart = self.store.connections['stewart']
        with mock.patch('berman.harnesses.codex.shutil.which', side_effect=lambda name: '/fixture/' + name):
            from berman.harnesses.codex import project_checks
            checks = project_checks(stewart.checks(), {'python': '/py', 'project': '/stewart', 'fixture': '/fx', 'packet': '/p'})
        self.assertEqual([c['id'] for c in checks], ['stewart_cargo_metadata', 'stewart_rustc_startup'])
        self.assertEqual(checks[0]['argv'][:4], ['/py', '-I', '-B', '-c'])
        self.assertEqual(checks[0]['argv'][-2:], ['/fixture/cargo', '/stewart/service'])
        self.assertEqual(checks[1], {'id': 'stewart_rustc_startup', 'argv': ['/fixture/rustc', '--version'],
                                     'timeout': 20, 'stdout_contains': 'rustc '})


class CollectorOwnershipTests(Base):
    def test_frozen_collector_bytes_are_unchanged(self):
        digest = files.digest((ROOT / '_legacy/capture_session.py').read_bytes())
        self.assertEqual(digest, 'f87241a6b9506590fffa4e2a964f37bb7f7c719e508f4024661b45c57f983a99')
        self.assertEqual(read(BASELINE / 'inputs.json')['implementation_sha256']['capture_session.py'], digest)
        self.assertNotIn(b'berman_collector', (ROOT / '_legacy/capture_session.py').read_bytes())

    def test_frozen_collector_ignores_new_openings_and_refuses_new_packets(self):
        one = self.workflow.new(connection='orchard')
        opening = (Path(one['packet']) / 'ASSIGNMENT.md').read_text()
        event = {'session_id': 'mixed-up', 'transcript_path': None, 'turn_id': 'one', 'hook_event_name': 'UserPromptSubmit'}
        # The frozen file is still shipped and can be run directly; it must never bind a current packet.
        frozen = capture_collector.capture.Collector(self.store.field, self.home, self.root)
        frozen_marker = capture_collector.capture.MARKER
        self.assertEqual(frozen.hook(dict(event, prompt=opening)), {})
        self.assertFalse((self.home / 'stewart-fieldwork/mixed-up.json').exists())
        # The notes layout has no narrative.md, so the frozen run_path check refuses registration.
        with self.assertRaisesRegex(OSError, 'narrative.md'):
            frozen.hook(dict(event, prompt=frozen_marker + one['run']))
        # Through the installed hook command, that refusal becomes a message and never a binding.
        hook = subprocess.run([sys.executable, '-I', '-B', str(ROOT / '_legacy/capture_session.py'), 'hook',
                               '--fieldwork-root', str(self.store.field), '--analysis-root', str(self.root),
                               '--codex-home', str(self.home)],
                              input=json.dumps(dict(event, prompt=frozen_marker + one['run'])),
                              capture_output=True, text=True, env=dict(os.environ, CODEX_HOME=str(self.home)))
        self.assertIn('narrative.md', json.loads(hook.stdout)['systemMessage'])
        hook = subprocess.run([sys.executable, '-I', '-B', str(ROOT / '_legacy/capture_session.py'), 'hook',
                               '--fieldwork-root', str(self.store.field), '--analysis-root', str(self.root),
                               '--codex-home', str(self.home)],
                              input=json.dumps(dict(event, prompt=opening)), capture_output=True, text=True)
        self.assertEqual(json.loads(hook.stdout), {})
        self.assertFalse((Path(one['packet']) / 'session').exists())
        self.assertFalse((self.home / 'stewart-fieldwork/mixed-up.json').exists())

    def test_a_run_recorded_with_the_removed_adapter_is_never_captured_or_operated_on(self):
        stewart = self.workflow.new()
        record = read(self.store.folder(stewart['run']) / 'operation.json')
        record['capture']['adapter'] = 'legacy-codex'  # As runs reserved before 0.4 recorded it.
        write(self.store.folder(stewart['run']) / 'operation.json', record)
        with self.assertRaisesRegex(ValueError, 'not assigned to the berman-collector/1 collector'):
            self.berman.hook({'session_id': 'other', 'turn_id': 'x', 'hook_event_name': 'UserPromptSubmit',
                              'prompt': MARKER + stewart['run']})
        self.assertFalse((Path(stewart['packet']) / 'session').exists())
        self.assertEqual(self.store.adapter(self.store.record(stewart['run'])), 'legacy-codex')
        with self.assertRaisesRegex(Decision, 'removed legacy-codex adapter'):
            self.store.collector_for(self.store.record(stewart['run']))
        # Low-level recovery acts only through a record that names a current adapter.
        def recover(run):
            return subprocess.run([sys.executable, '-I', '-B', '-m', 'berman.recovery', '--fieldwork-root', str(self.store.field),
                                   '--analysis-root', str(self.root), '--codex-home', str(self.home), 'status', run],
                                  capture_output=True, text=True)
        refused = recover(stewart['run'])
        self.assertEqual(refused.returncode, 1)
        self.assertIn('removed legacy-codex adapter', refused.stderr)
        unrecorded = recover('run-777')
        self.assertEqual(unrecorded.returncode, 1)
        self.assertIn('run-777 has no operation record', unrecorded.stderr)
        orchard_run = self.workflow.new(connection='orchard')
        self.assertEqual(json.loads(recover(orchard_run['run']).stdout)['state'], 'prepared')
        # A workspace that still selects it is told what to select instead.
        config = read(self.root / 'berman.json')
        config['connections']['stewart']['capture'] = 'legacy-codex'
        write(self.root / 'berman.json', config)
        with self.assertRaisesRegex(ValueError, 'removed; set the capture of connection stewart to berman-codex'):
            Store(self.root)

    def test_new_collector_routes_through_records_and_ignores_other_workspaces(self):
        one = self.workflow.new(connection='orchard')
        source, event = self.start(one['run'], self.berman)
        state = read(self.home / f'berman-capture/{event["session_id"]}.json')
        self.assertEqual((state['workspace'], state['harness'], state['collector'], state['packet']),
                         (str(self.root), 'codex', capture_collector.OWNER, one['packet']))
        elsewhere = capture_collector.Collector(self.base / 'unrouted', self.home, self.base / 'another-workspace')
        self.assertEqual(elsewhere.hook(dict(event, hook_event_name='Stop')), {})
        unrouted = capture_collector.Collector(self.base / 'unrouted', self.home, self.root)
        unrouted.hook(dict(event, hook_event_name='Stop'))
        self.assertEqual(read(snapshots.resolve(Path(one['packet']) / 'session') / 'capture.json')['event'], 'Stop')

    def test_local_install_cannot_replace_or_run_alongside_the_executor_router(self):
        template = adapters.definition(self.store, 'berman-codex')
        write(self.root / 'hooks/berman-capture.hooks.json', template)
        installed = adapters.definition(self.store, 'berman-codex')
        installed['hooks']['Stop'].append({'hooks': [{'type': 'command',
            'command': '/path/python -I -B -m berman.executor --spool "/example spool" hook --home /example'}]})
        path = self.home / 'hooks.json'
        write(path, installed)
        before = path.read_bytes()
        with self.assertRaisesRegex(Decision, 'not exclusive'):
            adapters.installed(self.store, 'berman-codex')
        with self.assertRaisesRegex(Decision, 'host executor owns capture'):
            adapters.install(self.store, 'berman-codex')
        self.assertEqual(path.read_bytes(), before)

    def test_installation_waits_for_closed_captures_and_trust_needs_a_recorded_review(self):
        (self.root / 'hooks').mkdir(exist_ok=True)
        write(self.root / 'hooks/berman-capture.hooks.json', adapters.definition(self.store, 'berman-codex'))
        # Direct collector handlers left by an installation that predates the router.
        direct_definition = direct_hooks()
        original_hooks = json.loads(json.dumps(direct_definition))
        unrelated = {'type': 'command', 'command': '/bin/true', 'timeout': 1}
        original_hooks['hooks']['Stop'][0]['hooks'].append(unrelated)
        write(self.home / 'hooks.json', original_hooks)
        stewart = self.workflow.new()
        source, event = self.start(stewart['run'], self.berman, stop=False)
        with self.assertRaisesRegex(Decision, 'requires closed captures'):
            adapters.install(self.store, 'berman-codex')
        self.berman.hook(dict(event, hook_event_name='Stop'))
        with self.assertRaisesRegex(Decision, 'requires closed captures'):
            adapters.install(self.store, 'berman-codex')
        self.workflow.finish('Fixture session concluded', stewart['run'])
        receipt = adapters.install(self.store, 'berman-codex')
        self.assertEqual(receipt['registrations_at_installation'][0]['collector'], 'berman-codex')
        installed = read(self.home / 'hooks.json')
        self.assertIn({'hooks': [unrelated]}, installed['hooks']['Stop'])
        for event_name, groups in direct_definition['hooks'].items():
            self.assertTrue(all(g not in installed['hooks'][event_name] for g in groups))
        self.assertEqual(adapters.install(self.store, 'berman-codex')['state'], 'already-installed')
        with self.assertRaisesRegex(Decision, 'no recorded CLI trust review'):
            adapters.readiness(self.store, 'berman-codex')
        evidence = self.base / 'hooks.terminal'
        evidence.write_text('Fixture /hooks review record\n')
        recorded = adapters.record_trust(self.store, evidence, 'fixture review')
        self.assertEqual(recorded['bases'], [str(self.root / 'hooks/verification/berman-codex.json')])
        self.assertEqual(adapters.readiness(self.store, 'berman-codex')['adapter'], 'berman-codex')
        # Any later change to the installed file needs another recorded review.
        installed['hooks']['Stop'].append({'hooks': [{'type': 'command', 'command': '/bin/true', 'timeout': 1}]})
        write(self.home / 'hooks.json', installed)
        with self.assertRaisesRegex(Decision, 'actual CLI review'):
            adapters.readiness(self.store, 'berman-codex')

    def test_generated_definition_matches_the_retained_template(self):
        store = self.store
        template = adapters.definition(store, 'berman-codex')
        command = template['hooks']['Stop'][0]['hooks'][0]['command']
        self.assertIn('-m berman.capture.router', command)
        self.assertNotIn(str(store.root / 'capture/router.py'), command)
        # This assertion must not depend on the operator's current installed/trusted hooks.
        (self.root / 'hooks').mkdir(exist_ok=True)
        write(self.root / 'hooks/berman-capture.hooks.json', adapters.definition(self.store, 'berman-codex'))
        write(self.home / 'hooks.json', {'hooks': {}})
        with self.assertRaisesRegex(Decision, 'capture hook differs'):
            adapters.readiness(self.store, 'berman-codex')


class ConcurrencyTests(Base):
    def test_blocked_native_check_on_one_connection_permits_another(self):
        release, entered = threading.Event(), threading.Event()
        class Blocking(FixtureHarness):
            def ensure(inner, run, folder):
                if (inner.store.record(run) or {}).get('connection', {}).get('id') == 'stewart':
                    entered.set()
                    release.wait(10)
                return FixtureHarness.ensure(inner, run, folder)
        results, errors = {}, []
        def stewart():
            try:
                store = Store(self.root)
                results['stewart'] = Workflow(store, Blocking(store)).new()
            except Exception as exc:  # surfaced in the assertion below
                errors.append(exc)
        thread = threading.Thread(target=stewart)
        thread.start()
        self.assertTrue(entered.wait(10))
        try:
            with self.assertRaisesRegex(Decision, 'Another Berman operation is using connection stewart'):
                Workflow(Store(self.root), FixtureHarness(self.store)).new()
            self.assertIn('connection stewart', Workflow(Store(self.root), FixtureHarness(self.store)).status()['operations_in_progress'])
            results['orchard'] = Workflow(Store(self.root), FixtureHarness(self.store)).new(connection='orchard')
        finally:
            release.set()
            thread.join(10)
        self.assertEqual(errors, [])
        self.assertEqual({results['stewart']['run'], results['orchard']['run']}, {'run-001', 'run-002'})

    def test_simultaneous_finishes_retain_every_inventory_entry(self):
        stewart = self.workflow.new(); self.start(stewart['run'], self.berman)
        one = self.workflow.new(connection='orchard'); self.start(one['run'], self.berman)
        barrier = threading.Barrier(2, timeout=10)
        verify = fieldwork.verify_moved
        def slow_verify(root, entry):
            result = verify(root, entry)
            if root.parent == self.root / 'runs':
                barrier.wait()  # Both migrations have moved and verified before either indexes.
            return result
        errors = []
        def finish(connection):
            try:
                store = Store(self.root)
                Workflow(store, FixtureHarness(store)).finish('ended', connection=connection)
            except Exception as exc:
                errors.append(exc)
        with mock.patch.object(fieldwork, 'verify_moved', side_effect=slow_verify):
            threads = [threading.Thread(target=finish, args=(c,)) for c in ('stewart', 'orchard')]
            for t in threads:
                t.start()
            for t in threads:
                t.join(20)
        self.assertEqual(errors, [])
        self.assertEqual(sorted(r['run'] for r in read(self.root / 'source-layout.json')['runs']), ['run-001', 'run-002'])

    def test_preserving_one_run_does_not_skip_another_sessions_callbacks(self):
        first = self.workflow.new(); self.start(first['run'], self.berman)
        second = self.workflow.new(keep_open=True)
        source, event = self.start(second['run'], self.berman, stop=False)
        entry = fieldwork.migration_entry
        observed = {}
        def during_inventory(root, run, destination):
            # A live session's callback arrives while the other packet is inventoried and hashed.
            observed['hook'] = self.berman.hook(dict(event, hook_event_name='Stop'))
            return entry(root, run, destination)
        with mock.patch.object(fieldwork, 'migration_entry', side_effect=during_inventory):
            self.workflow.finish('First engagement ended', first['run'])
        self.assertEqual(observed['hook'], {})
        self.assertEqual(read(snapshots.resolve(self.store.field / second['run'] / 'session') / 'capture.json')['event'], 'Stop')
        self.assertFalse(read(self.home / f'berman-capture/{event["session_id"]}.json')['active_turn'])

    def test_contention_names_the_occupied_run(self):
        one = self.workflow.new(connection='orchard')
        with self.store.lock('run', one['run']):
            with self.assertRaisesRegex(Decision, f'Another Berman operation is using {one["run"]}'):
                self.workflow.launch(one['run'])



class ArchiveEditionTests(Base):
    def builder(self):
        from berman.archive import builder
        return builder

    def build(self, edition, connections, builder=None):
        builder = builder or self.builder()
        build = builder.select(self.root, edition, connections)
        generated, _ = builder.render(build)
        payload = builder.assemble(build, generated)
        return build, generated, payload, builder.make_zip(payload, build.edition.prefix)

    def mixed(self):
        """One finished Stewart run and one finished orchard run in the disposable root."""
        for name in set(__import__('berman.archive.builder', fromlist=['builder']).CONTEXT_DOCUMENTS) | set(__import__('berman.archive.builder', fromlist=['builder']).EDITION_DOCUMENTS):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f'Fixture framing: {name}\n')
        for name in ('AGENTS.md', '.gitignore'):
            (self.root / name).write_text('Fixture support file.\n')
        stewart = self.workflow.new(); self.start(stewart['run'], self.berman)
        self.workflow.finish('Stewart engagement ended')
        one = self.workflow.new(connection='orchard')
        self.start(one['run'], self.berman, prompt=(Path(one['packet']) / 'ASSIGNMENT.md').read_text())
        (self.store.field / one['run'] / 'notes/account.md').write_text('An orchard account with a path /tmp/orchard-note.\n')
        self.workflow.finish('Orchard engagement ended', connection='orchard')
        return stewart, one

    def test_mixed_edition_identifies_connections_without_stewart_explanations(self):
        stewart, one = self.mixed()
        build, generated, payload, zipped = self.build('connections', ['stewart', 'orchard'])
        self.assertEqual(build.selection, {'edition': 'connections', 'connections': ['orchard', 'stewart'], 'runs': []})
        edition = json.loads(generated['review/EDITION.json'])
        self.assertEqual(edition['namespace'], 'berman-analysis/')
        self.assertEqual([(r['run'], r['connection']) for r in edition['runs']],
                         [(stewart['run'], 'stewart'), (one['run'], 'orchard')])
        self.assertTrue(all(row['attribution'] for row in edition['runs']))
        reader = generated[f'review/{one["run"]}.txt'].decode()
        self.assertTrue(reader.startswith(f'ORCHARD FIELDWORK: {one["run"]}\n\nConnection: orchard (attributed by '))
        self.assertNotIn('Stewart', reader)
        self.assertNotIn('EVIDENCE EXPORTS', reader)
        self.assertIn('INCLUDED SESSION CONVERSATION FILES', reader)
        self.assertIn('context/berman/closing-project/', reader)
        self.assertIn('An orchard account', reader)
        self.assertTrue(generated[f'review/{stewart["run"]}.txt'].decode().startswith(f'STEWART FIELDWORK: {stewart["run"]}'))
        rows = list(__import__('csv').DictReader(__import__('io').StringIO(generated['review/transcript-map.csv'].decode())))
        kinds = {r['record_kind'] for r in rows if r['run'] == one['run']}
        self.assertNotIn('rendered manager conversation', kinds)
        self.assertTrue(all(row['raw_availability'] == 'included' for row in rows))
        self.assertIn(b'DECODED NATIVE STREAMS', generated['review/events.txt'])
        guide = generated['ARCHIVE-GUIDE.txt'].decode()
        self.assertIn(f'  orchard (Orchard): {one["run"]}', guide)
        self.assertIn('Orchard: no specialized readers or coverage notes are recorded', guide)
        self.assertNotIn('Run 003', guide)
        with zipfile.ZipFile(__import__('io').BytesIO(zipped)) as archive:
            names = archive.namelist()
        self.assertTrue(all(n.startswith('berman-analysis/') for n in names))
        self.assertIn(f'berman-analysis/runs/{one["run"]}/notes/account.md', names)
        refs = generated['review/external-references.csv'].decode()
        self.assertIn('/tmp/orchard-note', refs)
        # The Stewart edition keeps Stewart runs only, with its own namespace and framing.
        build, generated, payload, zipped = self.build('stewart', [])
        self.assertEqual(build.runs, (stewart['run'],))
        self.assertEqual([r['run'] for r in json.loads(payload['source-layout.json'][0])['runs']], [stewart['run']])
        self.assertNotIn(f'runs/{one["run"]}/ASSIGNMENT.md', payload)
        with zipfile.ZipFile(__import__('io').BytesIO(zipped)) as archive:
            self.assertTrue(all(n.startswith('stewart-analysis/') for n in archive.namelist()))
        with self.assertRaisesRegex(ValueError, 'outside this edition'):
            self.builder().select(self.root, 'stewart', [], [one['run']])

    def test_editions_built_in_one_process_share_no_state(self):
        self.mixed()
        fresh_stewart = self.build('stewart', [])[1:]
        fresh_mixed = self.build('connections', ['stewart', 'orchard'])[1:]
        builder = self.builder()
        self.assertEqual(self.build('connections', ['stewart', 'orchard'], builder)[1:], fresh_mixed)
        self.assertEqual(self.build('stewart', [], builder)[1:], fresh_stewart)
        self.assertEqual(self.build('connections', ['stewart', 'orchard'], builder)[1:], fresh_mixed)

    def test_committed_snapshot_streams_use_their_preservation_manifest(self):
        stewart, orchard = self.mixed()
        build, generated, _, _ = self.build('connections', ['stewart', 'orchard'])
        blocks = [json.loads(line) for line in generated['review/decoded-blocks.jsonl'].splitlines()]
        self.assertEqual(len(blocks), 2)
        for prepared in (stewart, orchard):
            run = prepared['run']
            manifest = read(self.root / 'recordings' / run / 'manifest.json')
            stream = next(row for row in manifest['streams'] if row['format'] == 'codex-jsonl/1')
            self.assertIn('/session/snapshots/', stream['path'])
            block = next(item for item in blocks if item['run'] == run)
            self.assertEqual(block['source_path'], stream['path'])
            self.assertEqual(block['format'], stream['format'])
            self.assertEqual(block['source_sha256'], files.digest((self.root / stream['path']).read_bytes()))
            self.assertEqual(block['content'], 'done')
            self.assertIn(stream['path'].encode(), generated[f'review/{run}.txt'])
        report = self.builder().edition(build)
        self.assertEqual(self.builder().verify_edition(self.root / report['archive_path'])['freshness'], 'not-checked')

    def test_mutable_operation_changes_do_not_reassign_preserved_runs(self):
        stewart, orchard = self.mixed()
        for prepared in (stewart, orchard):
            path = self.root / 'operations' / prepared['run'] / 'operation.json'
            record = read(path)
            record['connection']['id'] = 'later-corrupt-view'
            write(path, record)
        build = self.builder().select(self.root, 'connections', ['stewart', 'orchard'])
        self.assertEqual(build.connection(stewart['run'])['id'], 'stewart')
        self.assertEqual(build.connection(orchard['run'])['id'], 'orchard')

    def test_recording_custody_replaces_a_missing_or_stale_mutable_inventory(self):
        stewart, orchard = self.mixed()
        index = self.root / 'source-layout.json'
        original = read(index)
        stale = read(index)
        item = next(row for row in stale['runs'][0]['entries'] if row['type'] == 'file')
        item['sha256'] = '0' * 64
        write(index, stale)
        selected = self.builder().select(self.root, 'connections')
        self.assertEqual(selected.layout['runs'], original['runs'])
        index.unlink()
        selected = self.builder().select(self.root, 'connections')
        self.assertEqual(set(selected.runs), {stewart['run'], orchard['run']})
        result = self.builder().edition(selected)
        self.assertFalse(index.exists())
        self.assertEqual(self.builder().verify_edition(self.root / result['archive_path'])['mode'], 'verified')

    def test_changed_retained_attribution_cannot_hide_a_run_during_selection(self):
        stewart, _ = self.mixed()
        path = self.root / 'recordings' / stewart['run'] / 'evidence/operation.json'
        authority = read(path)
        authority['connection']['id'] = 'orchard'
        write(path, authority)
        with self.assertRaisesRegex(ValueError, 'Recording evidence bytes differ'):
            self.builder().select(self.root, 'connections', ['stewart'])

    def test_default_selection_keeps_explicitly_unattributed_historical_runs(self):
        stewart, orchard = self.mixed()
        path = self.root / 'recordings' / stewart['run'] / 'manifest.json'
        manifest = read(path)
        manifest['profile'] = 'historical-import'
        manifest['evidence']['operation'] = None
        manifest['coverage']['unknown'].append('No historical operation evidence or connection attribution')
        manifest['attribution'] = {'connection': None, 'basis': 'unattributed', 'evidence': None}
        write(path, manifest)
        selected = self.builder().select(self.root, 'connections')
        self.assertEqual(set(selected.runs), {stewart['run'], orchard['run']})
        self.assertIsNone(selected.connection(stewart['run'])['id'])

    def test_manifest_attribution_must_agree_before_a_connection_can_exclude_it(self):
        stewart, _ = self.mixed()
        path = self.root / 'recordings' / stewart['run'] / 'manifest.json'
        manifest = read(path)
        manifest['attribution']['connection'] = 'orchard'
        write(path, manifest)
        with self.assertRaisesRegex(ValueError, 'Retained operation and connection attribution disagree'):
            self.builder().select(self.root, 'connections', ['stewart'])


if __name__ == '__main__':
    unittest.main()
