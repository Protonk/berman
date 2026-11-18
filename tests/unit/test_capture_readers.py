"""Native transcript parsing, installed hook recognition and project check arguments."""

from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from berman import executor
from berman.executor import capture, checkout, claude, codex
from berman.files import Decision


class ReaderTests(unittest.TestCase):
    def test_claude_reader_keeps_tool_provenance_and_malformed_lines(self):
        sid = '5f0c3f6e-1c6f-4f0e-9d0b-2a8a2a3b4c5d'
        raw = (
            json.dumps(
                {
                    'sessionId': sid,
                    'message': {
                        'role': 'assistant',
                        'content': [
                            {'type': 'tool_use', 'id': 'call-1', 'name': 'Read', 'input': {'file_path': '/test'}},
                            {'type': 'text', 'text': 'answer'},
                        ],
                    },
                }
            ).encode()
            + b'\ninvalid\n'
        )
        reader, messages, malformed, identities = claude.render(raw)
        self.assertIn(b'/message/content/0/input', reader)
        self.assertIn(b'call-1', reader)
        self.assertEqual((messages, malformed, identities), (['answer'], [2], {sid}))

    def test_claude_callbacks_are_the_seven_session_events(self):
        self.assertEqual(
            claude.EVENTS,
            ('SessionStart', 'UserPromptSubmit', 'Stop', 'StopFailure', 'PreCompact', 'PostCompact', 'SessionEnd'),
        )

    def test_codex_reader_bytes_messages_and_malformed_lines(self):
        raw = (
            json.dumps({'type': 'session_meta', 'payload': {'id': 'fixture'}})
            + '\n'
            + json.dumps(
                {
                    'type': 'response_item',
                    'payload': {'type': 'message', 'role': 'assistant', 'content': [{'text': 'done'}]},
                }
            )
            + '\nnot json\n'
        ).encode()
        reader, messages, malformed = codex.render(raw)
        self.assertTrue(reader.startswith(b'CODEX FIELDWORK SESSION: DERIVED READING COPY\n'))
        self.assertIn(b'SOURCE: transcript.jsonl#L2 /payload/content/0/text', reader)
        self.assertEqual((messages, malformed), (['done'], [3]))


class HookRecognitionTests(unittest.TestCase):
    def test_duplicate_or_conflicting_handlers_block_launch(self):
        def group(command):
            return {'hooks': [{'type': 'command', 'command': command}]}

        router = group('python -I -m berman.executor --spool /spool hook --home /codex')
        expected = {'hooks': {'Stop': [router]}}
        ordinary = group('/usr/local/bin/notify --on-stop')
        executor.verify_hooks({'hooks': {'Stop': [ordinary, router]}}, expected)
        commands = [
            router['hooks'][0]['command'],
            'python -m berman.capture.router hook',
            'python /install/berman_capture_router.py hook',
            'python /install/berman_collector.py hook',
            'python "/shared tools/capture_session.py" hook',
        ]
        for command in commands:
            with self.subTest(command=command), self.assertRaisesRegex(ValueError, 'exclusive executor router'):
                executor.verify_hooks({'hooks': {'Stop': [router, ordinary, group(command)]}}, expected)

    def test_codex_is_asked_for_the_timeouts_it_grants(self):
        # Codex clamps SessionEnd and Interrupt hooks to three seconds and reports a larger request
        # as an issue in its /hooks review. Claude's per-launch settings have no such limit.
        asked = {
            event: groups[0]['hooks'][0]['timeout']
            for event, groups in capture.definition('/spool', '/codex')['hooks'].items()
        }
        self.assertEqual({'UserPromptSubmit': 30, 'Stop': 30, 'Interrupt': 3, 'SessionEnd': 3}, asked)
        operation = '29525f31-3e9d-4c3d-9078-cedd71216be9'
        for groups in capture.definition('/spool', '/claude', operation)['hooks'].values():
            self.assertEqual(30, groups[0]['hooks'][0]['timeout'])

    def test_merging_replaces_every_berman_handler_and_keeps_everything_else(self):
        def group(command, **extra):
            return {'hooks': [{'type': 'command', 'command': command, **extra}]}

        expected = capture.definition('/spool', '/codex')
        ordinary = group('/usr/local/bin/notify --on-stop')
        earlier = group('/old/env/bin/python -I -B -m berman.executor --spool /spool hook --home /codex', timeout=30)
        installed = {
            'description': 'User-level Codex hooks',
            'hooks': {
                'Stop': [earlier, ordinary],
                'SessionEnd': [group('python -m berman.capture.router hook')],
                'PreToolUse': [ordinary],
            },
        }
        merged = executor.merged_hooks(installed, expected)
        executor.verify_hooks(merged, expected)
        self.assertEqual('User-level Codex hooks', merged['description'])
        self.assertEqual([ordinary, *expected['hooks']['Stop']], merged['hooks']['Stop'])
        self.assertEqual(expected['hooks']['SessionEnd'], merged['hooks']['SessionEnd'])
        self.assertEqual([ordinary], merged['hooks']['PreToolUse'])
        self.assertEqual(expected, executor.merged_hooks({}, expected))
        self.assertEqual(merged, executor.merged_hooks(merged, expected))

    def test_the_merge_command_prints_the_whole_file_to_install(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder).resolve() / 'codex'
            home.mkdir()
            ordinary = {'hooks': [{'type': 'command', 'command': '/usr/local/bin/notify'}]}
            (home / 'hooks.json').write_text(json.dumps({'hooks': {'Stop': [ordinary]}}))
            printed = StringIO()
            with redirect_stdout(printed):
                code = executor.main(['--spool', folder + '/spool', 'codex-hooks', '--home', str(home), '--merge'])
            merged = json.loads(printed.getvalue())
            self.assertEqual(0, code)
            self.assertEqual(ordinary, merged['hooks']['Stop'][0])
            executor.verify_hooks(merged, capture.definition(folder + '/spool', home))
            self.assertEqual({'hooks': {'Stop': [ordinary]}}, json.loads((home / 'hooks.json').read_text()))


