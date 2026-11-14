"""A published edition is self-contained; reader coverage is explicit and source-bound."""

import csv
import io
import json
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from berman.archive import builder, package, streams
from berman.schemas import validate
from tests.compatibility import test_build_archive as fixtures


class ReadingEditionsTests(unittest.TestCase):
    setUp = fixtures.ArchiveTests.setUp
    inventory = fixtures.ArchiveTests.inventory

    def publish(self):
        build = builder.select(self.root)
        report = builder.edition(build)
        return build, report, self.root / report['archive_path']

    def tree(self):
        return {p.relative_to(self.root).as_posix(): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}

    def rewrite_zip(self, path, mutate):
        with zipfile.ZipFile(path) as archive:
            payload = {info.filename: (archive.read(info), info.external_attr >> 16) for info in archive.infolist()}
        mutate(payload)
        return package.make_zip(payload, '')

    def test_verified_publication_survives_source_removal_and_new_builder(self):
        build, report, path = self.publish()
        shutil.rmtree(self.root / 'runs')
        (self.root / 'source-layout.json').unlink()
        (self.root / 'README.md').write_text('A later, unpublished analysis document.\n')
        before = self.tree()
        with patch.object(builder, 'render', side_effect=AssertionError('verification rerendered')):
            self.assertEqual(builder.edition(build, verify=True)['archive_sha256'], report['archive_sha256'])
        result = subprocess.run(
            [
                sys.executable,
                '-I',
                '-B',
                '-m',
                'berman.archive.builder',
                '--root',
                str(self.root),
                '--edition',
                'stewart',
                '--verify',
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.tree(), before)
        self.assertEqual(package.verify_edition(path)['freshness'], 'not-checked')

    def test_freshness_is_read_only_and_new_publication_keeps_previous_bytes(self):
        build, first, path = self.publish()
        original = path.read_bytes()
        self.assertEqual(builder.edition(builder.select(self.root), freshness=True)['freshness'], 'current')
        (self.root / 'README.md').write_text('A later selected interpretation.\n')
        before = self.tree()
        check = builder.edition(builder.select(self.root), freshness=True)
        self.assertEqual(check['freshness'], 'stale')
        self.assertEqual(self.tree(), before)
        self.assertEqual(builder.edition(build, verify=True)['archive_sha256'], first['archive_sha256'])
        _, second, second_path = self.publish()
        self.assertNotEqual(second_path, path)
        self.assertEqual(path.read_bytes(), original)
        self.assertTrue(second_path.is_file())
        self.assertEqual(
            package.verify_published(self.root, build.selection)['archive_sha256'], second['archive_sha256']
        )

    def test_standalone_copy_needs_no_workspace_and_mtime_does_not_change_identity(self):
        _, report, path = self.publish()
        with tempfile.TemporaryDirectory() as folder:
            copy_path = Path(folder) / 'shared-edition.zip'
            copy_path.write_bytes(path.read_bytes())
            shutil.rmtree(self.root)
            result = subprocess.run(
                [sys.executable, '-I', '-B', '-m', 'berman.archive.builder', '--path', str(copy_path)],
                capture_output=True,
                text=True,
                cwd=folder,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['archive_sha256'], report['archive_sha256'])

    def test_corrupt_members_modes_membership_and_input_assertions_are_rejected(self):
        _, _, path = self.publish()
        prefix = 'stewart-analysis/'
        name = prefix + 'runs/run-005/INSTRUCTIONS.md'

        def changed_content(payload):
            payload[name] = (b'changed', payload[name][1])

        def changed_mode(payload):
            payload[name] = (payload[name][0], stat.S_IFREG | 0o600)

        def missing(payload):
            del payload[name]

        def extra(payload):
            payload[prefix + 'extra.txt'] = (b'extra', stat.S_IFREG | 0o644)

        def contradictory_inputs(payload):
            manifest_name = prefix + package.MANIFEST
            manifest = json.loads(payload[manifest_name][0])
            manifest['inputs']['originals'][0]['sha256'] = '0' * 64
            payload[manifest_name] = (package.json_bytes(manifest), stat.S_IFREG | 0o644)

        for mutation in (changed_content, changed_mode, missing, extra, contradictory_inputs):
            with self.subTest(mutation=mutation.__name__):
                with self.assertRaises(ValueError):
                    package.inspect_edition(self.rewrite_zip(path, mutation))

    def test_changed_zip_under_its_published_name_is_not_reinterpreted(self):
        build, _, path = self.publish()
        data = path.read_bytes()
        path.write_bytes(data + b'changed appended bytes')
        with self.assertRaisesRegex(ValueError, 'published identity'):
            package.verify_published(self.root, build.selection)
        with self.assertRaisesRegex(ValueError, 'published identity'):
            package.verify_edition(path)

    def test_interrupted_publication_leaves_no_partial_immutable_name(self):
        build = builder.select(self.root)
        with patch.object(package.os, 'link', side_effect=OSError('interrupted before publication')):
            with self.assertRaisesRegex(OSError, 'interrupted'):
                builder.edition(build)
        self.assertEqual(list((self.root / 'archive/editions').glob('*.zip')), [])
        self.assertFalse((self.root / package.pointer_path(build.selection)).exists())
        self.assertEqual(list((self.root / 'archive/editions').glob('.edition-*')), [])
        self.assertEqual(builder.edition(builder.select(self.root))['mode'], 'published and verified')

    def test_interruption_after_zip_before_pointer_can_retry_same_publication(self):
        build = builder.select(self.root)
        with patch.object(package.os, 'replace', side_effect=OSError('pointer interruption')):
            with self.assertRaisesRegex(OSError, 'pointer interruption'):
                builder.edition(build)
        paths = list((self.root / 'archive/editions').glob('*.zip'))
        self.assertEqual(len(paths), 1)
        data = paths[0].read_bytes()
        result = builder.edition(builder.select(self.root))
        self.assertEqual(paths[0].read_bytes(), data)
        self.assertEqual(result['archive_sha256'], paths[0].stem)

    def test_a_foreign_collection_pointer_is_refused(self):
        build, _, _ = self.publish()
        (self.root / 'collection.json').write_text(json.dumps({'id': '00000000-0000-4000-8000-000000000002'}))
        with self.assertRaisesRegex(ValueError, 'another collection'):
            package.verify_published(self.root, build.selection)

    def test_manifest_pins_distinct_occurrences_with_identical_content(self):
        duplicate = self.run / 'another-account.md'
        duplicate.write_bytes((self.run / 'narrative.md').read_bytes())
        self.layout['runs'][0]['entries'] = self.inventory()
        (self.root / 'source-layout.json').write_text(json.dumps(self.layout))
        _, _, path = self.publish()
        manifest, _ = package.inspect_edition(path.read_bytes())
        validate('berman.reading-edition/2', manifest)
        rows = {row['path']: row for row in manifest['inputs']['originals']}
        one, two = (rows['runs/run-005/' + name] for name in ('narrative.md', 'another-account.md'))
        self.assertEqual(one['sha256'], two['sha256'])
        self.assertNotEqual(one['path'], two['path'])

    def test_explicit_claude_format_overrides_stewart_reader_and_preserves_roles(self):
        folder = self.run / 'session/snapshots/published'
        folder.mkdir(parents=True)
        source = folder / 'transcript.jsonl'
        source.write_text(
            json.dumps(
                {
                    'type': 'user',
                    'message': {
                        'role': 'user',
                        'content': [{'type': 'tool_result', 'tool_use_id': 'tool-1', 'content': 'Harness result'}],
                    },
                }
            )
            + '\n'
            + json.dumps({'type': 'assistant', 'message': {'role': 'assistant', 'content': 'Visible reply'}})
            + '\n'
        )
        self.layout['runs'][0]['entries'] = self.inventory()
        (self.root / 'source-layout.json').write_text(json.dumps(self.layout))
        name = source.relative_to(self.root).as_posix()
        authority = self.root / 'recordings/run-005/manifest.json'
        (authority.parent / 'evidence').mkdir(parents=True)
        operation = package.json_bytes({'run': 'run-005', 'connection': {'id': 'stewart'}, 'harness': {'id': 'claude'}})
        (authority.parent / 'evidence/operation.json').write_bytes(operation)
        manifest = {
            'schema': 'berman.recording/2',
            'collection_id': '00000000-0000-4000-8000-000000000001',
            'run': 'run-005',
            'profile': 'historical-import',
            'entries': [dict(entry, path='runs/run-005/' + entry['path']) for entry in self.inventory()]
            + [
                {
                    'path': 'evidence/operation.json',
                    'type': 'file',
                    'mode': '0o644',
                    'size': len(operation),
                    'sha256': package.SHA(operation),
                }
            ],
            'evidence': {'ending': None, 'capture': None, 'operation': 'evidence/operation.json', 'migration': None},
            'coverage': {'unknown': ['Historical ending, capture, migration, binding and closure not established']},
            'attribution': {
                'connection': 'stewart',
                'basis': 'retained operation',
                'evidence': 'evidence/operation.json',
            },
            'custody': {'inventory': self.layout['runs'][0]},
            'streams': [{'path': name, 'format': 'claude-jsonl/1'}],
        }
        authority.write_text(json.dumps(manifest))
        build = builder.select(self.root)
        self.assertTrue(build.stewart_reader('run-005'))
        generated, _ = builder.render(build)
        blocks = [json.loads(line) for line in generated['review/decoded-blocks.jsonl'].splitlines()]
        self.assertEqual([row['native_role'] for row in blocks], ['user', 'assistant'])
        self.assertEqual([row['pointer'] for row in blocks], ['/message/content/0/content', '/message/content'])
        self.assertEqual({row['source_sha256'] for row in blocks}, {package.SHA(source.read_bytes())})
        rows = list(csv.DictReader(io.StringIO(generated['review/transcript-map.csv'].decode())))
        self.assertEqual({row['format_basis'] for row in rows}, {'recording manifest'})
        self.assertEqual({row['decode_status'] for row in rows}, {'decoded'})
        report = builder.edition(build)
        self.assertEqual(
            package.verify_edition(self.root / report['archive_path'])['collection_id'], manifest['collection_id']
        )


class NativeStreamCoverageTests(unittest.TestCase):
    def decode(self, events, format_name):
        raw = b''.join((json.dumps(event) + '\n').encode() for event in events)
        source = {
            'collection_id': 'fixture',
            'run': 'run-001',
            'source_path': 'runs/run-001/session/transcript.jsonl',
            'source_sha256': package.SHA(raw),
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
