"""Black-box checks of an installed Berman in disposable homes and independent Git projects."""

import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

RECORDS_GUIDE = Path(__file__).resolve().parents[1] / 'docs' / 'records.md'


def documented_members():
    """The names in the first column of the record table in the reading guide."""
    rows = [line.split('|')[1] for line in RECORDS_GUIDE.read_text().splitlines() if line.startswith('| `')]
    return {name for row in rows for name in re.findall(r'`([^`]+)`', row)}


class EpisodeCase(unittest.TestCase):
    """Disposable homes, an independent Git checkout and scripted actors for each test."""

    def setUp(self):
        self.base = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.project = self.base / 'normal checkout'
        self.project.mkdir()
        subprocess.run(['git', '-C', self.project, 'init', '-q'], check=True)
        self.folder = self.base / 'episodes' / 'decisions'
        self.job = self.base / 'job.md'
        self.job.write_text('Record a decision when one arises.\n')
        binary = self.base / 'bin'
        binary.mkdir()
        self.env = dict(
            os.environ,
            PATH=str(binary) + os.pathsep + os.environ['PATH'],
            HOME=str(self.base),
            CODEX_HOME=str(self.base / '.codex'),
            CLAUDE_CONFIG_DIR=str(self.base / '.claude'),
            FIXTURE_LOG=str(self.base / 'launches.jsonl'),
        )
        self.env.pop('BERMAN_EPISODE', None)
        self.fixture = Path(__file__).parent / 'fixtures/actor.py'
        for name in ('codex', 'claude'):
            shutil.copy(self.fixture, binary / name)
            (binary / name).chmod(0o755)
        c = Path(self.env['CODEX_HOME'])
        c.mkdir()
        (c / 'hooks.json').write_text(json.dumps(self.cli('codex-hooks')))

    def cli(self, *args, error=None, code=0):
        result = subprocess.run(
            [sys.executable, '-I', '-m', 'berman', *map(str, args)],
            cwd=self.base,
            env=self.env,
            text=True,
            capture_output=True,
        )
        if error:
            self.assertNotEqual(0, result.returncode, args)
            self.assertIn(error, result.stderr)
            return
        self.assertEqual(code, result.returncode, result.stderr)
        return json.loads(result.stdout)

    def opening(self, actor, jobs=None):
        jobs = (self.job,) if jobs is None else jobs
        selection = [arg for job in jobs for arg in ('--job', job)]
        return self.cli('open', self.folder, '--actor', actor, '--project', self.project, *selection)

    def launches(self):
        log = Path(self.env['FIXTURE_LOG'])
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def assert_documented(self, record):
        documented = documented_members()
        for path in record.rglob('*'):
            name = str(path.relative_to(record))
            if path.is_file():
                with self.subTest(member=name):
                    self.assertTrue(name in documented or any(name.startswith(d) for d in documented if d[-1] == '/'))


