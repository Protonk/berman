"""End-to-end regressions for the fieldworker-support audit's failure cases."""
from tests.support.paths import REPO

import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock

from berman import recovery as fieldwork
from berman._legacy import capture_session as capture
from berman import files
from tests.support.frozen_packets import frozen_packet


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.field = self.base / 'stewart-fieldwork'
        self.analysis = self.base / 'analysis'
        self.home = self.base / 'codex'
        (self.analysis / 'runs').mkdir(parents=True)
        (self.analysis / 'source-layout.json').write_text('{"runs": []}\n')
        self.collector = capture.Collector(self.field, self.home, self.analysis)
        self.run = 'run-900001'
        self.root = self.field / self.run
        self.source = self.home / 'sessions/rollout-test-session.jsonl'
        self.source.parent.mkdir(parents=True)
        self.records = [{'type': 'session_meta', 'payload': {'id': 'test-session'}}]
        self.save()
        self.prepare()

    def prepare(self):
        return frozen_packet(self.collector, self.run, 'Describe these recording locations and stop.',
                                 self.base, 'not-applicable: support trial', ['narrative', 'scratch', 'auditor-streams'])

    def save(self):
        self.source.write_bytes(b''.join((json.dumps(r)+'\n').encode() for r in self.records))

    def event(self, name, **kw):
        return dict({'hook_event_name': name, 'session_id': 'test-session', 'transcript_path': str(self.source),
                     'turn_id': 'turn-one'}, **kw)

    def register(self, **kw):
        return self.collector.hook(self.event('UserPromptSubmit', prompt=capture.MARKER+self.run, **kw))

    def answer(self, text='done'):
        self.records.append({'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant', 'content': [{'text': text}]}})
        self.save()

    def stop(self):
        self.collector.hook(self.event('Stop'))

    def finish(self):
        self.register(); self.answer(); self.stop()
        return self.collector.command('finalize', self.run)

    def test_preparation_is_self_contained_and_never_truncates_an_existing_packet(self):
        self.assertEqual(self.collector.status(self.run)['state'], 'prepared')
        self.assertFalse((self.root/'session').exists())
        self.assertEqual((self.root/'ASSIGNMENT.md').read_text().splitlines()[0], capture.MARKER+self.run)
        self.assertIn(str(self.root/'narrative.md'), (self.root/'INSTRUCTIONS.md').read_text())
        (self.root/'narrative.md').write_text('sentinel')
        with self.assertRaises(ValueError): self.prepare()
        self.assertEqual((self.root/'narrative.md').read_text(), 'sentinel')

    def test_near_marker_on_bound_session_still_marks_a_turn_active(self):
        self.register(); self.answer(); self.stop()
        result = self.collector.hook(self.event('UserPromptSubmit', prompt='Stewart-Fieldwork-Run:run-900001'))
        self.assertIn('systemMessage', result)
        with self.assertRaisesRegex(ValueError, 'prompt was submitted'):
            self.collector.command('finalize', self.run)
        self.assertFalse((self.root/'session/finalized.json').exists())

    def test_status_exposes_a_prompt_without_terminal_callback_after_prior_snapshot(self):
        self.register(); self.answer(); self.stop()
        self.collector.hook(self.event('UserPromptSubmit', prompt='Next turn'))
        status = self.collector.status(self.run)
        self.assertEqual(status['state'], 'snapshot')
        self.assertTrue(status['prompt_without_terminal_callback'])
        self.assertIn('failed without Stop', status['warnings'][0])
        self.stop()
        self.assertFalse(self.collector.status(self.run)['prompt_without_terminal_callback'])

    def test_reader_surfaces_native_task_error_as_harness_and_tolerates_unknown_payloads(self):
        records = [{'type':'response_item','payload':None},
                   {'type':'response_item','payload':{'type':'message','role':'assistant','content':None}},
                   {'type':'event_msg','payload':{'type':'task_complete','error':{'message':'HTTP 400 trial'}}}]
        reader, messages, malformed = capture.render(b'\n'.join(json.dumps(r).encode() for r in records))
        self.assertIn(b'ROLE: harness | KIND: task_error', reader)
        self.assertIn(b'transcript.jsonl#L3 /payload/error', reader)
        self.assertIn(b'HTTP 400 trial', reader)
        self.assertNotIn('HTTP 400 trial', messages)
        self.assertEqual(malformed, [])

    def test_adapting_instructions_and_coverage(self):
        source=self.base/'INSTRUCTIONS.md'
        source.write_text('Narrative: `~/Desktop/Experiments/stewart-fieldwork/run-005/narrative.md`\nScratch: `~/Desktop/Experiments/stewart-fieldwork/run-005/scratch/`\n')
        result=frozen_packet(self.collector,'run-900002','assignment',self.base,'revision',
                                ['narrative','scratch','full-command-outputs'],source)
        text=(Path(result['path'])/'INSTRUCTIONS.md').read_text()
        self.assertNotIn('run-005',text)
        self.assertIn('full stdout/stderr',text)

    def test_archived_identity_and_stale_instruction_destinations_are_refused(self):
        (self.analysis/'runs'/self.run).mkdir()
        with self.assertRaises(ValueError): self.register()
        (self.analysis/'runs'/self.run).rmdir()
        (self.root/'INSTRUCTIONS.md').write_text('Write to /somewhere/run-005/narrative.md')
        with self.assertRaisesRegex(ValueError,'another run'): self.register()
        self.assertFalse((self.root/'session').exists())

    def test_notes_packet_captures_and_migrates_without_old_recording_requests(self):
        source = self.base / 'notes-instructions.md'
        source.write_text('Write two accounts in `/old/run-007/notes/`.\n')
        result = frozen_packet(self.collector, 'run-900002', 'Discuss the plans.', self.base,
                                  'continuing development', ['notes'], source)
        root = Path(result['path'])
        instructions = (root / 'INSTRUCTIONS.md').read_text()
        self.assertIn(str(root / 'notes'), instructions)
        self.assertNotIn('run-007', instructions)
        self.assertNotIn('narrative', instructions)
        self.assertNotIn('scratch', instructions)
        self.assertEqual(json.loads((root / 'run.json').read_text())['coverage'], ['notes'])
        (root / 'notes/first.md').write_text('A participant account.\n')
        self.collector.hook(self.event('UserPromptSubmit', prompt=capture.MARKER+'run-900002'))
        self.answer(); self.stop()
        self.assertTrue(self.collector.status('run-900002')['snapshot_consistent'])
        self.collector.command('finalize', 'run-900002')
        before = files.inventory(root)
        migrated = Path(fieldwork.migrate(self.collector, 'run-900002')['path'])
        self.assertEqual(files.inventory(migrated), before)
        self.assertEqual((migrated / 'notes/first.md').read_text(), 'A participant account.\n')

    def test_notes_coverage_requires_its_own_instructions_and_rejects_stale_paths(self):
        with self.assertRaisesRegex(ValueError, 'explicit researcher-authored'):
            frozen_packet(self.collector, 'run-900002', 'assignment', self.base, 'revision', ['notes'])
        source = self.base / 'notes-instructions.md'
        source.write_text('Write in `/old/run-007/notes/`.\n')
        frozen_packet(self.collector, 'run-900002', 'assignment', self.base, 'revision', ['notes'], source)
        root = self.field / 'run-900002'
        (root / 'INSTRUCTIONS.md').write_text('Write in `/old/run-007/notes/`.\n')
        with self.assertRaisesRegex(ValueError, 'another run'):
            self.collector.hook(self.event('UserPromptSubmit', prompt=capture.MARKER+'run-900002'))
        self.assertFalse((root / 'session').exists())
        with self.assertRaisesRegex(ValueError, 'Coverage must'):
            frozen_packet(self.collector, 'run-900003', 'assignment', self.base, 'revision',
                              ['notes', 'narrative', 'scratch'], source)

    def test_near_markers_warn_without_binding_and_missing_status_names_both_roots(self):
        for prompt in ('Stewart-Fieldwork-Run:run-900001', '  Stewart-Fieldwork-Run: run-900001',
                       '**Stewart-Fieldwork-Run: run-900001**', 'stewart-fieldwork-run: run-900001',
                       '```\nStewart-Fieldwork-Run: run-900001\n```', 'Hello\nStewart-Fieldwork-Run: run-900001'):
            result=self.collector.hook(self.event('UserPromptSubmit',prompt=prompt))
            self.assertIn('systemMessage',result,prompt)
        self.assertFalse((self.root/'session').exists())
        status=self.collector.status('run-999999')
        self.assertEqual(status['state'],'missing')
        self.assertEqual(len(status['searched']),2)

    def test_late_source_path_and_archived_rollout_are_recoverable(self):
        self.register(transcript_path=None)
        self.assertEqual(self.collector.status(self.run)['state'],'registered-no-snapshot')
        self.answer(); self.stop()
        self.assertEqual(json.loads((self.root/'session/binding.json').read_text())['transcript_path'],str(self.source))
        archived=self.home/'archived_sessions'/self.source.name
        archived.parent.mkdir(); self.source.rename(archived)
        result=self.collector.command('refresh',self.run)
        self.assertEqual(result['source_transcript'],str(archived))
        self.assertEqual(self.collector.status(self.run)['state'],'snapshot')
        first = archived.with_name('first-test-session.jsonl')
        archived.rename(first)
        second = archived.with_name('second-test-session.jsonl')
        second.write_bytes(first.read_bytes())
        with self.assertRaisesRegex(ValueError, 'Several transcripts'):
            self.collector.command('refresh', self.run)
        self.assertEqual(self.collector.command('refresh', self.run, transcript_path=str(first))['source_transcript'], str(first))

    def test_snapshot_inconsistency_is_visible_and_refresh_repairs_it(self):
        self.register(); self.answer(); self.stop()
        (self.root/'session/conversation.txt').write_text('interrupted write')
        self.assertEqual(self.collector.status(self.run)['state'],'snapshot-inconsistent')
        self.collector.command('refresh',self.run)
        self.assertEqual(self.collector.status(self.run)['state'],'snapshot')
        (self.root/'session/captures.jsonl').write_text('')
        self.assertFalse(self.collector.status(self.run)['snapshot_consistent'])

    def test_finalize_requires_boundary_and_post_finalize_warning_is_once(self):
        self.register(); self.answer('mid-turn')
        with self.assertRaisesRegex(ValueError,'Not finalized'): self.collector.command('finalize',self.run)
        self.assertFalse((self.root/'session/finalized.json').exists())
        final=self.collector.command('finalize',self.run,reason='Intentional interrupted encounter')
        self.assertEqual(final['boundary']['override_reason'],'Intentional interrupted encounter')
        frozen=(self.root/'session/transcript.jsonl').read_bytes()
        self.answer('later')
        self.assertIn('systemMessage',self.collector.hook(self.event('Stop')))
        self.assertEqual(self.collector.hook(self.event('Stop')), {})
        self.assertEqual((self.root/'session/transcript.jsonl').read_bytes(),frozen)
        supplement=self.collector.command('supplement',self.run,reason='Recover later activity')
        self.assertIn(b'later',(Path(supplement['path'])/'session/transcript.jsonl').read_bytes())
        self.assertEqual((self.root/'session/transcript.jsonl').read_bytes(),frozen)

    def test_only_task_complete_trailer_can_follow_terminal_snapshot_without_override(self):
        self.register(); self.answer(); self.stop()
        self.records.append({'type':'event_msg','payload':{'type':'task_complete'}}); self.save()
        final=self.collector.command('finalize',self.run)
        self.assertFalse(final['boundary']['warnings'])

    def test_new_prompt_after_stop_prevents_premature_finalization(self):
        self.register(); self.answer(); self.stop()
        self.collector.hook(self.event('UserPromptSubmit',prompt='Now do more work'))
        with self.assertRaisesRegex(ValueError,'prompt was submitted'): self.collector.command('finalize',self.run)

    def test_replacement_preserves_capture_and_retires_old_session(self):
        self.register(); self.answer(); self.stop()
        old=(self.root/'session/transcript.jsonl').read_bytes()
        (self.root/'narrative.md').write_text('old work stays')
        replacement=self.collector.command('replace',self.run,reason='Correct assignment',relationship='new-encounter')
        self.assertEqual((Path(replacement['preserved_at'])/'session/transcript.jsonl').read_bytes(),old)
        self.assertEqual((self.root/'narrative.md').read_text(),'old work stays')
        self.assertEqual(self.collector.hook(self.event('Stop')), {})
        with self.assertRaisesRegex(ValueError,'replaced'): self.register()
        fresh=self.event('UserPromptSubmit'); fresh['session_id']='new-session'; fresh['prompt']=capture.MARKER+self.run
        self.collector.hook(fresh)
        self.assertEqual(self.collector.status(self.run)['binding']['session_id'],'new-session')

    def test_manual_adoption_records_reason_and_restores_registry(self):
        self.answer()
        record=self.collector.command('adopt',self.run,session_id='test-session',transcript_path=str(self.source),reason='Hook was unavailable')
        self.assertEqual(record['event'],'manual-adopt')
        self.assertTrue(self.collector.registry('test-session').exists())
        self.assertIn('adoption',self.collector.status(self.run)['binding'])

    def test_migration_preserves_nested_git_modes_symlinks_and_mtimes(self):
        git=self.root/'scratch/repo/.git'; (git/'empty').mkdir(parents=True)
        (git/'object').write_bytes(b'\x00immutable\xff')
        (self.root/'scratch/pointer').mkdir()
        (self.root/'scratch/pointer/.git').write_text('gitdir: /historical/target\n')
        (self.root/'scratch/link').symlink_to('/historical/missing')
        (git/'empty').chmod(0o700)
        self.finish()
        before=files.inventory(self.root)
        result=fieldwork.migrate(self.collector,self.run)
        destination=Path(result['path'])
        self.assertEqual(files.inventory(destination),before)
        self.assertFalse(self.root.exists())
        self.assertEqual(self.collector.status(self.run)['location'],'archived')
        layout=json.loads((self.analysis/'source-layout.json').read_text())
        self.assertEqual(len(layout['runs']),1)
        self.assertTrue(layout['runs'][0]['git_pointers'])
        self.assertTrue(layout['runs'][0]['git_directories'])
        self.assertEqual(fieldwork.migrate(self.collector,self.run)['state'],'migrated')

    def test_migration_destination_collision_never_nests_or_overwrites(self):
        self.finish()
        destination=self.analysis/'runs'/self.run; destination.mkdir()
        (destination/'sentinel').write_text('keep')
        with self.assertRaisesRegex(ValueError,'already exists'): fieldwork.migrate(self.collector,self.run)
        self.assertTrue(self.root.exists())
        self.assertEqual((destination/'sentinel').read_text(),'keep')

    def test_migration_recovers_after_move_before_index_update(self):
        self.finish()
        atomic=files.atomic
        def fail_layout(path,data):
            if path.name=='source-layout.json': raise OSError('simulated interruption')
            return atomic(path,data)
        with mock.patch.object(files,'atomic',side_effect=fail_layout):
            with self.assertRaises(OSError): fieldwork.migrate(self.collector,self.run)
        self.assertFalse(self.root.exists())
        self.assertTrue((self.analysis/'runs'/self.run).exists())
        self.assertEqual(json.loads((self.analysis/'source-layout.json').read_text())['runs'],[])
        fieldwork.migrate(self.collector,self.run)
        self.assertEqual(len(json.loads((self.analysis/'source-layout.json').read_text())['runs']),1)

    def test_migration_refuses_cross_device_without_copy(self):
        self.finish()
        original=Path.stat
        def altered(path,*args,**kwargs):
            value=original(path,*args,**kwargs)
            if path==self.analysis/'runs':
                fields=list(value); fields[2]+=1
                return os.stat_result(fields)
            return value
        with mock.patch.object(Path,'stat',altered):
            with self.assertRaisesRegex(ValueError,'Cross-filesystem'): fieldwork.migrate(self.collector,self.run)
        self.assertTrue(self.root.exists())
        self.assertFalse((self.analysis/'runs'/self.run).exists())


