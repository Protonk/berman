"""Several connections in one co-located workspace: attribution, each plan's own sequence, and mixed editions.

The workspace and its runs are synthetic, written by tests/support/legacy.py as release 0.26.3
wrote them. Nothing here prepares or closes an engagement: these are the reader's facts.
"""

import csv
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest
import zipfile

from berman import files, records
from berman.files import read, write
from berman.records import Store
from tests.support import legacy
from tests.support.workspace import git_repository, workspace

INQUIRY_RELATIONSHIP = (
    'Independent: orchard fieldwork neither consumes, resets, nor reinterprets the Stewart '
    'repeat-then-Astra commitment.'
)


def orchard(root, base, relationship=INQUIRY_RELATIONSHIP, default='stewart'):
    """A disposable second project configured only through the connection interface."""
    git_repository(
        base / 'orchard', {'GUIDE.md': 'How the orchard project works.\n', 'notes/plan.txt': 'Planting plan.\n'}
    )
    write(
        root / 'connections/orchard/connection.json',
        {
            'schema': 'berman.connection/1',
            'id': 'orchard',
            'name': 'Orchard',
            'version': 1,
            'description': 'A disposable second repository with no Stewart bootstrap or auditor.',
            'documents': ['GUIDE.md', 'notes/plan.txt', 'HANDOFF.md'],
            'checks': [],
            'readers': [],
        },
    )
    recipe = root / 'recipes/orchard-accounts'
    write(
        recipe / 'recipe.json',
        {
            'schema': 'berman.recipe/2',
            'id': 'orchard-accounts',
            'connections': ['orchard'],
            'coverage': ['notes'],
            'recording_protocol': 'work-accounts/1',
            'instructions': 'INSTRUCTIONS.md',
            'source': 'fixture',
            'meaning': 'fixture',
        },
    )
    (recipe / 'INSTRUCTIONS.md').write_text(
        '# Orchard accounts\n\nWrite accounts of the work in `{{notes_directory}}`.\n'
    )
    write(
        root / 'plans/orchard-study/plan.json',
        {
            'schema': 'berman.research-plan/1',
            'id': 'orchard-study',
            'connection': 'orchard',
            'steps': [
                {'id': 'orchard-first', 'recipe': 'orchard-accounts'},
                {'id': 'orchard-second', 'recipe': 'orchard-accounts'},
            ],
            'relationships': [{'plan': 'stewart-fieldwork-roles', 'relationship': relationship}]
            if relationship
            else [],
        },
    )
    config = read(root / 'berman.json')
    config['default_connection'] = default
    config['connections']['orchard'] = {
        'definition': 'connections/orchard/connection.json',
        'checkout': str(base / 'orchard'),
        'plan': 'plans/orchard-study/plan.json',
        'capture': 'berman-codex',
    }
    write(root / 'berman.json', config)


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name).resolve()
        self.root = self.base / 'berman'
        self.home = self.base / 'codex'
        self.home.mkdir()
        workspace(self.root, self.base, self.base / 'stewart', documents=['README.md', 'COORDINATOR-HANDOFF.md'])
        git_repository(self.base / 'stewart', {'README.md': 'Stewart\n', 'COORDINATOR-HANDOFF.md': 'Handoff\n'})
        orchard(self.root, self.base)
        self.reload()

    def reload(self):
        self.store = Store(self.root)

    def status(self):
        return records.status(self.store)

    def finished(self, connection=None):
        """One preserved run on a connection, closed as release 0.26.3 closed it."""
        run = legacy.prepare(self.store, connection)
        legacy.start(self.store, run)
        legacy.finish(self.store, run, 'done')
        return run

    def default(self, name):
        config = read(self.root / 'berman.json')
        config['default_connection'] = name
        write(self.root / 'berman.json', config)
        self.reload()


class WorkspaceFormatTests(unittest.TestCase):
    def test_the_single_project_configuration_is_refused_as_another_releases_format(self):
        with tempfile.TemporaryDirectory(prefix='berman-old-format-') as temporary:
            root = Path(temporary).resolve()
            write(root / 'berman.json', {'schema': 'berman.configuration/1', 'project': str(root / 'project')})
            with self.assertRaisesRegex(ValueError, r'berman.configuration/1 format is not read by this version'):
                Store(root)


