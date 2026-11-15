"""Operational changes exercised with disposable recordings and native metadata fixtures."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from berman import sources
from berman.archive import builder
from berman.archive.select import select
from berman.files import Decision, read, write
from berman.recipes import snapshot
from berman.records import Store
from berman.workflow import Workflow
from tests.support.sessions import start
from tests.support.workspace import FixtureHarness, git_repository, workspace


class SimplerOperationsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.root, self.project = self.base / 'study', self.base / 'project'
        (self.base / 'codex').mkdir()
        workspace(self.root, self.base, self.project, documents=['README.md', '*-PLAN.md', 'reviews/*.txt'])
        git_repository(self.project, {'README.md': 'Context\n'})
        settings = read(self.root / 'berman.json')
        settings['connections']['stewart']['plan'] = None
        write(self.root / 'berman.json', settings)
        definition = self.root / 'connections/stewart/connection.json'
        write(definition, dict(read(definition), default_recipe='work-accounts'))
        recipe = self.root / 'recipes/work-accounts/recipe.json'
        write(recipe, dict(read(recipe), connections=[]))
        self.load()

    def load(self):
        self.store = Store(self.root)
        self.workflow = Workflow(self.store, FixtureHarness(self.store))

    def session(self):
        first = self.workflow.new()
        start(self.store, first['run'], end=True)
        return first

    def test_generic_default_and_exact_repeat_use_fresh_context(self):
        self.assertEqual(self.workflow.ready()['state'], 'ready-to-prepare')
        self.assertEqual(self.store.discover(), [])
        one = self.session()
        original = self.store.record(one['run'])['recipe']
        self.workflow.finish('Done')
        (self.project / 'CURRENT-PLAN.md').write_text('New context\n')
        (self.root / 'recipes/work-accounts/INSTRUCTIONS.md').write_text('Changed {{notes_directory}}')
        two = self.workflow.new(repeat=one['run'])
        record = self.store.record(two['run'])
        self.assertEqual(record['recipe'], original)
        self.assertEqual(record['repeated_from'], one['run'])
        self.assertEqual((Path(two['preparation']) / 'initial-project/CURRENT-PLAN.md').read_text(), 'New context\n')
        self.assertEqual(self.workflow.new(repeat=one['run'])['run'], two['run'])

    def test_finish_resumes_publication_without_touching_the_recording(self):
        one = self.session()
        with patch('berman.preserve.builder.edition', side_effect=OSError('interrupted publication')):
            with self.assertRaisesRegex(OSError, 'interrupted publication'):
                self.workflow.finish('Done')
        manifest = self.root / 'recordings' / one['run'] / 'manifest.json'
        before = manifest.read_bytes()
        self.assertEqual(self.workflow.status()['runs'][0]['state'], 'reading-pending')
        result = self.workflow.finish('Done', run=one['run'])
        self.assertEqual(result['reading']['mode'], 'published and verified')
        self.assertEqual(manifest.read_bytes(), before)
        self.assertEqual(self.workflow.finish('Done', run=one['run'])['reading'], result['reading'])
        rendered, _ = builder.render(select(self.root, 'connections', runs=[one['run']]))
        reader = rendered['review/' + one['run'] + '.txt'].decode()
        self.assertNotIn('MANAGER CONVERSATION', reader)
        self.assertNotIn('SAVED PROGRAMS', reader)
        self.assertNotIn('EVIDENCE EXPORTS', reader)

    def test_opaque_history_does_not_gate_new_work_or_selected_editions(self):
        one = self.session()
        self.workflow.finish('Done')
        settings = read(self.root / 'berman.json')
        settings['historical_runs'] = [one['run']]
        write(self.root / 'berman.json', settings)
        # An incompatible historical manifest remains bytes, not a runtime dependency.
        (self.root / 'recordings' / one['run'] / 'manifest.json').write_text('future unsupported schema')
        self.load()
        two = self.session()
        self.assertEqual(two['run'], 'run-002')
        result = self.workflow.finish('Done again')
        self.assertEqual(result['reading']['mode'], 'published and verified')
        self.assertEqual(select(self.root, 'connections').runs, ('run-002',))
        self.assertEqual(self.workflow.status()['historical_runs'], ['run-001'])

    def test_edition_retention_keeps_current_previous_and_originals(self):
        one = self.session()
        first = self.workflow.finish('Done')['reading']
        original = (self.root / 'recordings' / one['run'] / 'manifest.json').read_bytes()
        write(self.root / 'edition.json', {'schema': 'berman.edition/1', 'documents': ['context.txt']})
        results = [first]
        for text in ('second', 'third'):
            (self.root / 'context.txt').write_text(text)
            results.append(builder.edition(select(self.root, 'connections', runs=[one['run']])))
        self.assertFalse((self.root / results[0]['archive_path']).exists())
        self.assertTrue(all((self.root / result['archive_path']).exists() for result in results[1:]))
        self.assertEqual((self.root / 'recordings' / one['run'] / 'manifest.json').read_bytes(), original)
        self.assertEqual(
            self.workflow.finish('Done', run=one['run'])['reading']['archive_sha256'], results[-1]['archive_sha256']
        )

    def test_rule_changes_invalidate_the_native_access_fingerprint(self):
        from berman.harnesses.codex import Codex

        one = self.workflow.new()
        harness = Codex(self.store)
        before = harness.fingerprint(one['run'], self.project, 'berman-codex', [])
        rules = self.store.home / 'rules/default.rules'
        rules.parent.mkdir(parents=True)
        rules.write_text('prefix_rule(pattern=["git", "add"], decision="allow")')
        after = harness.fingerprint(one['run'], self.project, 'berman-codex', [])
        self.assertNotEqual(before, after)
        rules.unlink()
        self.assertEqual(before, harness.fingerprint(one['run'], self.project, 'berman-codex', []))

    def test_selectors_are_reexpanded_and_symlink_escapes_refused(self):
        with self.assertRaisesRegex(ValueError, 'Invalid selected document'):
            snapshot(self.project, ['**/*.md'], self.base / 'recursive')
        first = snapshot(self.project, ['*-PLAN.md'], self.base / 'first')
        self.assertEqual(first['files'], [{'path': '*-PLAN.md', 'missing': True}])
        (self.project / 'NEW-PLAN.md').write_text('Plan')
        second = snapshot(self.project, ['*-PLAN.md'], self.base / 'second')
        self.assertEqual(second['files'][0]['path'], 'NEW-PLAN.md')
        (self.project / 'outside').symlink_to(self.base, target_is_directory=True)
        (self.base / 'secret.txt').write_text('secret')
        with self.assertRaisesRegex(Decision, 'symlink'):
            snapshot(self.project, ['outside/*.txt'], self.base / 'third')


class AssociatedSourcesTests(unittest.TestCase):
    def test_parent_guardian_descendant_selection_and_retry(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp).resolve()
            home = base / 'codex'
            sessions = home / 'sessions'

            def native(name, payload):
                write(sessions / name, {'type': 'session_meta', 'payload': payload})
                # Native JSONL metadata occupies one physical line.
                import json

                (sessions / name).write_text(json.dumps({'type': 'session_meta', 'payload': payload}) + '\n')

            native(
                'reviewer.jsonl',
                {'id': 'reviewer', 'source': {'subagent': {'thread_spawn': {'parent_thread_id': 'parent'}}}},
            )
            native(
                'guardian.jsonl',
                {'session_id': 'guardian', 'parent_thread_id': 'parent', 'source': {'subagent': {'other': 'guardian'}}},
            )
            native('descendant.jsonl', {'id': 'descendant', 'forked_from_id': 'reviewer'})
            native('unrelated.jsonl', {'id': 'other', 'parent_thread_id': 'unrelated'})
            target = base / 'associated'
            result = sources.collect(home, 'parent', 'codex', target)
            self.assertEqual({r['session_id'] for r in result['sources']}, {'guardian', 'reviewer', 'descendant'})
            native('later.jsonl', {'id': 'later', 'parent_thread_id': 'parent'})
            self.assertEqual(sources.collect(home, 'parent', 'codex', target), result)
            (target / result['sources'][0]['path']).write_text('damage')
            with self.assertRaisesRegex(Decision, 'changed'):
                sources.collect(home, 'parent', 'codex', target)
