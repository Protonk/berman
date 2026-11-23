"""Read native output recorded from the actual CLIs, reduced to the fields Berman reads.

Each sample names the CLI version and situation that produced it. Add a sample when a harness
changes the output an unattended sitting reports.
"""

import json
import unittest

from berman import claude, codex


def jsonl(events):
    return ''.join(json.dumps(event) + '\n' for event in events).encode()


# codex-cli 0.171.0, codex exec --json, signed out: the turn fails after reconnection attempts.
CODEX_SIGNED_OUT = [
    {'type': 'thread.started', 'thread_id': '01a204d2-9f33-7941-b932-2b13ecac3758'},
    {'type': 'turn.started'},
    {'type': 'error', 'message': 'Reconnecting... 2/5 (unexpected status 401 Unauthorized)'},
    {
        'type': 'item.completed',
        'item': {'id': 'item_0', 'type': 'error', 'message': 'Falling back from WebSockets to HTTPS transport.'},
    },
    {'type': 'error', 'message': 'Reconnecting... 5/5 (unexpected status 401 Unauthorized)'},
    {'type': 'error', 'message': 'unexpected status 401 Unauthorized'},
    {'type': 'turn.failed', 'error': {'message': 'unexpected status 401 Unauthorized'}},
]

# codex-cli 0.171.0, codex exec --json: a progress message, two commands, then the final message.
CODEX_COMPLETED = [
    {'type': 'thread.started', 'thread_id': '01a205c0-8c55-7a03-9308-146f8fd19681'},
    {'type': 'turn.started'},
    {'type': 'item.completed', 'item': {'id': 'item_0', 'type': 'agent_message', 'text': 'I’ll inspect the files.'}},
    {
        'type': 'item.started',
        'item': {
            'id': 'item_1',
            'type': 'command_execution',
            'command': 'ls -a',
            'exit_code': None,
            'status': 'in_progress',
        },
    },
    {
        'type': 'item.completed',
        'item': {
            'id': 'item_1',
            'type': 'command_execution',
            'command': 'ls -a',
            'exit_code': 0,
            'status': 'completed',
        },
    },
    {'type': 'item.completed', 'item': {'id': 'item_3', 'type': 'agent_message', 'text': 'A conversion module.'}},
    {'type': 'turn.completed', 'usage': {}},
]

# Claude Code 2.1.330, --print --output-format json, default permissions: an edit was denied.
CLAUDE_DENIED = {
    'type': 'result',
    'subtype': 'success',
    'is_error': False,
    'result': "I didn't fix the bug or run the tests, because both actions were blocked.",
    'session_id': 'c2b74254-6c8f-4745-a2b2-f30f4822b6d3',
    'errors': None,
    'stop_reason': 'end_turn',
    'terminal_reason': 'completed',
    'permission_denials': [
        {
            'tool_name': 'Edit',
            'tool_use_id': 'toolu_01PVQW3jnNoSuVh1spPM6UW6',
            'tool_input': {'file_path': '/home/berman-dev/trials/project/convert.py'},
        }
    ],
}

# Claude Code 2.1.330, --print --output-format json, interrupted with SIGINT. The process exited 0.
CLAUDE_INTERRUPTED = {
    'type': 'result',
    'subtype': 'error_during_execution',
    'is_error': True,
    'result': None,
    'session_id': 'c2b74254-6c8f-4745-a2b2-f30f4822b6d3',
    'errors': ['[ede_diagnostic] result_type=user last_content_type=n/a stop_reason=tool_use'],
    'stop_reason': 'tool_use',
    'terminal_reason': 'aborted_streaming',
    'permission_denials': [],
}


class NativeResultTests(unittest.TestCase):
    def test_codex_failed_turn_reports_its_session_and_one_error(self):
        result = codex.outcome(jsonl(CODEX_SIGNED_OUT))
        self.assertEqual(result['session'], '01a204d2-9f33-7941-b932-2b13ecac3758')
        self.assertIsNone(result['response'])
        self.assertIsNone(result['denials'])
        self.assertEqual(result['limitations'], ['The native turn failed: unexpected status 401 Unauthorized.'])

    def test_codex_output_cut_off_mid_turn_has_no_response(self):
        result = codex.outcome(jsonl(CODEX_SIGNED_OUT[:5]))
        self.assertIsNone(result['response'])
        self.assertEqual(
            result['limitations'],
            [
                'The native output does not show the turn finishing.',
                'Its last reported error: Reconnecting... 5/5 (unexpected status 401 Unauthorized)',
            ],
        )

    def test_codex_completed_turn_reports_only_its_final_message(self):
        result = codex.outcome(jsonl(CODEX_COMPLETED))
        self.assertEqual(result['response'], 'A conversion module.')
        self.assertEqual(result['limitations'], [])
        self.assertIsNone(codex.outcome(jsonl(CODEX_COMPLETED[:-1]))['response'])

    def test_claude_result_reports_denials_with_its_response(self):
        result = claude.outcome(json.dumps(CLAUDE_DENIED).encode())
        self.assertEqual(result['session'], 'c2b74254-6c8f-4745-a2b2-f30f4822b6d3')
        self.assertEqual(result['response'], CLAUDE_DENIED['result'])
        self.assertEqual(
            result['denials'], [{'tool': 'Edit', 'input': {'file_path': '/home/berman-dev/trials/project/convert.py'}}]
        )
        self.assertEqual(result['limitations'], [])

    def test_claude_interrupted_result_has_no_response(self):
        result = claude.outcome(json.dumps(CLAUDE_INTERRUPTED).encode())
        self.assertIsNone(result['response'])
        self.assertEqual(result['denials'], [])
        self.assertEqual(
            result['limitations'],
            [
                'The native result reports an error: error_during_execution; aborted_streaming; '
                '[ede_diagnostic] result_type=user last_content_type=n/a stop_reason=tool_use'
            ],
        )


if __name__ == '__main__':
    unittest.main()
