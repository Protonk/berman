"""Per-run editions, opaque history, edition retention and bounded collection, on disposable recordings."""

from pathlib import Path
import tempfile
import unittest

from berman import records, sources
from berman.archive import builder
from berman.archive.package import verify_published
from berman.archive.select import select, selection
from berman.files import Decision, read, write
from berman.recipes import snapshot
from berman.records import Store
from tests.support import legacy
from tests.support.workspace import git_repository, workspace


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

    def finished(self):
        """One preserved run with its per-run reading edition, as a 0.26.3 closeout left it."""
        run = legacy.prepare(self.store, recipe='work-accounts')
        legacy.start(self.store, run, end=True)
        return run, legacy.finish(self.store, run, 'Done')

    def test_a_per_run_edition_of_a_notes_packet_has_no_stewart_sections(self):
        run, result = self.finished()
        self.assertEqual(result['reading']['mode'], 'published and verified')
        self.assertEqual(records.status(self.store)['unsettled'], [])
        rendered, _ = builder.render(select(self.root, 'connections', runs=[run]))
        reader = rendered['review/' + run + '.txt'].decode()
        self.assertNotIn('MANAGER CONVERSATION', reader)
        self.assertNotIn('SAVED PROGRAMS', reader)
        self.assertNotIn('EVIDENCE EXPORTS', reader)

    def test_opaque_history_is_left_out_of_discovery_and_default_editions(self):
        one, _ = self.finished()
        settings = read(self.root / 'berman.json')
        settings['historical_runs'] = [one]
        write(self.root / 'berman.json', settings)
        # An incompatible historical manifest remains bytes, not a runtime dependency.
        (self.root / 'recordings' / one / 'manifest.json').write_text('future unsupported schema')
        self.load()
        two, result = self.finished()
        self.assertEqual(two, 'run-002')
        self.assertEqual(result['reading']['mode'], 'published and verified')
        self.assertEqual(select(self.root, 'connections').runs, ('run-002',))
        status = records.status(self.store)
        self.assertEqual((status['historical_runs'], status['archived'], status['runs']), (['run-001'], 2, []))

    def test_edition_retention_keeps_current_previous_and_originals(self):
        run, result = self.finished()
        original = (self.root / 'recordings' / run / 'manifest.json').read_bytes()
        write(self.root / 'edition.json', {'schema': 'berman.edition/1', 'documents': ['context.txt']})
        results = [result['reading']]
        for text in ('second', 'third'):
            (self.root / 'context.txt').write_text(text)
            results.append(builder.edition(select(self.root, 'connections', runs=[run])))
        self.assertFalse((self.root / results[0]['archive_path']).exists())
        self.assertTrue(all((self.root / result['archive_path']).exists() for result in results[1:]))
        self.assertEqual((self.root / 'recordings' / run / 'manifest.json').read_bytes(), original)
        current = verify_published(self.root, selection('connections', runs=[run]))
        self.assertEqual(current['archive_sha256'], results[-1]['archive_sha256'])

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
