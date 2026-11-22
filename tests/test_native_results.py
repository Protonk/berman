"""Read native output recorded from the actual CLIs, reduced to the lines Berman reads.

Each sample names the CLI version that produced it. Add a sample when a harness changes the
output an unattended sitting reports.
"""

import json
import unittest

from berman import codex

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


class NativeResultTests(unittest.TestCase):
    def test_codex_failed_turn_reports_its_session_and_one_error(self):
        output = ''.join(json.dumps(event) + '\n' for event in CODEX_SIGNED_OUT).encode()
        result = codex.outcome(output)
        self.assertEqual(result['session'], '01a204d2-9f33-7941-b932-2b13ecac3758')
        self.assertIsNone(result['response'])
        self.assertEqual(result['denials'], [])
        self.assertEqual(result['limitations'], ['The native turn failed: unexpected status 401 Unauthorized.'])

    def test_codex_output_cut_off_mid_turn_has_no_response(self):
        cut = CODEX_SIGNED_OUT[:5]
        result = codex.outcome(''.join(json.dumps(event) + '\n' for event in cut).encode())
        self.assertIsNone(result['response'])
        self.assertEqual(
            result['limitations'],
            [
                'The native output does not show the turn finishing.',
                'Its last reported error: Reconnecting... 5/5 (unexpected status 401 Unauthorized)',
            ],
        )


if __name__ == '__main__':
    unittest.main()