class EpisodeTests(EpisodeCase):
    def full_episode(self, actor):
        subprocess.run([actor], cwd=self.project, env=self.env, check=True)
        self.assertFalse(self.folder.parent.exists())
        state = self.opening(actor)
        self.assertEqual(state, self.opening(actor))
        self.assertEqual(state['initial_project']['head'], None)
        self.assertIn('ordinary.txt', state['initial_project']['status'])
        first = self.cli('start', self.folder)
        self.assertEqual(first['exit'], 0)
        self.assertEqual(first, self.cli('start', self.folder))
        self.assertIsNone(self.cli('status', self.folder)['ending'])
        second = self.cli('resume', self.folder, '--request', 'after-lunch', '--prompt', 'Continue.')
        self.assertEqual(second, self.cli('resume', self.folder, '--request', 'after-lunch', '--prompt', 'Continue.'))
        self.cli(
            'resume', self.folder, '--request', 'after-lunch', '--prompt', 'Different.', error='different instructions'
        )
        (self.folder / 'return' / 'do-not-copy').symlink_to(self.job)
        closed = self.cli('close', self.folder, '--reason', 'My decision is made.')
        self.assertEqual(closed, self.cli('close', self.folder, '--reason', 'My decision is made.'))
        self.cli('start', self.folder, error='closed')
        record = self.folder / 'record'
        self.assert_documented(record)
        self.assertIn('two callers', (record / 'return/decision.md').read_text())
        self.assertFalse((record / 'return/do-not-copy').exists())
        self.assertIn('do-not-copy', str(closed['coverage']))
        self.assertIn('done', (record / 'telemetry/parent/conversation.txt').read_text())
        self.assertEqual(len(list((record / 'telemetry/events').iterdir())), 6)
        self.assertEqual(len(self.cli('status', self.folder)['sittings']), 2)
        copy = self.base / 'detached'
        shutil.copytree(record, copy)
        shutil.rmtree(self.folder)
        shutil.rmtree(self.project)
        digest = closed['sha256']
        self.assertEqual(self.cli('verify', copy, '--sha256', digest)['sha256'], digest)
        review = self.base / 'read these'
        summary = self.cli('review', copy, '--question', 'What did the actor rule out?', '--output', review)
        self.assertEqual(summary['sources'][0]['sha256'], digest)
        material = review / summary['sources'][0]['path']
        self.assertTrue((material / 'return/decision.md').is_file())
        (review / 'answer.md').write_text('See sources/' + state['id'] + '/return/decision.md.')
        self.cli('review', copy, '--question', 'Again?', '--output', review, error='exists')
        (copy / 'return/decision.md').write_text('changed')
        self.cli('verify', copy, error='differ')
        self.assertEqual(self.cli('verify', material)['sha256'], digest)

    def test_codex_episode(self):
        self.full_episode('codex')

    def test_claude_episode(self):
        self.full_episode('claude')

    def test_empty_return_scoped_review_and_changed_ending(self):
        self.opening('codex')
        self.job.write_text('Change the return.')
        self.cli(
            'open', self.folder, '--actor', 'codex', '--project', self.project, '--job', self.job, error='different'
        )
        receipt = self.cli('close', self.folder, '--reason', 'The work moved on.')
        self.assertIn('No sitting', str(receipt))
        self.cli('close', self.folder, '--reason', 'Different.', error='different ending')
        record = self.folder / 'record'
        self.cli(
            'review', record, '--question', 'What happened?', '--output', record / 'nested' / 'review', error='outside'
        )
        self.assertFalse((record / 'nested').exists())
        self.cli('verify', record, '--sha256', 'wrong', error='differs')
        output = self.base / 'review'
        self.cli('review', record, record, '--question', 'When?', '--output', output, error='twice')
        self.assertFalse(output.exists())

    def test_review_two_independently_closed_episodes(self):
        ids = []
        sources = []
        for name, actor in (('first', 'claude'), ('later', 'codex')):
            self.folder = self.base / 'episodes' / name
            ids.append(self.opening(actor)['id'])
            self.cli('start', self.folder)
            sources.append(self.cli('close', self.folder, '--reason', name)['record'])
        output = self.base / 'returning-producer'
        result = self.cli(
            'review', *sources, '--question', 'What evidence would change the next decision?', '--output', output
        )
        self.assertEqual([item['episode'] for item in result['sources']], ids)
        for item in result['sources']:
            source = output / item['path']
            self.assertTrue((source / 'return/decision.md').is_file())
            self.assertEqual(self.cli('verify', source)['sha256'], item['sha256'])

    def test_ordinary_callback_requires_no_episode_files(self):
        result = subprocess.run(
            [sys.executable, '-I', '-m', 'berman.capture', 'codex'],
            env=self.env,
            input='ordinary input',
            text=True,
            capture_output=True,
            check=True,
        )
        self.assertEqual(json.loads(result.stdout), {})
        self.assertFalse(self.folder.parent.exists())

    def test_wrong_parent_and_ended_callback_are_ignored(self):
        self.opening('codex')
        self.cli('start', self.folder)
        events = self.folder / 'telemetry/events'
        first = set(events.iterdir())
        row = {'hook_event_name': 'Stop', 'session_id': 'different'}
        env = dict(self.env, BERMAN_EPISODE=str(self.folder))
        cmd = [sys.executable, '-I', '-m', 'berman.capture', 'codex']
        subprocess.run(cmd, input=json.dumps(row), text=True, env=env, capture_output=True, check=True)
        self.assertEqual(first, set(events.iterdir()))
        self.cli('close', self.folder, '--reason', 'Done.')
        row['session_id'] = json.loads((self.folder / 'session-id.json').read_text())
        subprocess.run(cmd, input=json.dumps(row), text=True, env=env, capture_output=True, check=True)
        self.assertEqual(first, set(events.iterdir()))

    def test_busy_sitting_and_path_separation(self):
        self.cli(
            'open',
            self.project / 'episode',
            '--actor',
            'claude',
            '--project',
            self.project,
            '--job',
            self.job,
            error='separate',
        )
        self.opening('claude')
        with (self.folder / '.sitting.lock').open('a+b') as lease:
            fcntl.flock(lease, fcntl.LOCK_EX)
            self.cli('close', self.folder, '--reason', 'Done.', error='in progress')
        self.assertFalse((self.folder / 'ending.json').exists())

    def test_unrelated_directory_stays_empty_on_a_mistyped_command(self):
        empty = self.base / 'empty'
        empty.mkdir()
        self.cli('start', empty, error='No such file')
        self.assertEqual(list(empty.iterdir()), [])

    def test_closing_finds_a_claude_transcript_when_no_callback_arrived(self):
        self.env['FIXTURE_WITHOUT_HOOKS'] = '1'
        self.opening('claude')
        self.cli('start', self.folder)
        self.assertFalse((self.folder / 'telemetry').exists())
        receipt = self.cli('close', self.folder, '--reason', 'The callbacks never ran.')
        record = self.folder / 'record'
        self.assert_documented(record)
        self.assertIn('done', (record / 'telemetry/parent/conversation.txt').read_text())
        self.assertIn('No session callbacks were observed.', receipt['coverage']['limitations'])

    def test_unreadable_transcript_does_not_discard_the_actor_return(self):
        self.opening('claude')
        self.cli('start', self.folder)
        source = next((self.folder / 'telemetry/session/snapshots').glob('*/transcript.jsonl'))
        source.write_text('damaged')
        receipt = self.cli('close', self.folder, '--reason', 'Keep the account with the gap.')
        self.assertIn('Could not verify the staged parent', str(receipt['coverage']))
        self.assertTrue((self.folder / 'record/return/decision.md').is_file())

    def test_templates_are_opt_in_and_existing_output_is_preserved(self):
        destination = self.base / 'jobs'
        self.cli('template', destination)
        note = destination / 'note-taking.md'
        self.assertIn('a reader who was not there', note.read_text())
        handover = destination / 'handover.md'
        self.assertIn('HANDOVER.md', handover.read_text())
        for selected, other in ((note, handover), (handover, note)):
            with self.subTest(selected=selected.name):
                state = self.cli(
                    'open',
                    self.folder / selected.stem,
                    '--actor',
                    'claude',
                    '--project',
                    self.project,
                    '--job',
                    selected,
                )
                self.assertEqual(state['jobs'], [selected.read_text()])
                self.assertIn(selected.read_text().rstrip(), state['opening'])
                self.assertNotIn(other.read_text().splitlines()[0], state['opening'])
        note.write_text('custom')
        self.cli('template', destination, error='exists')
        self.assertEqual(note.read_text(), 'custom')

    def test_compose_jobs_in_one_actor_opening_and_record(self):
        destination = self.base / 'jobs'
        self.cli('template', destination)
        note = destination / 'note-taking.md'
        handover = destination / 'handover.md'
        command = ('open', self.folder, '--actor', 'codex', '--project', self.project)
        selection = ('--job', handover, '--job', note)
        state = self.cli(*command, *selection)
        jobs = [handover.read_text(), note.read_text()]
        self.assertEqual(state['jobs'], jobs)
        self.assertNotIn('job', state)
        self.assertLess(state['opening'].index(jobs[0].rstrip()), state['opening'].index(jobs[1].rstrip()))
        self.assertEqual(self.cli(*command, *selection), state)
        self.cli(*command, '--job', note, '--job', handover, error='different instructions')
        self.cli(*command, '--job', handover, error='different instructions')
        handover.unlink()
        note.unlink()
        self.cli('start', self.folder)
        self.cli('close', self.folder, '--reason', 'The opening was delivered.')
        record = self.folder / 'record'
        self.assertEqual((record / 'opening.md').read_text(), state['opening'])
        self.assertEqual(json.loads((record / 'episode.json').read_text())['jobs'], jobs)
        self.cli('verify', record)

    def test_blank_second_job_leaves_no_episode(self):
        blank = self.base / 'blank.md'
        blank.write_text('  \n')
        self.cli(
            'open',
            self.folder,
            '--actor',
            'claude',
            '--project',
            self.project,
            '--job',
            self.job,
            '--job',
            blank,
            error='blank.md',
        )
        self.assertFalse(self.folder.exists())

    def test_edited_opening_refuses_a_sitting(self):
        self.opening('claude')
        (self.folder / 'opening.md').write_text('Something else.\n')
        self.cli('start', self.folder, error='opening.md differs')
        self.assertFalse((self.folder / 'sittings').exists())