class ProductPreparationTests(unittest.TestCase):
    """The package itself prepares only the notes layout that the current collectors register."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        (self.base / 'analysis/runs').mkdir(parents=True)
        self.collector = capture.Collector(self.base / 'packets', self.base / 'codex', self.base / 'analysis')
        self.source = self.base / 'instructions.md'
        self.source.write_text('Write accounts in `/elsewhere/run-007/notes/`.\n')

    def prepare(self, run='run-900002', coverage=('notes',), **kwargs):
        options = dict(marker='Berman-Fieldwork-Run: ', connection='orchard')
        options.update(kwargs)
        return fieldwork.prepare(self.collector, run, 'Discuss the plans.', self.base, 'fixture revision',
                                 list(coverage), self.source, **options)

    def test_a_notes_packet_names_its_marker_connection_and_destination(self):
        root = Path(self.prepare()['path'])
        self.assertEqual(sorted(p.name for p in root.iterdir()), ['ASSIGNMENT.md', 'INSTRUCTIONS.md', 'notes', 'run.json'])
        self.assertEqual((root / 'ASSIGNMENT.md').read_text().splitlines()[0], 'Berman-Fieldwork-Run: run-900002')
        self.assertIn(str(root / 'notes'), (root / 'INSTRUCTIONS.md').read_text())
        meta = json.loads((root / 'run.json').read_text())
        self.assertEqual((meta['schema'], meta['connection'], meta['capture_marker'], meta['coverage'], meta['recording_protocol']),
                         ('berman.run-preparation/1', 'orchard', 'Berman-Fieldwork-Run:', ['notes'], 'work-accounts/1'))
        self.assertEqual(meta['collector_layout'], {'notes/': 'Requested participant accounts and selected supporting copies.'})
        with self.assertRaisesRegex(ValueError, 'never overwrites'):
            self.prepare()

    def test_the_removed_layout_and_incomplete_requests_are_refused(self):
        for coverage in (('narrative', 'scratch'), ('notes', 'narrative', 'scratch')):
            with self.assertRaisesRegex(ValueError, 'narrative and scratch layout was removed'):
                self.prepare(coverage=coverage)
        with self.assertRaisesRegex(ValueError, 'narrative and scratch layout was removed'):
            self.prepare(layout='legacy')
        for missing in ('marker', 'connection'):
            with self.assertRaisesRegex(ValueError, 'collector marker and a connection identity'):
                self.prepare(**{missing: None})
        self.source.write_text('Keep a narrative in `/elsewhere/run-007/narrative.md` and notes in `/elsewhere/run-007/notes/`.\n')
        with self.assertRaisesRegex(ValueError, 'old narrative or scratch destinations'):
            self.prepare()
        self.assertFalse((self.base / 'packets/run-900002').exists())


if __name__=='__main__':
    unittest.main()