class ProjectCheckTests(unittest.TestCase):
    VALUES = {'python': '/py', 'project': '/project', 'packet': '/p'}

    def test_declared_checks_resolve_to_explicit_arguments(self):
        declared = [
            {
                'id': 'metadata',
                'argv': ['{python}', '-c', 'pass', '{executable:cargo}', '{project}/service'],
                'timeout': '45',
            },
            {'id': 'startup', 'argv': ['{executable:rustc}', '--version'], 'stdout_contains': 3},
        ]
        with mock.patch.object(checkout.shutil, 'which', side_effect=lambda name: '/bin/' + name):
            resolved = checkout.project_checks(declared, self.VALUES)
        self.assertEqual(
            resolved,
            [
                {'id': 'metadata', 'argv': ['/py', '-c', 'pass', '/bin/cargo', '/project/service'], 'timeout': 45},
                {'id': 'startup', 'argv': ['/bin/rustc', '--version'], 'timeout': 30, 'stdout_contains': '3'},
            ],
        )

    def test_project_checks_cannot_replace_another_observation(self):
        for ids in [('invalid-name',), ('',), ('project_tool', 'project_tool')]:
            with self.subTest(ids=ids), self.assertRaisesRegex(ValueError, 'Invalid project check identity'):
                checkout.project_checks([{'id': ident, 'argv': ['true']} for ident in ids], {})

    def test_missing_executables_unknown_placeholders_and_timeouts_are_refused(self):
        with mock.patch.object(checkout.shutil, 'which', return_value=None):
            with self.assertRaisesRegex(Decision, 'needs the cargo executable for metadata'):
                checkout.project_checks([{'id': 'metadata', 'argv': ['{executable:cargo}']}], self.VALUES)
        with self.assertRaisesRegex(ValueError, 'Unknown placeholder in project check metadata'):
            checkout.project_checks([{'id': 'metadata', 'argv': ['{home}']}], self.VALUES)
        for timeout in (0, 121):
            with self.subTest(timeout=timeout), self.assertRaisesRegex(ValueError, 'between 1 and 120 seconds'):
                checkout.project_checks([{'id': 'metadata', 'argv': ['true'], 'timeout': timeout}], self.VALUES)


if __name__ == '__main__':
    unittest.main()
