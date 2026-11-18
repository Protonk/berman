"""Native stream decoding and citation coverage."""

import json
import unittest
from berman import files
from berman.archive import streams


class NativeStreamCoverageTests(unittest.TestCase):
    def decode(self, events, format_name):
        raw = b''.join((json.dumps(event) + '\n').encode() for event in events)
        source = {
            'collection_id': 'fixture',
            'run': 'run-001',
            'source_path': 'runs/run-001/session/transcript.jsonl',
            'source_sha256': files.digest(raw),
        }
        return streams.decode(raw, format_name, source)

    def test_unknown_format_does_not_guess_from_native_fields(self):
        event = {'type': 'assistant', 'message': {'role': 'assistant', 'content': 'Visible text'}}
        records, blocks = self.decode([event], 'unknown')
        self.assertEqual(blocks, [])
        self.assertEqual((records[0]['raw_availability'], records[0]['decode_status']), ('included', 'unsupported'))

    def test_partial_unknown_and_malformed_shapes_keep_coverage_explicit(self):
        events = [
            {
                'type': 'assistant',
                'message': {
                    'role': 'assistant',
                    'content': [{'type': 'text', 'text': 'Visible'}, {'type': 'future-block', 'future': 'Unknown'}],
                },
            },
            {'type': 'assistant', 'message': {'role': 'assistant', 'content': [{'type': ['invalid']}]}},
            {
                'type': 'assistant',
                'message': {'role': 'assistant', 'content': [{'type': 'thinking', 'thinking': 'Private'}]},
            },
            [],
        ]
        records, blocks = self.decode(events, 'claude-jsonl/1')
        self.assertEqual(
            [row['decode_status'] for row in records], ['partial', 'unsupported', 'not-selected', 'malformed']
        )
        self.assertEqual([row['content'] for row in blocks], ['Visible'])

    def test_codex_does_not_duplicate_message_events_and_exposes_harness_errors(self):
        records, blocks = self.decode(
            [
                {
                    'type': 'response_item',
                    'payload': {'type': 'message', 'role': 'assistant', 'content': [{'text': 'One event'}]},
                },
                {'type': 'event_msg', 'payload': {'type': 'agent_message', 'message': 'One event'}},
                {'type': 'event_msg', 'payload': {'type': 'task_complete', 'error': {'message': 'Transport error'}}},
                {'type': 'response_item', 'payload': {'type': ['malformed']}},
            ],
            'codex-jsonl/1',
        )
        self.assertEqual(
            [row['decode_status'] for row in records], ['decoded', 'not-selected', 'decoded', 'unsupported']
        )
        self.assertEqual([row['pointer'] for row in blocks], ['/payload/content/0/text', '/payload/error'])
        self.assertEqual(blocks[1]['native_role'], '')
        self.assertEqual(blocks[1]['kind'], 'task_error')


if __name__ == '__main__':
    unittest.main()
