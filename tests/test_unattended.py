"""Unattended sittings: a producer corresponds with a scripted actor through Berman's ordinary commands.

These checks establish Berman's handling of the scripted native output. They are not evidence of
how the real Claude Code or Codex CLIs behave.
"""

import json
import os
from pathlib import Path
import signal
import subprocess
import time
import unittest

from tests import test_episodes

ACTORS = ('claude', 'codex')


class UnattendedTests(test_episodes.EpisodeCase):
    def send(self, request, prompt, **expect):
        command = ('start',) if request is None else ('resume', '--request', request)
        return self.cli(command[0], self.folder, *command[1:], '--unattended', '--prompt', prompt, **expect)

    def git(self, *args):
        subprocess.run(
            ['git', '-c', 'user.name=Someone', '-c', 'user.email=someone@example.invalid', '-C', self.project, *args],
            check=True,
            capture_output=True,
        )

    def background(self, *args):
        """Run Berman in its own process group so a test can signal it alone or with the actor."""
        command = [*test_episodes.BERMAN, *map(str, args)]
        process = subprocess.Popen(
            command, cwd=self.base, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True
        )
        self.addCleanup(process.__exit__, None, None, None)
        sitting = self.folder / 'sittings' / '000001.json'
        self.wait_for(lambda: sitting.exists() and 'pid' in json.loads(sitting.read_text()) and self.launches())
        time.sleep(0.3)
        return process

    def wait_for(self, condition, seconds=20):
        deadline = time.monotonic() + seconds
        while not condition():
            self.assertLess(time.monotonic(), deadline, 'condition not reached')
            time.sleep(0.05)

    def test_conversation_without_an_additional_job(self):
        for actor in ACTORS:
            with self.subTest(actor=actor):
                self.folder = self.base / 'episodes' / f'{actor}-plain'
                state = self.opening(actor, jobs=())
                self.assertEqual(state['jobs'], [])
                self.assertIn('no additional job', state['opening'])
                self.assertNotIn('Continue', state['opening'])
                messages = ['What is in this checkout?', 'Which test is slowest?', 'Leave it for now.']
                sittings = [self.send(None, messages[0])]
                sittings += [self.send(f'follow-up-{n}', m) for n, m in enumerate(messages[1:], 2)]
                for number, (sitting, message) in enumerate(zip(sittings, messages, strict=True), 1):
                    self.assertFalse(sitting['attended'])
                    self.assertEqual(sitting['exit'], 0)
                    self.assertEqual(sitting['response'], f'done {number}: {message}')
                    self.assertEqual(sitting['denials'], [] if actor == 'claude' else None)
                    self.assertEqual(sitting['limitations'], [])
                    self.assertEqual(sitting['native_session'], sittings[0]['native_session'])
                status = self.cli('status', self.folder)
                self.assertIsNone(status['ending'])
                self.assertEqual(status['sittings'], sittings)
                self.assertEqual(list((self.folder / 'return').iterdir()), [])
                receipt = self.cli('close', self.folder, '--reason', 'I know enough to decide.')
                self.assertEqual(receipt['coverage']['limitations'], [])
                record = self.folder / 'record'
                self.assert_documented(record)
                self.assertFalse((record / 'return').exists())
                self.assertIn('Message from the producer', (record / 'telemetry/parent/transcript.jsonl').read_text())
                conversation = (record / 'telemetry/parent/conversation.txt').read_text()
                for number, message in enumerate(messages, 1):
                    self.assertIn(f'done {number}: {message}', conversation)
                self.assertTrue((record / 'sittings/000003.stdout').is_file())
                self.assertEqual(json.loads((record / 'sittings/000003.json').read_text()), sittings[2])

    def test_additional_jobs_stay_apart_from_responses(self):
        templates = self.base / 'jobs'
        self.cli('template', templates)
        for actor in ACTORS:
            with self.subTest(actor=actor):
                self.folder = self.base / 'episodes' / f'{actor}-jobs'
                state = self.opening(actor, jobs=(self.job, templates / 'handover.md'))
                self.assertIn('these additional jobs', state['opening'])
                self.assertIn('HANDOVER.md', state['opening'])
                first = self.send(None, 'Pick up where the last session stopped.')
                second = self.send('check', 'Did the reader change?')
                returned = (self.folder / 'return/decision.md').read_text()
                for sitting in (first, second):
                    self.assertTrue(sitting['response'].startswith('done'))
                    self.assertNotIn(returned.strip(), sitting['response'])
                self.cli('close', self.folder, '--reason', 'The handover question is settled.')
                record = self.folder / 'record'
                self.assertEqual((record / 'return/decision.md').read_text(), returned)
                self.assertEqual(sorted(p.name for p in (record / 'return').iterdir()), ['decision.md'])
                self.assertEqual(
                    json.loads((record / 'sittings/000002.json').read_text())['response'], second['response']
                )
                self.cli('verify', record)

    def test_attendance_belongs_to_each_sitting_of_one_session(self):
        for actor in ACTORS:
            with self.subTest(actor=actor):
                self.folder = self.base / 'episodes' / f'{actor}-mixed'
                self.opening(actor)
                attended = self.cli('start', self.folder, '--prompt', 'Look at the failing build first.')
                self.assertTrue(attended['attended'])
                self.assertNotIn('response', attended)
                bound = json.loads((self.folder / 'session-id.json').read_text())
                second = self.send('away', 'Carry on while I am out.')
                third = self.cli('resume', self.folder, '--request', 'back')
                fourth = self.send('later', 'Summarize where things stand.')
                self.assertEqual(second['response'], 'done 2: Carry on while I am out.')
                self.assertEqual(fourth['response'], 'done 4: Summarize where things stand.')
                self.assertEqual({second['native_session'], fourth['native_session']}, {bound})
                status = self.cli('status', self.folder)
                self.assertEqual([s['attended'] for s in status['sittings']], [True, False, True, False])
                self.assertIsNone(status['ending'])
                self.assertTrue(third['exit'] == 0 and status['sittings'][2] == third)
                self.cli('close', self.folder, '--reason', 'Back at the desk.')
                self.cli('verify', self.folder / 'record')

    def test_repeated_requests_return_the_recorded_sitting(self):
        self.opening('codex', jobs=())
        first = self.send(None, 'Begin with the tests.')
        self.assertEqual(self.send(None, 'Begin with the tests.'), first)
        self.send(None, 'Something else.', error='different instructions')
        self.cli('start', self.folder, '--prompt', 'Begin with the tests.', error='different instructions')
        second = self.send('next', 'Then the docs.')
        self.assertEqual(self.send('next', 'Then the docs.'), second)
        self.cli('resume', self.folder, '--request', 'next', '--prompt', 'Then the docs.', error='different')
        self.assertEqual(len(self.launches()), 2)
        self.cli('start', self.folder, '--unattended', error='--prompt')
        self.send('blank', ' ', error='nonempty prompt')
        self.assertEqual(len(self.cli('status', self.folder)['sittings']), 2)

    def test_unsuccessful_and_silent_sittings_report_no_response(self):
        for actor in ACTORS:
            with self.subTest(actor=actor):
                self.folder = self.base / 'episodes' / f'{actor}-failing'
                self.opening(actor, jobs=())
                self.env['FIXTURE_FAIL'] = '1'
                failed = self.send(None, 'Try the migration.', code=1)
                del self.env['FIXTURE_FAIL']
                self.assertIsNone(failed['response'])
                self.assertEqual(failed['exit'], 1)
                self.assertEqual(len(failed['limitations']), 2)
                self.assertRegex(failed['limitations'][0], 'reports an error|turn failed')
                self.assertIn('the turn could not finish', failed['limitations'][1])
                self.env['FIXTURE_SILENT'] = '1'
                silent = self.send('silent', 'Anything?')
                del self.env['FIXTURE_SILENT']
                self.assertEqual((silent['exit'], silent['response']), (0, None))
                self.assertRegex(silent['limitations'][0], 'holds no (result|events)')
                self.env['FIXTURE_DENY'] = '1'
                denied = self.send('denied', 'Write the note.')
                del self.env['FIXTURE_DENY']
                self.assertEqual(denied['response'], 'done 3: Write the note.')
                # Codex reports refusals to the actor only, so Berman has none to list.
                self.assertEqual(
                    len(denied['denials']) if actor == 'claude' else denied['denials'], 1 if actor == 'claude' else None
                )
                self.assertIsNone(silent['denials'])
                after = self.send('after', 'Try again more simply.')
                self.assertEqual(after['response'], 'done 4: Try again more simply.')
                self.assertIsNone(self.cli('status', self.folder)['ending'])
                receipt = self.cli('close', self.folder, '--reason', 'The migration waits for review.')
                limitations = receipt['coverage']['limitations']
                self.assertTrue(any(item.startswith('sittings/000001.json: ') for item in limitations))
                self.assertTrue(any(item.startswith('sittings/000002.json: ') for item in limitations))

    def test_interrupt_reaches_the_actor_and_leaves_the_episode_open(self):
        self.opening('claude', jobs=())
        self.env['FIXTURE_SLEEP'] = '30'
        process = self.background('start', self.folder, '--unattended', '--prompt', 'Run the long check.')
        os.killpg(process.pid, signal.SIGINT)
        output, _ = process.communicate(timeout=20)
        self.assertEqual(process.returncode, 130)
        sitting = json.loads(output)
        self.assertEqual((sitting['exit'], sitting['response']), (-signal.SIGINT, None))
        self.assertIn('ended on signal 2', sitting['limitations'][-1])
        del self.env['FIXTURE_SLEEP']
        self.assertEqual(self.send('shorter', 'Run the quick check.')['response'], 'done 2: Run the quick check.')

    def test_lost_launcher_neither_relaunches_nor_invents_an_exit(self):
        self.opening('codex', jobs=())
        self.env['FIXTURE_SLEEP'] = '2'
        process = self.background('start', self.folder, '--unattended', '--prompt', 'Survey the modules.')
        process.kill()
        process.wait()
        self.send(None, 'Survey the modules.', error='in progress')
        retried = []
        self.wait_for(lambda: self.retry(retried))
        sitting = retried[0]
        self.assertIsNone(sitting['exit'])
        self.assertEqual(sitting['response'], 'done 1: Survey the modules.')
        self.assertEqual(sitting['limitations'], ['The launcher did not observe the process exit.'])
        self.assertEqual(self.send(None, 'Survey the modules.'), sitting)
        self.assertEqual(len(self.launches()), 1)
        receipt = self.cli('close', self.folder, '--reason', 'Enough for today.')
        self.assertIn('sittings/000001.json: process exit unobserved.', receipt['coverage']['limitations'])

    @unittest.skipUnless(
        test_episodes.UNDER_TEST or Path('/proc/self/stat').exists(), 'the Python reads process start times from /proc'
    )
    def test_an_actor_that_drops_the_lease_still_holds_the_episode(self):
        self.opening('codex', jobs=())
        self.env.update(FIXTURE_SLEEP='2', FIXTURE_DROP_LEASE='1')
        process = self.background('start', self.folder, '--unattended', '--prompt', 'Survey the modules.')
        process.kill()
        process.wait()
        self.send(None, 'Survey the modules.', error='still running as process')
        self.cli('close', self.folder, '--reason', 'Too early.', error='still running as process')
        self.send('other', 'Something else.', error='still running as process')
        retried = []
        self.wait_for(lambda: self.retry(retried))
        self.assertEqual(retried[0]['response'], 'done 1: Survey the modules.')
        self.assertEqual(len(self.launches()), 1)

    def retry(self, retried):
        result = subprocess.run(
            [
                *test_episodes.BERMAN,
                'start',
                self.folder,
                '--unattended',
                '--prompt',
                'Survey the modules.',
            ],
            cwd=self.base,
            env=self.env,
            text=True,
            capture_output=True,
        )
        if 'in progress' in result.stderr or 'still running' in result.stderr:
            return False
        self.assertEqual(result.returncode, 0, result.stderr)
        retried.append(json.loads(result.stdout))
        return True

    def test_an_opening_that_started_no_session_can_be_tried_again(self):
        for actor in ACTORS:
            with self.subTest(actor=actor):
                self.folder = self.base / 'episodes' / f'{actor}-again'
                self.opening(actor, jobs=())
                self.env['FIXTURE_NO_SESSION'] = '1'
                failed = self.send(None, 'Begin.', code=1)
                del self.env['FIXTURE_NO_SESSION']
                self.assertIsNone(failed['response'])
                self.assertIn('could not reach the service', failed['limitations'][-1])
                self.assertEqual(self.send(None, 'Begin.', code=1), failed)
                self.send('next', 'More.', error='No native session is known')
                retry = ('start', self.folder, '--request', 'again', '--unattended', '--prompt', 'Begin.')
                again = self.cli(*retry)
                self.assertTrue(again['opening'])
                self.assertEqual(again['response'], 'done 1: Begin.')
                launches = len(self.launches())
                self.assertEqual(self.cli(*retry), again)
                self.assertEqual(len(self.launches()), launches)
                self.cli(
                    'resume', self.folder, '--request', 'again', '--unattended', '--prompt', 'Begin.', error='different'
                )
                self.assertEqual(self.send('next', 'More.')['response'], 'done 2: More.')
                self.cli('start', self.folder, '--request', 'third', '--prompt', 'Begin.', error='already started')
                self.cli('close', self.folder, '--reason', 'The second attempt reached the actor.')
                self.assertIn('done 2: More.', (self.folder / 'record/telemetry/parent/conversation.txt').read_text())

    def test_codex_hook_binds_an_attended_second_opening(self):
        self.opening('codex', jobs=())
        self.env['FIXTURE_NO_SESSION'] = '1'
        self.cli('start', self.folder, code=1)
        del self.env['FIXTURE_NO_SESSION']
        self.cli('resume', self.folder, '--request', 'next', error='No native session is known')
        self.cli('start', self.folder, '--request', 'again', '--prompt', 'Begin with the tests.')
        self.assertTrue((self.folder / 'session-id.json').exists())
        self.assertEqual(self.cli('resume', self.folder, '--request', 'next')['exit'], 0)

    def test_a_callback_before_the_transcript_exists_is_not_a_gap(self):
        self.env['FIXTURE_EARLY_HOOK'] = '1'
        state = self.opening('claude', jobs=())
        self.send(None, 'Begin.')
        rows = [json.loads(p.read_text()) for p in (self.folder / 'telemetry/events').iterdir()]
        early = [row for row in rows if row['event']['hook_event_name'] == 'SessionStart']
        self.assertEqual([row.get('observation') for row in early], ['The native transcript did not exist yet.'])
        self.assertNotIn('limitation', early[0])
        # Once a transcript has been captured, its absence is a gap.
        gone = Path(self.env['CLAUDE_CONFIG_DIR']) / 'projects' / 'example' / 'gone.jsonl'
        event = {'hook_event_name': 'Stop', 'session_id': state['id'], 'transcript_path': str(gone)}
        subprocess.run(
            test_episodes.callback('claude'),
            input=json.dumps(event),
            text=True,
            env=dict(self.env, BERMAN_EPISODE=str(self.folder)),
            capture_output=True,
            check=True,
        )
        limitations = self.cli('close', self.folder, '--reason', 'Checked.')['coverage']['limitations']
        self.assertEqual(len([item for item in limitations if 'gone.jsonl' in item]), 1)
        self.assertFalse([item for item in limitations if 'SessionStart' in item or 'did not exist' in item])

    def test_codex_output_binds_a_session_no_callback_bound(self):
        self.env['FIXTURE_WITHOUT_HOOKS'] = '1'
        self.opening('codex', jobs=())
        first = self.send(None, 'Look around.')
        self.assertFalse((self.folder / 'telemetry').exists())
        self.assertEqual(json.loads((self.folder / 'session-id.json').read_text()), first['native_session'])
        self.assertEqual(self.send('more', 'And the docs?')['response'], 'done 2: And the docs?')
        receipt = self.cli('close', self.folder, '--reason', 'The callbacks were never reviewed.')
        self.assertIn('No session callbacks were observed.', receipt['coverage']['limitations'])
        self.assertIn('done 2', (self.folder / 'record/telemetry/parent/conversation.txt').read_text())

    def test_output_naming_another_session_does_not_rebind(self):
        for actor in ACTORS:
            with self.subTest(actor=actor):
                self.folder = self.base / 'episodes' / f'{actor}-other'
                self.opening(actor, jobs=())
                bound = self.send(None, 'Begin.')['native_session']
                self.env['FIXTURE_REPORTED_SESSION'] = 'another-session'
                sitting = self.send('next', 'Continue.')
                del self.env['FIXTURE_REPORTED_SESSION']
                self.assertEqual(sitting['native_session'], 'another-session')
                self.assertIn(f'not the bound session {bound}', sitting['limitations'][0])
                self.assertEqual(json.loads((self.folder / 'session-id.json').read_text()), bound)

    def test_the_checkout_can_change_between_every_encounter(self):
        for actor in ACTORS:
            with self.subTest(actor=actor):
                self.project = self.base / f'{actor} checkout'
                self.project.mkdir()
                self.git('init', '-q')
                self.folder = self.base / 'episodes' / f'{actor}-changing'
                state = self.opening(actor, jobs=())
                (self.project / 'plan.md').write_text('A colleague changed direction.\n')
                self.git('add', 'plan.md')
                self.git('commit', '-q', '-m', 'Change direction')
                self.git('switch', '-q', '-c', 'elsewhere')
                self.assertEqual(self.send(None, 'What changed?')['exit'], 0)
                (self.project / 'ordinary.txt').unlink()
                self.git('rm', '-q', 'plan.md')
                self.git('commit', '-q', '-m', 'Someone finished the work')
                self.assertEqual(self.send('again', 'And now?')['response'], 'done 2: And now?')
                (self.project / 'late.md').write_text('Another change before closing.\n')
                self.git('add', 'late.md')
                self.git('commit', '-q', '-m', 'Late change')
                receipt = self.cli('close', self.folder, '--reason', 'The work went another way.')
                self.assertEqual(receipt['coverage']['limitations'], [])
                self.assertIn('not who changed it', receipt['coverage']['scope'])
                final = json.loads((self.folder / 'record/final-project.json').read_text())
                head = subprocess.run(
                    ['git', '-C', self.project, 'rev-parse', 'HEAD'], capture_output=True, text=True, check=True
                )
                self.assertEqual(final['head'], head.stdout.strip())
                self.assertIsNone(state['initial_project']['head'])
                self.assertTrue(state['initial_project']['observed_at'] < final['observed_at'])

    def test_an_unavailable_checkout_refuses_without_a_sitting(self):
        self.opening('claude', jobs=())
        moved = self.base / 'moved away'
        self.project.rename(moved)
        self.send(None, 'Begin.', error='checkout is unavailable')
        self.assertFalse((self.folder / 'sittings').exists())
        moved.rename(self.project)
        self.assertEqual(self.send(None, 'Begin.')['exit'], 0)

    def test_independent_sessions_stay_outside_the_episode(self):
        for actor in ACTORS:
            with self.subTest(actor=actor):
                self.folder = self.base / 'episodes' / f'{actor}-neighbours'
                self.opening(actor, jobs=())
                bound = self.send(None, 'Begin.')['native_session']
                events = set((self.folder / 'telemetry/events').iterdir())
                independent = dict(self.env, FIXTURE_INDEPENDENT='1')
                subprocess.run([actor], cwd=self.project, env=independent, check=True)
                self.assertEqual(set((self.folder / 'telemetry/events').iterdir()), events)
                self.send('next', 'Continue.')
                subprocess.run([actor], cwd=self.project, env=independent, check=True)
                self.cli('close', self.folder, '--reason', 'Others carried on without Berman.')
                record = self.folder / 'record'
                self.assertEqual(json.loads((self.folder / 'session-id.json').read_text()), bound)
                for path in record.rglob('*'):
                    if path.is_file():
                        self.assertNotIn(b'independent work', path.read_bytes())