class SecondConnectionTests(Base):
    def test_each_plan_reads_only_its_own_connections_started_runs(self):
        stewart = legacy.prepare(self.store)
        legacy.start(self.store, stewart)
        one = legacy.prepare(self.store, 'orchard')
        self.assertEqual((stewart, one), ('run-001', 'run-002'))
        record = self.store.record(one)
        self.assertEqual((record['connection']['id'], record['plan']['id']), ('orchard', 'orchard-study'))
        # A prepared, unstarted packet consumes no step; a started one does.
        status = self.status()
        self.assertEqual(status['connections']['stewart']['next_recipe'], 'participant-inquiry')
        self.assertEqual(status['connections']['orchard']['next_step'], 'orchard-first')
        legacy.start(self.store, one)
        self.assertEqual(self.status()['connections']['orchard']['next_step'], 'orchard-second')
        # Changing the default connection reattributes nothing.
        self.default('orchard')
        self.assertEqual(
            {r['run']: r['connection'] for r in self.status()['runs']}, {stewart: 'stewart', one: 'orchard'}
        )
        self.assertEqual(self.status()['connections']['stewart']['next_recipe'], 'participant-inquiry')

    def test_another_connections_runs_neither_advance_nor_invalidate_stewart(self):
        support = self.root / 'support-trial'
        write(support / 'source-layout.json', {'runs': [{'run': 'run-900101', 'current_root': 'runs/run-900101'}]})
        write(support / 'runs/run-900101/session/binding.json', {'run': 'run-900101', 'session_id': 'support-session'})
        write(self.home / 'berman-capture/support-session.json', {'run': 'run-900101', 'session_id': 'support-session'})
        self.store.catalogue.support = ['support-trial']
        for _ in range(2):
            self.finished('orchard')
        status = self.status()
        self.assertEqual(status['connections']['stewart']['next_recipe'], 'work-accounts')
        self.assertIsNone(status['connections']['orchard']['next_recipe'])
        self.assertEqual(status['connections']['orchard']['plan_note'], 'every step was started')
        # A closed support collection's registration is not a research run.
        self.assertEqual([r['run'] for r in self.store.discover()], ['run-001', 'run-002'])
        # An encounter without a plan association neither advances nor obstructs Stewart's plan.
        packet = self.store.field / 'run-010'
        (packet / 'notes').mkdir(parents=True)
        (packet / 'INSTRUCTIONS.md').write_text('Write accounts.\n')
        write(packet / 'run.json', {'run': 'run-010', 'connection': 'stewart'})
        write(packet / 'session/binding.json', {'run': 'run-010', 'session_id': 'unplanned-session'})
        rows = self.store.discover()
        row = next(r for r in rows if r['run'] == 'run-010')
        self.assertEqual(
            (row['connection'], row['connection_basis'], row['started']), ('stewart', 'packet metadata', True)
        )
        self.assertEqual(self.store.connections['stewart'].plan.next_step(rows)['recipe'], 'work-accounts')

    def test_a_problem_on_one_connection_is_reported_there_and_not_on_another(self):
        one = legacy.prepare(self.store, 'orchard')
        shutil.rmtree(self.store.field / one)
        status = self.status()
        self.assertIn('packet is missing', status['connections']['orchard']['problems'][0])
        self.assertEqual(status['connections']['stewart']['problems'], [])
        self.assertEqual(status['unsettled'], [], 'what a damaged run awaits is unknown')


