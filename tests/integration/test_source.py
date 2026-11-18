"""Standalone source, workspace selection and generic editions, with no harness and no session."""

import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from berman import cli, verify
from berman import workspace as workspaces
from berman.archive import builder
from berman.files import write
from berman.records import Store
from tests.integration.test_connections import orchard
from tests.support import legacy
from tests.support.workspace import git_repository, state, workspace


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class StandaloneTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()

    def test_discovery_stops_at_repository_boundaries(self):
        (self.base / 'berman.json').write_text('{}')
        child = self.base / 'child'
        (child / '.git').mkdir(parents=True)
        (child / 'nested').mkdir()
        self.assertIsNone(workspaces.discover(child / 'nested'))
        (child / 'berman.json').write_text('{}')
        self.assertEqual(workspaces.discover(child / 'nested'), child)

    def test_software_verification_never_loads_a_study(self):
        with mock.patch.object(verify.records, 'Store', side_effect=AssertionError('study loaded')):
            result = verify.software()
        self.assertEqual(result['verdict'], 'passed')
        self.assertEqual(set(result['checks']), {'installation', 'dependencies', 'frozen-collector', 'schemas'})
        self.assertEqual(result['scope'], 'software')

    def test_entrypoint_ignores_pythonpath_startup_code(self):
        hostile = self.base / 'hostile'
        hostile.mkdir()
        marker = self.base / 'startup-ran'
        (hostile / 'sitecustomize.py').write_text(f'open({str(marker)!r}, "w").write("loaded")\n')
        result = subprocess.run(
            [sys.executable, '-I', '-B', '-m', 'berman', '--help'],
            env=dict(os.environ, PYTHONPATH=str(hostile)),
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(marker.exists())

    def test_an_external_workspace_is_read_and_builds_a_generic_edition(self):
        root = self.base / 'study'
        (self.base / 'codex').mkdir()
        project = self.base / 'stewart'
        git_repository(project, {'README.md': 'Synthetic legacy project\n'})
        workspace(root, self.base, project, documents=['README.md'])
        orchard(root, self.base, default='orchard')
        store = Store(root)
        finished = legacy.prepare(store)
        legacy.start(store, finished, end=True)
        legacy.finish(store, finished, 'Synthetic engagement completed')
        (root / 'README.md').write_text('Selected context only.\n')
        write(root / 'edition.json', {'schema': 'berman.edition/1', 'documents': ['README.md']})
        build = builder.select(root, 'connections', ['orchard'])
        generated, _ = builder.render(build)
        payload = builder.assemble(build, generated)
        self.assertIn('README.md', payload)
        self.assertNotIn('berman.json', payload)
        self.assertFalse(any(p.startswith(('tools/', 'hooks/', 'recipes/')) for p in payload))
        for tail in ([], ['--verify']):
            result = subprocess.run(
                [sys.executable, '-I', '-B', '-m', 'berman.archive.builder', '--root', str(root), *tail],
                text=True,
                capture_output=True,
                cwd=self.base,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
        result = subprocess.run(
            [sys.executable, '-I', '-B', '-m', 'berman', '--root', str(root), '--json', 'status'],
            text=True,
            capture_output=True,
            cwd=self.base,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        status = json.loads(result.stdout)
        self.assertEqual((status['archived'], status['runs'], status['unsettled']), (1, [], []))
        write(root / 'edition.json', {'schema': 'berman.edition/1', 'documents': ['../outside']})
        with self.assertRaisesRegex(ValueError, 'relative paths'):
            builder.assemble(build, generated)


class RetiredWriterTests(unittest.TestCase):
    """The co-located writer's commands are refused plainly, and a workspace with open work is left alone."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.root = self.base / 'study'
        (self.base / 'codex').mkdir()
        git_repository(self.base / 'stewart', {'README.md': 'Synthetic legacy project\n'})
        workspace(self.root, self.base, self.base / 'stewart', documents=['README.md'])
        self.store = Store(self.root)

    def test_every_writer_command_is_refused_with_where_fieldwork_now_runs(self):
        opened = legacy.prepare(self.store)
        legacy.start(self.store, opened)
        before = state(self.base)
        root = ['--root', str(self.root)]
        attempts = {
            'init': ['init', str(self.base / 'new-study'), '--project', str(self.base / 'stewart')],
            'ready': [*root, 'ready'],
            'new': [*root, 'new', '--finish-previous', 'It ended'],
            'launch': [*root, 'launch', '--run', opened],
            'finish': [*root, 'finish', '--reason', 'It ended', '--run', opened],
            'supplement': [*root, 'supplement', '--reason', 'Later activity'],
            'cancel': [*root, 'cancel', '--reason', 'Unwanted'],
            'dev': [*root, 'dev', '--harness', 'codex'],
            'cheatsheet': [*root, 'cheatsheet'],
            'capture': [*root, 'capture', 'install', '--adapter', 'berman-codex'],
            'support-trial': [
                *root,
                'support-trial',
                'prepare',
                '--evidence',
                'experiments/x',
                '--adapter',
                'berman-codex',
            ],
        }
        self.assertEqual(set(attempts), set(cli.RETIRED))
        for name, argv in attempts.items():
            with self.subTest(command=name):
                code, out, err = run(*argv)
                self.assertEqual((code, out), (2, ''))
                self.assertIn(f'berman {name} is not part of this version', err)
                self.assertIn('only through the controller and executor', err)
                self.assertIn('has to be closed with the release that opened it', err)
        code, out, err = run('--json', *attempts['finish'])
        self.assertEqual((code, out, json.loads(err)['state']), (2, '', 'needs-attention'))
        self.assertEqual(state(self.base), before, 'a refused command changed the workspace')
        self.assertFalse((self.base / 'new-study').exists())
        self.assertNotIn('ending', self.store.record(opened))

    def test_only_reading_commands_and_the_two_fieldwork_entry_points_remain(self):
        with contextlib.redirect_stdout(io.StringIO()) as out, self.assertRaises(SystemExit) as stopped:
            cli.main(['--help'])
        self.assertEqual(stopped.exception.code, 0)
        text = ' '.join(out.getvalue().split())
        self.assertIn('{remote,executor,version,status,verify,archive,recording}', text)
        self.assertIn(
            'Fieldwork is commissioned, prepared, launched, ended, collected and preserved only through', text
        )
        self.assertRegex(text, r'read- ?only in this version')
        with contextlib.redirect_stdout(io.StringIO()) as out, self.assertRaises(SystemExit):
            cli.main(['recording', '--help'])
        self.assertIn('{verify,materialize,export}', out.getvalue())

    def test_a_reading_command_without_a_workspace_says_what_it_reads(self):
        empty = self.base / 'empty'
        (empty / '.git').mkdir(parents=True)
        with mock.patch.object(workspaces.Path, 'cwd', return_value=empty):
            code, out, err = run('status')
        self.assertEqual((code, out), (2, ''))
        self.assertIn('reads a co-located workspace', err)
        self.assertIn('A guest study is operated with berman remote', err)
        self.assertNotIn('berman init', err)


if __name__ == '__main__':
    unittest.main()
