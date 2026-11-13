"""Claude-specific boundaries and native-format preservation, without model calls."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import berman_capture
import berman_claude
import berman_claude_support as support
import berman_support
from berman_claude_capture import MARKER, render
from berman_claude_router import access
from berman_files import Decision, read, write
from berman_records import Store
from test_berman import workspace, git_repository


class ClaudeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name).resolve()
        self.root, self.project = base / 'berman', base / 'project'
        git_repository(self.project, {'README.md': 'project\n'})
        workspace(self.root, base, self.project, documents=['README.md'])
        config = read(self.root / 'berman.json')
        config['claude_home'] = str(base / 'claude')
        write(self.root / 'berman.json', config)
        self.store = Store(self.root)
        with mock.patch.object(berman_claude, 'configuration', return_value={'fixture': True}):
            self.trial = support.prepare(self.store, 'experiments/claude-test')
        self.run = self.trial['run']
        self.record = self.store.record(self.run)
        self.sid = self.record['harness']['session_id']
        self.packet = Path(self.record['packet'])
        self.collector = self.store.collector_for(self.record)
        self.source = self.store.claude_home / 'projects/test' / (self.sid + '.jsonl')
        self.source.parent.mkdir(parents=True)
        self.message('user', MARKER + self.run, fresh=True)

    def message(self, role, text, fresh=False):
        with self.source.open('w' if fresh else 'a') as stream:
            stream.write(json.dumps({'sessionId': self.sid, 'type': role,
                                     'message': {'role': role, 'content': text}}) + '\n')

    def hook(self, name, **extra):
        return self.collector.hook({'hook_event_name': name, 'session_id': self.sid,
                                    'transcript_path': str(self.source), 'cwd': str(self.project), **extra})

    def register(self):
        self.hook('UserPromptSubmit', prompt=MARKER + self.run)

    def test_reserved_identity_marker_and_parent_only(self):
        self.hook('SessionStart')
        self.assertFalse((self.packet / 'session').exists())
        with self.assertRaisesRegex(ValueError, 'first prompt'):
            self.hook('UserPromptSubmit', prompt='not the assignment')
        with self.assertRaisesRegex(ValueError, 'reserved'):
            self.hook('UserPromptSubmit', session_id='00000000-0000-0000-0000-000000000000', prompt=MARKER + self.run)
        self.register()
        self.assertTrue(self.collector.status(self.run)['prompt_without_terminal_callback'])
        with self.assertRaisesRegex(ValueError, 'not supported'):
            self.collector.command('replace', self.run)

    def test_round_trip_finalize_collect_and_retire(self):
        folder = self.root / self.trial['evidence']
        trial = read(folder / 'trial.json')
        trial['phase'] = 'launching'
        write(folder / 'trial.json', trial)
        self.register()
        self.message('assistant', trial['expected_reply'])
        self.hook('Stop', last_assistant_message=trial['expected_reply'])
        self.hook('SessionEnd', reason='prompt_input_exit')
        result = berman_support.collect(self.store, self.trial['evidence'])
        self.assertTrue(result['demonstrates_capture'])
        preserved = self.root / result['preserved_packet']
        self.assertEqual((preserved / 'session/transcript.jsonl').read_bytes(), self.source.read_bytes())
        self.assertIn('SOURCE: transcript.jsonl#L2 /message/content', (preserved / 'session/conversation.txt').read_text())
        cleaned = berman_support.cleanup(self.store, self.trial['evidence'], 'fixture completed')
        self.assertEqual(cleaned['phase'], 'cleaned')
        self.assertTrue(read(self.collector.registry(self.sid))['retired'])
        self.assertTrue(self.project.exists())

    def test_no_terminal_and_partial_or_foreign_source_cannot_finalize(self):
        self.register()
        self.message('assistant', 'reply')
        with self.assertRaisesRegex(ValueError, 'Not finalized'):
            self.collector.command('finalize', self.run)
        self.hook('Stop', last_assistant_message='reply')
        with self.source.open('ab') as out:
            out.write(b'{"partial":')
        with self.assertRaisesRegex(ValueError, 'Partial'):
            self.collector.command('finalize', self.run)
        self.message('assistant', 'changed', fresh=True)
        self.source.write_text(self.source.read_text().replace(self.sid, 'another-session'))
        with self.assertRaisesRegex(ValueError, 'identity'):
            self.collector.command('refresh', self.run)

    def test_frozen_capture_supplement_keeps_original(self):
        self.register()
        self.message('assistant', 'reply')
        self.hook('Stop', last_assistant_message='reply')
        self.collector.command('finalize', self.run)
        frozen = (self.packet / 'session/transcript.jsonl').read_bytes()
        self.message('assistant', 'later')
        self.hook('SessionEnd')
        result = self.collector.command('supplement', self.run, reason='later persisted activity')
        self.assertEqual((self.packet / 'session/transcript.jsonl').read_bytes(), frozen)
        self.assertIn(b'later', (Path(result['path']) / 'session/transcript.jsonl').read_bytes())

    def test_file_tools_cover_aliases_ancestors_and_packet_exception(self):
        def allowed(tool, **args):
            return not access({'tool_name': tool, 'tool_input': args, 'cwd': str(self.project)}, self.root, self.record)
        self.assertTrue(allowed('Read', file_path=str(self.packet / 'INSTRUCTIONS.md')))
        self.assertTrue(allowed('Grep', path=str(self.packet), pattern='anything'))
        self.assertTrue(allowed('Read', file_path='README.md'))
        self.assertFalse(allowed('Read', file_path=str(self.root / 'secret')))
        self.assertFalse(allowed('Write', file_path=str(self.store.field / 'run-999/notes/new')))
        self.assertFalse(allowed('Grep', path=str(self.root.parent), pattern='anything'))
        self.assertFalse(allowed('Glob', path=str(self.project), pattern='../berman/**'))
        alias = self.project / 'alias'
        alias.symlink_to(self.root)
        self.assertFalse(allowed('Read', file_path=str(alias / 'secret')))
        self.assertFalse(allowed('Read', file_path=str(self.root).upper() + '/secret'))

    def test_source_refuses_symlink_and_outside_home(self):
        alias = self.source.parent / 'alias.jsonl'
        alias.symlink_to(self.source)
        with self.assertRaises(ValueError):
            self.collector.source(str(alias))
        with self.assertRaises(ValueError):
            self.collector.source(str(self.project / 'session.jsonl'))

    def test_reader_keeps_tool_provenance_and_malformed_lines(self):
        raw = json.dumps({'sessionId': self.sid, 'message': {'role': 'assistant', 'content': [
            {'type': 'tool_use', 'id': 'call-1', 'name': 'Read', 'input': {'file_path': '/test'}},
            {'type': 'text', 'text': 'answer'}]}}).encode() + b'\ninvalid\n'
        reader, messages, malformed, identities = render(raw)
        self.assertIn(b'/message/content/0/input', reader)
        self.assertIn(b'call-1', reader)
        self.assertEqual((messages, malformed, identities), (['answer'], [2], {self.sid}))

    def test_native_assessment_requires_actual_matching_calls(self):
        result = {'schema': 'berman.access-probe/1', 'token': 'token', 'checks': {'check': True}}
        stream = json.dumps({'message': {'content': [{'type': 'text', 'text': json.dumps(result)}]}})
        self.assertFalse(support.assess(stream, 'probe', 'token', {'check'}, {})['passed'])
        rows = [{'message': {'content': [{'type': 'tool_use', 'id': '1', 'name': 'Bash', 'input': {'command': 'probe'}}]}},
                {'message': {'content': [{'type': 'tool_result', 'tool_use_id': '1', 'content': json.dumps(result)}]}}]
        stream = '\n'.join(json.dumps(r) for r in rows)
        self.assertTrue(support.assess(stream, 'probe', 'token', {'check'}, {})['passed'])
        rows[0]['message']['content'][0]['input']['dangerouslyDisableSandbox'] = True
        self.assertFalse(support.assess('\n'.join(json.dumps(r) for r in rows), 'probe', 'token', {'check'}, {})['passed'])
        spec = {'tool': 'Grep', 'input': {'path': '/denied', 'pattern': 'token'}, 'allowed': False}
        rows[0]['message']['content'][0] = {'type': 'tool_use', 'id': '2', 'name': 'Grep', 'input': spec['input']}
        rows[1]['message']['content'][0] = {'type': 'tool_result', 'tool_use_id': '2', 'is_error': True,
                                          'content': 'Error: No such tool available: Grep'}
        observed = support.assess('\n'.join(json.dumps(r) for r in rows), 'probe', 'token', {'check'}, {'denial': spec})
        self.assertFalse(observed['checks']['denial'])

    def test_new_needs_explicit_qualification_and_generated_settings_match(self):
        with self.assertRaisesRegex(Decision, 'no qualified'):
            berman_claude.readiness(self.store, self.project)
        settings_path = berman_claude.settings_path(self.store, self.run)
        settings = read(settings_path)
        self.assertEqual(settings['sandbox']['filesystem']['allowRead'], [str(self.packet)])
        self.assertFalse(settings['sandbox']['allowUnsandboxedCommands'])
        self.assertEqual(settings['hooks']['PreToolUse'][0]['hooks'][0]['onFailure'], 'block')
        settings['sandbox']['enabled'] = False
        write(settings_path, settings)
        with self.assertRaisesRegex(Decision, 'differ'):
            berman_claude.Claude(self.store).argv(self.run)

    def test_model_is_pinned_in_the_reserved_launch(self):
        self.record['harness']['model'] = 'claude-opus-5-5'
        write(self.store.folder(self.run) / 'operation.json', self.record)
        argv = berman_claude.Claude(self.store).argv(self.run)
        self.assertEqual(argv[argv.index('--model') + 1], 'claude-opus-5-5')

    def configure(self, settings_file):
        """The real configuration() against one local settings file, with the CLI and conflicts mocked."""
        self.store.claude = Path(sys.executable)
        with mock.patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': str(self.store.claude_home)}), \
             mock.patch.object(berman_claude, 'claude_sources', return_value={settings_file: None}), \
             mock.patch.object(berman_claude, 'claude_conflicts', return_value=[]), \
             mock.patch.object(berman_claude.subprocess, 'run', return_value=mock.Mock(stdout='fixture version')):
            return berman_claude.configuration(self.store, self.project)

    def test_configuration_separates_policy_keys_harness_and_context(self):
        settings_file = self.root.parent / 'user-settings.json'
        write(settings_file, {'theme': 'dark'})
        before = self.configure(settings_file)
        self.assertEqual(before['schema'], berman_claude.CONFIGURATION)
        self.assertEqual(before['policy']['settings'], {str(settings_file): None})
        self.assertEqual(before['policy']['template'], berman_claude.definition())
        self.assertEqual(before['harness']['version'], 'fixture version')
        self.assertIn('project_checks', before['context'])
        write(settings_file, {'theme': 'light', 'statusLine': {'type': 'command'}})
        cosmetic = self.configure(settings_file)
        self.assertEqual(cosmetic['policy'], before['policy'])
        self.assertNotEqual(cosmetic['context']['settings_sha256'], before['context']['settings_sha256'])
        self.store.connection().definition['checks'] = [{'id': 'changed', 'argv': ['true']}]
        self.assertEqual(self.configure(settings_file)['policy'], before['policy'])
        write(settings_file, {'theme': 'light', 'sandbox': {'enabled': False}})
        self.assertNotEqual(self.configure(settings_file)['policy']['settings'], before['policy']['settings'])
        write(settings_file, {'disableAllHooks': True})
        with self.assertRaisesRegex(Decision, 'may be disabled'):
            self.configure(settings_file)

    def qualify(self, configuration, **changes):
        basis = {'schema': berman_claude.SCHEMA, 'adapter': 'berman-claude',
                 'implementation_sha256': berman_capture.implementation('berman-claude'),
                 'helpers_sha256': berman_claude.helpers(), 'evidence_sha256': {}, 'hook_review': None,
                 'configuration': configuration, 'successful_capture': {'run': 'run-900101'}, 'meaning': 'fixture'}
        write(self.root / berman_claude.BASIS, dict(basis, **changes))

    def test_readiness_tiers_stop_warn_and_ignore(self):
        qualified = {'schema': berman_claude.CONFIGURATION,
                     'policy': {'project': str(self.project), 'template': berman_claude.definition(),
                                'settings': {'/home/settings.json': 'policy-a', '/home/settings.local.json': None}},
                     'harness': {'binary': '/versions/2.1.312', 'version': '2.1.312 (Claude Code)',
                                 'binary_size': 1, 'binary_mtime_ns': 1},
                     'context': {'project_checks': {'stewart': [{'id': 'old'}]}, 'settings_sha256': {'/home/settings.json': 'x'}}}
        self.qualify(qualified)

        def current(**changes):
            value = copy.deepcopy(qualified)
            for section, update in changes.items():
                value[section].update(update)
            return value

        with mock.patch.object(berman_claude, 'configuration', return_value=current()):
            self.assertEqual(berman_claude.readiness(self.store, self.project)['warnings'], [])
        context = current(context={'project_checks': {'stewart': [{'id': 'new'}]}, 'settings_sha256': {'/home/settings.json': 'y'}})
        with mock.patch.object(berman_claude, 'configuration', return_value=context):
            self.assertEqual(berman_claude.readiness(self.store, self.project)['warnings'], [])
        harness = current(harness={'binary': '/versions/2.1.316', 'version': '2.1.316 (Claude Code)'})
        with mock.patch.object(berman_claude, 'configuration', return_value=harness):
            result = berman_claude.readiness(self.store, self.project)
            self.assertEqual(len(result['warnings']), 1)
            self.assertIn('2.1.312 (Claude Code) at /versions/2.1.312 is now 2.1.316', result['warnings'][0])
            self.assertEqual(result['harness']['current']['version'], '2.1.316 (Claude Code)')
        policy = current(policy={'settings': {'/home/settings.json': 'policy-b', '/home/settings.local.json': None}})
        with mock.patch.object(berman_claude, 'configuration', return_value=policy), \
             self.assertRaisesRegex(Decision, r'policy or hook template changed.*settings:/home/settings\.json'):
            berman_claude.readiness(self.store, self.project)
        template = current(policy={'template': dict(berman_claude.definition(), events=['Stop'])})
        with mock.patch.object(berman_claude, 'configuration', return_value=template), \
             self.assertRaisesRegex(Decision, 'template'):
            berman_claude.readiness(self.store, self.project)
        # Without a project (another workspace's verify) the policy is not compared and no warning is raised.
        self.assertEqual(berman_claude.readiness(self.store)['warnings'], [])

    def test_readiness_stops_on_implementation_helpers_evidence_and_old_schema(self):
        qualified = {'schema': berman_claude.CONFIGURATION, 'policy': {}, 'harness': {}, 'context': {}}
        self.qualify(qualified, schema='berman.claude-verification/1', hook_review='fixture')
        with self.assertRaisesRegex(Decision, 'predates tiered receipts'):
            berman_claude.readiness(self.store)
        self.qualify(qualified, implementation_sha256={'tools/berman_claude_router.py': 'other'})
        with self.assertRaisesRegex(Decision, 'in-session implementation changed'):
            berman_claude.readiness(self.store)
        self.qualify(qualified, helpers_sha256={'tools/development_launchers/launch.py:claude_sources': 'other'})
        with self.assertRaisesRegex(Decision, 'helpers changed'):
            berman_claude.readiness(self.store)
        evidence = self.root / 'experiments/fixture/evidence.txt'
        write(evidence, {'fixture': True})
        self.qualify(qualified, evidence_sha256={'experiments/fixture/evidence.txt': 'other'})
        with self.assertRaisesRegex(Decision, 'evidence changed'):
            berman_claude.readiness(self.store)
        self.qualify(qualified, successful_capture=None)
        with self.assertRaisesRegex(Decision, 'native capture'):
            berman_claude.readiness(self.store)
        self.qualify(qualified)
        self.assertEqual(berman_claude.readiness(self.store)['tiers'], berman_claude.TIERS)
        self.assertNotIn('tools/development_launchers/launch.py', berman_capture.ADAPTERS['berman-claude']['implementation'])
        self.assertIn('tools/development_launchers/launch.py:allow_read_conflicts', berman_claude.helpers())


if __name__ == '__main__':
    unittest.main()
