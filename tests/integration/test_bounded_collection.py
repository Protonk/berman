"""Context snapshots and bounded collection on a disposable checkout."""

from pathlib import Path
import tempfile
import unittest
from berman.files import Decision, write
from berman.executor import sources
from berman.executor.checkout import snapshot
from tests.support.project import git_repository


class ContextTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.project = self.base / 'project'
        git_repository(self.project, {'README.md': 'Context\n'})

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
    def test_parent_guardian_descendant_selection(self):
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