class ArchiveEditionTests(Base):
    def builder(self):
        from berman.archive import builder

        return builder

    def build(self, edition, connections, builder=None):
        builder = builder or self.builder()
        build = builder.select(self.root, edition, connections)
        generated, _ = builder.render(build)
        payload = builder.assemble(build, generated)
        return build, generated, payload, builder.make_zip(payload, build.edition.prefix)

    def mixed(self):
        """One finished Stewart run and one finished orchard run in the disposable root."""
        for name in set(self.builder().CONTEXT_DOCUMENTS) | set(self.builder().EDITION_DOCUMENTS):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f'Fixture framing: {name}\n')
        for name in ('AGENTS.md', '.gitignore'):
            (self.root / name).write_text('Fixture support file.\n')
        stewart = legacy.prepare(self.store)
        legacy.start(self.store, stewart)
        legacy.finish(self.store, stewart, 'Stewart engagement ended')
        one = legacy.prepare(self.store, 'orchard')
        legacy.start(self.store, one)
        (self.store.field / one / 'notes/account.md').write_text('An orchard account with a path /tmp/orchard-note.\n')
        legacy.finish(self.store, one, 'Orchard engagement ended')
        return {'run': stewart}, {'run': one}

    def test_mixed_edition_identifies_connections_without_stewart_explanations(self):
        stewart, one = self.mixed()
        build, generated, payload, zipped = self.build('connections', ['stewart', 'orchard'])
        self.assertEqual(build.selection, {'edition': 'connections', 'connections': ['orchard', 'stewart'], 'runs': []})
        edition = json.loads(generated['review/EDITION.json'])
        self.assertEqual(edition['namespace'], 'berman-analysis/')
        self.assertEqual(
            [(r['run'], r['connection']) for r in edition['runs']],
            [(stewart['run'], 'stewart'), (one['run'], 'orchard')],
        )
        self.assertTrue(all(row['attribution'] for row in edition['runs']))
        reader = generated[f'review/{one["run"]}.txt'].decode()
        self.assertTrue(reader.startswith(f'ORCHARD FIELDWORK: {one["run"]}\n\nConnection: orchard (attributed by '))
        self.assertNotIn('Stewart', reader)
        self.assertNotIn('EVIDENCE EXPORTS', reader)
        self.assertIn('INCLUDED SESSION CONVERSATION FILES', reader)
        self.assertIn('context/berman/closing-project/', reader)
        self.assertIn('An orchard account', reader)
        self.assertTrue(
            generated[f'review/{stewart["run"]}.txt'].decode().startswith(f'STEWART FIELDWORK: {stewart["run"]}')
        )
        rows = list(csv.DictReader(io.StringIO(generated['review/transcript-map.csv'].decode())))
        kinds = {r['record_kind'] for r in rows if r['run'] == one['run']}
        self.assertNotIn('rendered manager conversation', kinds)
        self.assertTrue(all(row['raw_availability'] == 'included' for row in rows))
        self.assertIn(b'DECODED NATIVE STREAMS', generated['review/events.txt'])
        guide = generated['ARCHIVE-GUIDE.txt'].decode()
        self.assertIn(f'  orchard (Orchard): {one["run"]}', guide)
        self.assertIn('Orchard: no specialized readers or coverage notes are recorded', guide)
        self.assertNotIn('Run 003', guide)
        with zipfile.ZipFile(io.BytesIO(zipped)) as archive:
            names = archive.namelist()
        self.assertTrue(all(n.startswith('berman-analysis/') for n in names))
        self.assertIn(f'berman-analysis/runs/{one["run"]}/notes/account.md', names)
        refs = generated['review/external-references.csv'].decode()
        self.assertIn('/tmp/orchard-note', refs)
        # The Stewart edition keeps Stewart runs only, with its own namespace and framing.
        build, generated, payload, zipped = self.build('stewart', [])
        self.assertEqual(build.runs, (stewart['run'],))
        self.assertEqual([r['run'] for r in json.loads(payload['source-layout.json'][0])['runs']], [stewart['run']])
        self.assertNotIn(f'runs/{one["run"]}/ASSIGNMENT.md', payload)
        with zipfile.ZipFile(io.BytesIO(zipped)) as archive:
            self.assertTrue(all(n.startswith('stewart-analysis/') for n in archive.namelist()))
        with self.assertRaisesRegex(ValueError, 'outside this edition'):
            self.builder().select(self.root, 'stewart', [], [one['run']])

    def test_editions_built_in_one_process_share_no_state(self):
        self.mixed()
        fresh_stewart = self.build('stewart', [])[1:]
        fresh_mixed = self.build('connections', ['stewart', 'orchard'])[1:]
        builder = self.builder()
        self.assertEqual(self.build('connections', ['stewart', 'orchard'], builder)[1:], fresh_mixed)
        self.assertEqual(self.build('stewart', [], builder)[1:], fresh_stewart)
        self.assertEqual(self.build('connections', ['stewart', 'orchard'], builder)[1:], fresh_mixed)

    def test_committed_snapshot_streams_use_their_preservation_manifest(self):
        stewart, orchard = self.mixed()
        build, generated, _, _ = self.build('connections', ['stewart', 'orchard'])
        blocks = [json.loads(line) for line in generated['review/decoded-blocks.jsonl'].splitlines()]
        self.assertEqual(len(blocks), 2)
        for prepared in (stewart, orchard):
            run = prepared['run']
            manifest = read(self.root / 'recordings' / run / 'manifest.json')
            stream = next(row for row in manifest['streams'] if row['format'] == 'codex-jsonl/1')
            self.assertIn('/session/snapshots/', stream['path'])
            block = next(item for item in blocks if item['run'] == run)
            self.assertEqual(block['source_path'], stream['path'])
            self.assertEqual(block['format'], stream['format'])
            self.assertEqual(block['source_sha256'], files.digest((self.root / stream['path']).read_bytes()))
            self.assertEqual(block['content'], 'done')
            self.assertIn(stream['path'].encode(), generated[f'review/{run}.txt'])
        report = self.builder().edition(build)
        self.assertEqual(self.builder().verify_edition(self.root / report['archive_path'])['freshness'], 'not-checked')

    def test_mutable_operation_changes_do_not_reassign_preserved_runs(self):
        stewart, orchard = self.mixed()
        for prepared in (stewart, orchard):
            path = self.root / 'operations' / prepared['run'] / 'operation.json'
            record = read(path)
            record['connection']['id'] = 'later-corrupt-view'
            write(path, record)
        build = self.builder().select(self.root, 'connections', ['stewart', 'orchard'])
        self.assertEqual(build.connection(stewart['run'])['id'], 'stewart')
        self.assertEqual(build.connection(orchard['run'])['id'], 'orchard')

    def test_recording_custody_replaces_a_missing_or_stale_mutable_inventory(self):
        stewart, orchard = self.mixed()
        index = self.root / 'source-layout.json'
        original = read(index)
        stale = read(index)
        item = next(row for row in stale['runs'][0]['entries'] if row['type'] == 'file')
        item['sha256'] = '0' * 64
        write(index, stale)
        selected = self.builder().select(self.root, 'connections')
        self.assertEqual(selected.layout['runs'], original['runs'])
        index.unlink()
        selected = self.builder().select(self.root, 'connections')
        self.assertEqual(set(selected.runs), {stewart['run'], orchard['run']})
        result = self.builder().edition(selected)
        self.assertFalse(index.exists())
        self.assertEqual(self.builder().verify_edition(self.root / result['archive_path'])['mode'], 'verified')

    def test_changed_retained_attribution_cannot_hide_a_run_during_selection(self):
        stewart, _ = self.mixed()
        path = self.root / 'recordings' / stewart['run'] / 'evidence/operation.json'
        authority = read(path)
        authority['connection']['id'] = 'orchard'
        write(path, authority)
        with self.assertRaisesRegex(ValueError, 'Recording evidence bytes differ'):
            self.builder().select(self.root, 'connections', ['stewart'])

    def test_default_selection_keeps_explicitly_unattributed_historical_runs(self):
        stewart, orchard = self.mixed()
        path = self.root / 'recordings' / stewart['run'] / 'manifest.json'
        manifest = read(path)
        manifest['profile'] = 'historical-import'
        manifest['evidence']['operation'] = None
        manifest['coverage']['unknown'].append('No historical operation evidence or connection attribution')
        manifest['attribution'] = {'connection': None, 'basis': 'unattributed', 'evidence': None}
        write(path, manifest)
        selected = self.builder().select(self.root, 'connections')
        self.assertEqual(set(selected.runs), {stewart['run'], orchard['run']})
        self.assertIsNone(selected.connection(stewart['run'])['id'])

    def test_manifest_attribution_must_agree_before_a_connection_can_exclude_it(self):
        stewart, _ = self.mixed()
        path = self.root / 'recordings' / stewart['run'] / 'manifest.json'
        manifest = read(path)
        manifest['attribution']['connection'] = 'orchard'
        write(path, manifest)
        with self.assertRaisesRegex(ValueError, 'Retained operation and connection attribution disagree'):
            self.builder().select(self.root, 'connections', ['stewart'])


if __name__ == '__main__':
    unittest.main()
