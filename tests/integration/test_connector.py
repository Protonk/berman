"""Drive the operator commands end to end against an in-process guest and a harness double.

The loopback below replaces only the SSH process. Requests, receipts, operation identities,
the executor, the controller and custody are the real ones.
"""

from argparse import Namespace
from contextlib import redirect_stderr
from io import BytesIO, StringIO
import json
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
import zipfile
from uuid import uuid4

from unittest.mock import patch

from berman import cheatsheet, cli, connector, evidence, executor, files, protocol
from berman.archive import package
from berman.connector import Operator
from berman.controller import Controller
from berman.executor import capture
from tests.integration.test_executor import HARNESS


class Loopback:
    """One request in, one receipt out, as the SSH command would give. Can lose a reply."""

    def __init__(self, controller, collection):
        self.controller, self.collection = controller, collection
        self.lose_reply_to = None
        self.down = False
        self.calls = []

    def call(self, command, operation=None, body=None, **fields):
        if self.down:
            raise ValueError('guest unreachable')
        request = dict(protocol=protocol.VERSION, collection_id=self.collection, command=command, **fields)
        if operation:
            request['operation'] = operation
        self.calls.append(command)
        reply = self.controller.execute(request, BytesIO(body.read_bytes() if body else b''))
        if self.lose_reply_to == command:
            self.lose_reply_to = None
            raise ValueError('reply lost after the guest committed')
        return reply


class ConnectorTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        study = self.root / 'guest study'
        self.ident = str(uuid4())
        files.write(study / 'collection.json', {'id': self.ident})
        files.write(
            study / 'connections/example/connection.json',
            {
                'schema': 'berman.connection/1',
                'id': 'example',
                'default_recipe': 'baseline',
                'documents': ['README.md'],
            },
        )
        files.write(
            study / 'recipes/baseline/recipe.json',
            {
                'schema': 'berman.recipe/2',
                'id': 'baseline',
                'instructions': 'packet.md',
                'connections': [],
                'coverage': ['notes'],
                'recording_protocol': 'work-accounts/1',
                'meaning': 'Private recipe rationale',
            },
        )
        (study / 'recipes/baseline/packet.md').write_text('Write an account in {{notes_directory}}.')
        self.project = self.root / 'project'
        self.home = self.root / 'native home'
        self.spool = self.root / 'spool'
        self.home.mkdir()
        self.project.mkdir()
        (self.project / 'README.md').write_text('checkout context')
        for args in (
            ['init', '-q'],
            ['add', 'README.md'],
            [
                '-c',
                'user.name=F',
                '-c',
                'user.email=f@example.invalid',
                '-c',
                'commit.gpgsign=false',
                'commit',
                '-qm',
                'f',
            ],
        ):
            subprocess.run(['git', '-C', self.project, *args], check=True, capture_output=True)
        harness = self.root / 'fixture harness'
        harness.write_text('#!' + sys.executable + '\n' + HARNESS)
        harness.chmod(0o700)
        binding = {'executable': str(harness), 'home': str(self.home), 'project': str(self.project)}
        settings = {
            'collection_id': self.ident,
            'study': 'study',
            'host': 'unused',
            'spool': str(self.spool),
            'bindings': {'example': {'claude': binding, 'codex': binding}},
        }
        self.settings = settings
        self.guest = Loopback(Controller(study), self.ident)
        self.operator = Operator(settings, remote=self.guest)

    def run_command(self, action, run=None, **fields):
        defaults = dict(
            supplement=None,
            one_turn=True,
            reason=None,
            missing_reason=None,
            expected_child=None,
            prompt=None,
            request=None,
        )
        return self.operator.perform(Namespace(action=action, run=run, **{**defaults, **fields}))

    def commission(self, harness='claude', label='first'):
        args = Namespace(
            action='commission', request=label, connection='example', harness=harness, recipe=None, plan=None
        )
        return self.operator.perform(args)

    def sheet(self):
        """The cheatsheet for this study, as an installation on PATH would print it."""
        config = self.root / 'study.json'
        files.write(config, self.settings)
        with patch.object(connector, 'programs', return_value=(['berman-research'], ['berman'])):
            return connector.sheet(config, remote=self.guest)

    def entry(self):
        (only,) = self.sheet()['engagements']
        selected = 'berman-research --config ' + cheatsheet.shown(self.root / 'study.json') + ' '
        return only['state'], [item['command'].removeprefix(selected) for item in only['steps']]

    def test_cheatsheet_names_the_next_command_at_each_stage_and_changes_nothing(self):
        self.assertEqual([], self.sheet()['engagements'])
        run = self.commission()['run']
        self.assertEqual(('commissioned', [f'prepare {run}']), self.entry())
        self.run_command('prepare', run)
        (prepared,) = self.sheet()['engagements']
        operation = self.operator.ticket(run)['operation']
        self.assertEqual(str(capture.operation_dir(self.spool, operation) / 'packet'), prepared['packet'])
        self.assertEqual(('prepared', [f'launch {run}']), self.entry())
        self.assertIn('After the packet is reviewed', prepared['steps'][0]['what'])

        # A first sitting begun here that the guest has not heard of is reported, not offered again.
        intent = capture.operation_dir(self.spool, operation) / 'sittings/0001/intent.json'
        files.write(intent, {'argv': []})
        (begun,) = self.sheet()['engagements']
        self.assertIn('no recorded exit on this host', begun['notes'][0])
        self.assertIn('It starts nothing', begun['steps'][0]['what'])
        intent.unlink()
        intent.parent.rmdir()
        intent.parent.parent.rmdir()

        self.run_command('launch', run)
        self.assertEqual(
            ('in fieldwork', [f'resume {run} --request LABEL', f"closeout {run} --reason 'REASON'"]), self.entry()
        )
        self.assertEqual(1, self.sheet()['engagements'][0]['sittings'])
        self.run_command('end', run, reason='The researcher ended fieldwork')
        self.assertEqual(('closing', [f'closeout {run}']), self.entry())
        self.assertEqual(['So far the ending is recorded.'], self.sheet()['engagements'][0]['notes'])
        self.run_command('closeout', run)
        self.assertEqual(
            ('complete', [f'verify {run}', f"supplement {run} --request LABEL --reason 'REASON'"]), self.entry()
        )
        self.run_command('supplement', run, request='later', reason='The worker continued')
        self.assertEqual(('supplement open', [f'closeout {run} --supplement 1']), self.entry())
        self.run_command('closeout', run, supplement=1)
        self.assertEqual('complete', self.entry()[0])

        # Reading the sheet sends one status and writes nothing on the host.
        before = files.inventory(self.spool)
        self.guest.calls.clear()
        text = cheatsheet.text(self.sheet())
        self.assertEqual((['status'], before), (self.guest.calls, files.inventory(self.spool)))
        self.assertIn(f'1 complete: recording preserved and reading edition published. The latest is {run}.', text)
        self.assertIn('cd ' + cheatsheet.shown(self.project) + ' && claude', text)

        self.guest.down = True
        offline = self.sheet()
        self.assertEqual([], offline['engagements'])
        self.assertTrue(any('did not answer: guest unreachable' in note for note in offline['notes']))
        self.assertIn('commission --request LABEL --connection example --harness codex', cheatsheet.text(offline))

    def test_every_command_the_cheatsheet_prints_is_accepted_by_the_command_line(self):
        run = self.commission()['run']
        sheets = [self.sheet()]
        self.run_command('prepare', run)
        self.run_command('launch', run)
        sheets.append(self.sheet())
        self.run_command('closeout', run, reason='The researcher ended fieldwork')
        self.run_command('supplement', run, request='later', reason='The worker continued')
        sheets.append(self.sheet())
        self.run_command('closeout', run, supplement=1)
        sheets.append(self.sheet())
        with (
            patch.object(cheatsheet, 'DEFAULT_CONFIG', str(self.root / 'none/connector.json')),
            patch.object(connector, 'programs', return_value=(['berman-research'], ['berman'])),
        ):
            sheets.append(connector.sheet(self.root / 'none/connector.json'))
        checked = set()
        for value in sheets:
            steps = [s for e in value['engagements'] or [] for s in e['steps']]
            steps += [s for section in value['sections'] for s in section['steps']]
            for item in steps:
                program, *words = shlex.split(item['command'])
                if program == 'cd':
                    self.assertEqual(['&&'], words[1:2])
                    continue
                parser = connector.arguments() if program == 'berman-research' else cli.arguments()
                with self.subTest(command=item['command']), redirect_stderr(StringIO()):
                    parser.parse_args(words)  # Exits, failing the test, on an unknown command or option.
                checked.add((program, next(w for w in words if not w.startswith('-') and not w.endswith('.json'))))
        self.assertLessEqual(
            {
                ('berman-research', name)
                for name in ('commission', 'prepare', 'resume', 'closeout', 'verify', 'supplement', 'status')
            }
            | {('berman', 'verify'), ('berman', 'recording'), ('berman', 'archive')},
            checked,
        )

    def test_prepare_shows_the_packet_and_the_launch_command(self):
        run = self.commission()['run']
        self.operator.command = ['berman-research', '--config', 'study.json']
        said = StringIO()
        with redirect_stderr(said):
            self.run_command('prepare', run)
        packet = capture.operation_dir(self.spool, self.operator.ticket(run)['operation']) / 'packet'
        self.assertIn(f'Packet: {packet}\n', said.getvalue())
        self.assertIn(f'\n  berman-research --config study.json launch {run}', said.getvalue())

    def test_one_closeout_resumes_from_every_committed_boundary(self):
        run = self.commission()['run']
        self.run_command('prepare', run)
        self.run_command('launch', run)
        with self.assertRaisesRegex(ValueError, '--reason'):
            self.run_command('closeout', run)
        for command in ('ending', 'capture', 'transfer', 'accept', 'preserve', 'publish', 'verify'):
            with self.subTest(lost_reply=command):
                self.guest.lose_reply_to = command
                with self.assertRaisesRegex(ValueError, 'reply lost'):
                    self.run_command('closeout', run, reason='Researcher ended the run')
        first = self.run_command('closeout', run)
        self.assertEqual(('verified', 1, 1), (first['status'], first['sittings'], first['notes']))
        self.assertEqual(first['observed_members'], first['accepted_members'])
        self.assertEqual([], first['omitted'])
        self.assertTrue(first['recording'].endswith('custody/run-001/recording.zip'))
        self.assertTrue(first['edition'].endswith(first['edition_sha256'] + '.zip'))
        self.spool.rename(self.root / 'offline spool')
        self.guest.calls.clear()
        self.assertEqual(first, self.run_command('closeout', run))
        self.assertEqual(['status', 'verify'], self.guest.calls)
        with self.assertRaisesRegex(ValueError, 'different input'):
            self.run_command('closeout', run, reason='Different declaration')

    def test_closeout_transfers_available_files_and_waits_for_a_missing_reason(self):
        run = self.commission()['run']
        self.run_command('prepare', run)
        self.run_command('launch', run)
        folder = self.closed_out(run)
        name = 'packet/notes/account.md'
        (folder / 'collection/payload' / name).unlink()
        blocked = self.run_command('closeout', run)
        self.assertEqual([name], blocked['outstanding'])
        self.assertEqual([name], [s['path'] for s in blocked['unavailable']])
        self.assertNotIn('accept', self.guest.calls)
        reason = 'The staged note was lost'
        self.guest.lose_reply_to = 'accept'
        with self.assertRaisesRegex(ValueError, 'reply lost'):
            self.run_command('closeout', run, missing_reason=reason)
        result = self.run_command('closeout', run)
        self.assertEqual(reason, result['missing_reason'])
        self.assertEqual(result['observed_members'] - 1, result['accepted_members'])
        self.assertEqual([name], [row['path'] for row in result['missing']])
        self.assertIn(name, connector.closeout_text(result))

    def test_closeout_cli_reports_a_damaged_guest_object_until_the_host_can_repair_it(self):
        run = self.commission()['run']
        self.run_command('prepare', run)
        self.run_command('launch', run)
        folder = self.closed_out(run)
        self.run_command('transfer', run)
        path = 'packet/notes/account.md'
        staged = folder / 'collection/payload' / path
        original = staged.read_bytes()
        obj = self.guest.controller.store / 'objects' / files.digest(original)
        obj.chmod(0o600)
        obj.write_bytes(b'x' * len(original))
        staged.unlink()
        settings = self.root / 'connector.json'
        files.write(settings, self.operator.settings)
        args = ['--config', str(settings), 'closeout', run]
        with patch.object(connector, 'Remote', return_value=self.guest):
            with patch('sys.stdout', new_callable=StringIO) as out, patch('sys.stderr', new_callable=StringIO) as err:
                self.assertEqual(1, connector.main([*args, '--json']))
                self.assertEqual([path], json.loads(out.getvalue())['outstanding'])
                self.assertIn('--missing-reason', err.getvalue())
            staged.write_bytes(original)
            with patch('sys.stdout', new_callable=StringIO) as out:
                self.assertEqual(0, connector.main(args))
                self.assertIn('Guest reading edition:', out.getvalue())
        self.assertEqual(1, len(self.run_command('status', run)['result']['repairs']))

    def test_closeout_reports_collection_omissions_and_discovers_the_supplement_edition(self):
        run = self.commission()['run']
        self.run_command('prepare', run)
        self.run_command('launch', run)
        folder = next((self.spool / 'operations').iterdir())
        (folder / 'packet/notes/link').symlink_to('account.md')
        with patch('sys.stderr', new_callable=StringIO) as err:
            first = self.run_command('closeout', run, reason='Ended')
        self.assertIn('packet/notes/link', err.getvalue())
        self.assertEqual(['packet/notes/link'], [row['path'] for row in first['omitted']])
        self.run_command('resume', run, request='later', prompt='continued')
        self.run_command('supplement', run, request='fieldwork-returned', reason='Later work')
        with patch('sys.stderr', new_callable=StringIO):
            later = self.run_command('closeout', run, supplement=1)
        self.assertEqual(1, later['supplement'])
        self.assertNotEqual(first['edition_sha256'], later['edition_sha256'])
        self.assertEqual(later, self.run_command('closeout', run, supplement=1))
        self.assertEqual(first['recording_sha256'], self.run_command('closeout', run)['recording_sha256'])

    def test_a_lost_reply_never_makes_a_second_run_or_a_second_process(self):
        self.guest.lose_reply_to = 'commission'
        with self.assertRaisesRegex(ValueError, 'reply lost'):
            self.commission()
        run = self.commission()['run']
        self.assertEqual(['run-001'], [row['run'] for row in self.run_command('status')['result']])
        self.run_command('prepare', run)
        self.guest.lose_reply_to = 'launch'
        with self.assertRaisesRegex(ValueError, 'reply lost'):
            self.run_command('launch', run)
        first = self.run_command('launch', run)
        self.assertEqual(first, self.run_command('launch', run))
        folder = next((self.spool / 'operations').iterdir())
        self.assertEqual([1], capture.sittings(folder))
        self.assertEqual(3, len(list((folder / 'events').glob('*.json'))))

    def test_guest_down_during_the_engagement_then_everything_reported(self):
        run = self.commission()['run']
        self.run_command('prepare', run)
        self.guest.down = True
        with self.assertRaisesRegex(ValueError, 'unreachable'):
            self.run_command('launch', run)
        with self.assertRaisesRegex(ValueError, 'unreachable'):
            self.run_command('resume', run, prompt='continue offline', request='offline')
        folder = next((self.spool / 'operations').iterdir())
        self.assertEqual([1, 2], capture.sittings(folder))
        self.assertEqual(6, len(list((folder / 'events').glob('*.json'))))
        self.guest.down = False
        self.assertEqual(['preparation'], self.run_command('status', run)['result']['facts'])
        self.run_command('end', run, reason='Ended after the guest returned')
        self.run_command('collect', run)
        status = self.run_command('status', run)['result']
        self.assertEqual(2, status['sittings'])
        self.assertEqual(['preparation', 'ending', 'launch', 'capture'], status['facts'])

    def test_one_unreadable_staged_member_does_not_strand_the_rest(self):
        run = self.commission()['run']
        for action in ('prepare', 'launch'):
            self.run_command(action, run)
        self.run_command('end', run, reason='Ended by the researcher')
        self.run_command('collect', run)
        folder = next((self.spool / 'operations').iterdir())
        (folder / 'collection/payload/packet/notes/account.md').write_bytes(b'damaged after the freeze')
        sent = self.run_command('transfer', run)
        self.assertEqual(['packet/notes/account.md'], [row['path'] for row in sent['unavailable_on_host']])
        self.assertEqual(['packet/notes/account.md'], sent['result']['outstanding'])
        self.assertEqual(len(sent['result']['observed']) - 1, len(sent['result']['transferred']))
        with self.assertRaisesRegex(ValueError, 'never transferred'):
            self.run_command('accept', run)
        accepted = self.run_command('accept', run, missing_reason='The staged account was damaged on the host')
        self.assertEqual(['packet/notes/account.md'], [row['path'] for row in accepted['result']['missing']])
        self.run_command('preserve', run)
        self.run_command('publish', run)
        verified = self.run_command('verify', run)['result']
        self.assertEqual(('passed', 'verified'), (verified['recording']['integrity'], verified['edition']['mode']))
        self.assertTrue(
            any(
                'not accepted into custody: packet/notes/account.md' in t
                for t in verified['recording']['coverage']['unknown']
            )
        )

    def test_sittings_supplement_and_custody_for_each_harness(self):
        for harness in ('claude', 'codex'):
            with self.subTest(harness=harness):
                if harness == 'codex':
                    files.write(self.home / 'hooks.json', capture.definition(self.spool, self.home))
                run = self.commission(harness, label=harness)['run']
                self.run_command('prepare', run)
                self.run_command('launch', run)
                second = self.run_command('resume', run, prompt='continue the work', request='second')
                self.assertEqual('sitting', second['command'])
                self.run_command('end', run, reason='Ended by the researcher')
                self.run_command('collect', run)
                self.assertEqual([], self.run_command('transfer', run)['unavailable_on_host'])
                for action in ('accept', 'preserve', 'publish'):
                    self.run_command(action, run)
                self.assertTrue(
                    self.operator.perform(Namespace(action='qualify', run=run, expected_child=None))['passed']
                )
                # The worker comes back after the ending. Its callbacks wait for a supplement.
                third = self.run_command('resume', run, prompt='one more thing', request='third')
                self.assertEqual(3, third['result']['number'])
                declared = self.operator.perform(
                    Namespace(
                        action='supplement', run=run, request='returned', reason='The worker returned after the ending'
                    )
                )['result']
                self.assertEqual(1, declared['sequence'])
                self.run_command('collect', run, supplement=1)
                self.assertEqual([], self.run_command('transfer', run, supplement=1)['unavailable_on_host'])
                for action in ('accept', 'preserve', 'publish'):
                    self.run_command(action, run, supplement=1)
                verified = self.run_command('verify', run)['result']
                self.assertEqual('verified', verified['edition']['mode'])
                self.assertEqual(['verified'], [s['edition']['mode'] for s in verified['supplements']])
                status = self.run_command('status', run, supplement=1)['result']
                self.assertEqual([], status['outstanding'])
                self.assertEqual(3, status['sittings'])

    def closed_out(self, run):
        self.run_command('end', run, reason='Ended by the researcher')
        self.run_command('collect', run)
        return next((self.spool / 'operations').iterdir())

    def test_a_resume_repeated_after_a_lost_reply_starts_nothing_more(self):
        run = self.commission()['run']
        self.run_command('prepare', run)
        self.run_command('launch', run)
        self.guest.lose_reply_to = 'sitting'
        with self.assertRaisesRegex(ValueError, 'reply lost'):
            self.run_command('resume', run, prompt='continue the work', request='second')
        second = self.run_command('resume', run, prompt='continue the work', request='second')
        self.assertEqual(2, second['result']['number'])
        folder = next((self.spool / 'operations').iterdir())
        self.assertEqual([1, 2], capture.sittings(folder))
        self.assertEqual(6, len(list((folder / 'events').glob('*.json'))))

    def test_a_first_sitting_that_bound_no_session_does_not_block_closeout(self):
        run = self.commission()['run']
        self.run_command('prepare', run)
        with patch('berman.executor.subprocess.Popen', side_effect=OSError('spawn failed')):
            with patch('berman.executor.fingerprint', return_value={}):
                with self.assertRaisesRegex(OSError, 'spawn failed'):
                    self.run_command('launch', run)
        self.run_command('launch', run)
        self.run_command('resume', run, request='second', one_turn=False)
        # The first receipt was reported without a session. Closeout repeats that report unchanged.
        folder = self.closed_out(run)
        self.assertEqual([], self.run_command('transfer', run)['unavailable_on_host'])
        self.run_command('accept', run)
        self.assertTrue(files.read(folder / 'session-id.json'))

    def test_failed_codex_sitting_can_be_published_with_unknown_associated_sessions(self):
        (self.root / 'fixture harness').write_text(
            '#!' + sys.executable + '\nimport sys\nsys.exit(0 if sys.argv[1:] == ["--version"] else 1)\n'
        )
        files.write(self.home / 'hooks.json', capture.definition(self.spool, self.home))
        run = self.commission('codex')['run']
        self.run_command('prepare', run)
        self.run_command('launch', run)
        report = self.run_command('closeout', run, reason='Fixture sitting ended')
        self.assertEqual(report['observed_members'], report['accepted_members'])
        self.assertFalse(self.run_command('qualify', run)['passed'])
        guest = self.guest.controller.root
        recording = self.root / 'detached-recording.zip'
        recording.write_bytes((guest / 'custody' / run / 'recording.zip').read_bytes())
        edition = self.root / 'detached-edition.zip'
        edition.write_bytes(Path(report['edition']).read_bytes())
        manifest = evidence.verify_package(recording, report['recording_sha256'])['manifest']
        self.assertEqual(manifest['streams'], [])
        self.assertIn('No verified parent transcript observed.', manifest['coverage']['unknown'])
        with zipfile.ZipFile(recording) as archive:
            facts = json.loads(archive.read('evidence/controller.json'))['facts']
            self.assertIsNone(facts['capture']['associated_sessions'])
            self.assertEqual(1, facts['launch']['exit']['returncode'])
            self.assertEqual(
                files.digest(archive.read('recording.json')),
                package.verify_edition(edition, report['edition_sha256'])['recording_manifest_sha256'],
            )

    def test_transfer_needs_a_recorded_collection(self):
        run = self.commission()['run']
        for action in ('prepare', 'launch'):
            self.run_command(action, run)
        self.run_command('end', run, reason='Ended by the researcher')
        # Frozen on the host, never reported: the guest has no inventory to check a transfer against.
        folder = next((self.spool / 'operations').iterdir())
        declaration = self.operator.read('closeout', run)['result']['operation']
        executor.collect(self.spool, folder.name, declaration)
        with self.assertRaisesRegex(ValueError, 'Run collect first'):
            self.run_command('transfer', run)
        self.assertNotIn('transfer', self.guest.calls)

    def test_same_length_damage_in_the_guest_store_is_resent(self):
        run = self.commission()['run']
        for action in ('prepare', 'launch'):
            self.run_command(action, run)
        folder = self.closed_out(run)
        self.run_command('transfer', run)
        note = folder / 'collection/payload/packet/notes/account.md'
        stored = self.guest.controller.store / 'objects' / files.digest(note.read_bytes())
        stored.chmod(0o600)
        stored.write_bytes(b'x' * stored.stat().st_size)
        with self.assertRaisesRegex(ValueError, 'damaged in the guest store'):
            self.run_command('accept', run)
        before = self.guest.calls.count('transfer')
        self.assertEqual([], self.run_command('transfer', run)['result']['outstanding'])
        self.assertEqual(1, self.guest.calls.count('transfer') - before)
        self.run_command('accept', run)
        self.assertEqual(1, len(self.run_command('status', run)['result']['repairs']))

    def test_a_missing_parent_transcript_file_still_reaches_custody_as_a_stated_gap(self):
        run = self.commission()['run']
        for action in ('prepare', 'launch'):
            self.run_command(action, run)
        folder = self.closed_out(run)
        transcript = next((folder / 'collection/payload/session/snapshots').glob('*/transcript.jsonl'))
        transcript.write_bytes(b'damaged on the host after the freeze')
        self.assertEqual(1, len(self.run_command('transfer', run)['unavailable_on_host']))
        self.run_command('accept', run, missing_reason='The staged transcript was damaged on the host')
        recording = self.run_command('preserve', run)['result']
        self.assertIn(
            'The parent transcript generation was observed on the host but is not complete in custody. It is not verified here.',
            recording['coverage']['unknown'],
        )
        self.run_command('publish', run)
        verified = self.run_command('verify', run)['result']
        self.assertEqual(('passed', 'verified'), (verified['recording']['integrity'], verified['edition']['mode']))
        # The file became readable again. The accepted collection is closed; nothing more is sent.
        before = self.guest.calls.count('transfer')
        self.assertEqual([], self.run_command('transfer', run)['result']['outstanding'])
        self.assertEqual(before, self.guest.calls.count('transfer'))

    def test_command_line_exit_status_reports_unsent_members(self):
        run = self.commission()['run']
        for action in ('prepare', 'launch'):
            self.run_command(action, run)
        folder = self.closed_out(run)
        (folder / 'collection/payload/packet/notes/account.md').unlink()
        config = self.root / 'connector.json'
        files.write(config, self.operator.settings)
        with patch.object(connector, 'Remote', return_value=self.guest):
            with patch('builtins.print'):
                self.assertEqual(1, connector.main(['--config', str(config), 'transfer', run]))
                self.assertEqual(1, connector.main(['--config', str(config), 'accept', run]))
                self.assertEqual(
                    0, connector.main(['--config', str(config), 'accept', run, '--missing-reason', 'Gone'])
                )
