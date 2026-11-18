"""Native transcript parsing, installed hook recognition and project check arguments."""

import json
import unittest
from unittest import mock

from berman import checks, executor
from berman.capture import claude, codex
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


class ProjectCheckTests(unittest.TestCase):
    VALUES = {'python': '/py', 'project': '/stewart', 'packet': '/p'}

    def test_declared_checks_resolve_to_explicit_arguments(self):
        declared = [
            {
                'id': 'metadata',
                'argv': ['{python}', '-c', 'pass', '{executable:cargo}', '{project}/service'],
                'timeout': '45',
            },
            {'id': 'startup', 'argv': ['{executable:rustc}', '--version'], 'stdout_contains': 3},
        ]
        with mock.patch.object(checks.shutil, 'which', side_effect=lambda name: '/bin/' + name):
            resolved = checks.project_checks(declared, self.VALUES)
        self.assertEqual(
            resolved,
            [
                {'id': 'metadata', 'argv': ['/py', '-c', 'pass', '/bin/cargo', '/stewart/service'], 'timeout': 45},
                {'id': 'startup', 'argv': ['/bin/rustc', '--version'], 'timeout': 30, 'stdout_contains': '3'},
            ],
        )

    def test_project_checks_cannot_replace_another_observation(self):
        for ids in [('invalid-name',), ('',), ('project_tool', 'project_tool')]:
            with self.subTest(ids=ids), self.assertRaisesRegex(ValueError, 'Invalid project check identity'):
                checks.project_checks([{'id': ident, 'argv': ['true']} for ident in ids], {})

    def test_missing_executables_unknown_placeholders_and_timeouts_are_refused(self):
        with mock.patch.object(checks.shutil, 'which', return_value=None):
            with self.assertRaisesRegex(Decision, 'needs the cargo executable for metadata'):
                checks.project_checks([{'id': 'metadata', 'argv': ['{executable:cargo}']}], self.VALUES)
        with self.assertRaisesRegex(ValueError, 'Unknown placeholder in project check metadata'):
            checks.project_checks([{'id': 'metadata', 'argv': ['{home}']}], self.VALUES)
        for timeout in (0, 121):
            with self.subTest(timeout=timeout), self.assertRaisesRegex(ValueError, 'between 1 and 120 seconds'):
                checks.project_checks([{'id': 'metadata', 'argv': ['true'], 'timeout': timeout}], self.VALUES)


if __name__ == '__main__':
    unittest.main()
