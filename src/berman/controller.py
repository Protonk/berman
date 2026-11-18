"""One SSH invocation processes one v1 command against a guest-owned study."""

import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace

from berman import config, custody, evidence, files, protocol, recipes, schemas
from berman.archive.package import verify_edition


class Controller:
    def __init__(self, root):
        self.root = root.resolve(strict=True)
        self.collection = evidence.collection(self.root)
        if (self.root / 'berman.json').exists():
            raise ValueError('Select a controller study directory separate from the retained legacy installation')
        self.store = self.root / 'controller'
        self.store.mkdir(mode=0o700, exist_ok=True)

    def execute(self, request, body):
        if request.get('protocol') != protocol.VERSION or request.get('collection_id') != self.collection:
            raise ValueError('Protocol version or collection identity mismatch')
        command = request['command']
        with closing(sqlite3.connect(self.store / 'ledger.sqlite', timeout=60)) as db, db:
            db.row_factory = sqlite3.Row
            db.execute('CREATE TABLE IF NOT EXISTS runs (number INTEGER PRIMARY KEY, state TEXT NOT NULL)')
            db.execute(
                'CREATE TABLE IF NOT EXISTS operations '
                '(id TEXT PRIMARY KEY, request TEXT NOT NULL, receipt TEXT NOT NULL)'
            )
            db.execute('BEGIN IMMEDIATE')
            db.execute(
                'CREATE TABLE IF NOT EXISTS study (singleton INTEGER PRIMARY KEY CHECK(singleton=1), id TEXT NOT NULL)'
            )
            db.execute('INSERT OR IGNORE INTO study VALUES (1,?)', (self.collection,))
            if db.execute('SELECT id FROM study').fetchone()[0] != self.collection:
                raise ValueError('Collection identity differs from the controller ledger')
            if command == 'verify':
                facts = self.run_state(db, request['run'])['facts']
                result = {}
                if 'preserve' not in facts:
                    raise ValueError('No preserved recording for this run')
                recording = custody.verify_recording(self.root, facts['preserve'])
                result['recording'] = {k: recording[k] for k in ('package_sha256', 'integrity', 'coverage')}
                if 'publish' in facts:
                    edition = facts['publish']
                    result['edition'] = verify_edition(self.root / edition['archive_path'], edition['archive_sha256'])
                return dict(protocol=protocol.VERSION, collection_id=self.collection, command=command, result=result)
            if command in ('assignment', 'closeout'):
                state = self.run_state(db, request['run'])
                value = state['assignment']
                if command == 'closeout':
                    ending = state['facts'].get('ending')
                    if not ending:
                        raise ValueError('The engagement has no researcher ending declaration')
                    value = dict(collection_id=self.collection, run=state['run'], operation=ending['operation'])
                return dict(protocol=protocol.VERSION, collection_id=self.collection, command=command, result=value)
            if command == 'status':
                return self.status(db, request.get('run'))
            operation = protocol.identifier(request['operation'])
            digest = protocol.fingerprint(request)
            previous = db.execute('SELECT * FROM operations WHERE id=?', (operation,)).fetchone()
            if previous:
                if previous['request'] != digest:
                    raise ValueError('Operation identity reused for different input')
                if command == 'transfer':
                    self.receive(body, request['size'], request['sha256'])
                return json.loads(previous['receipt'])
            if command == 'commission':
                state = self.commission(db, request)
                result = state['assignment']
            else:
                state = self.run_state(db, request['run'])
                result = self.advance(state, command, request, body)
            db.execute(
                'INSERT INTO runs VALUES (?,?) ON CONFLICT(number) DO UPDATE SET state=excluded.state',
                (int(state['run'][4:]), json.dumps(state)),
            )
            receipt = {
                'protocol': protocol.VERSION,
                'collection_id': self.collection,
                'run': state['run'],
                'command': command,
                'operation': operation,
                'request_sha256': digest,
                'recorded_at': files.now(),
                'result': result,
            }
            db.execute('INSERT INTO operations VALUES (?,?,?)', (operation, digest, json.dumps(receipt)))
            return receipt

    @staticmethod
    def run_state(db, run):
        if not evidence.RUN.fullmatch(run):
            raise ValueError('Invalid run')
        row = db.execute('SELECT state FROM runs WHERE number=?', (int(run[4:]),)).fetchone()
        if row is None or json.loads(row['state'])['run'] != run:
            raise ValueError('Unknown run')
        return json.loads(row['state'])

    def status(self, db, run):
        if not run:
            runs = [json.loads(row[0]) for row in db.execute('SELECT state FROM runs ORDER BY number')]
            result = [{'run': s['run'], 'connection': s['connection'], 'facts': list(s['facts'])} for s in runs]
        else:
            state = self.run_state(db, run)
            facts = state['facts']
            result = dict(
                run=run,
                connection=state['connection'],
                facts=list(facts),
                capture_limits=facts.get('capture', {}).get('limitations'),
                exposures=len(state.get('exposures', [])),
                observed=[row['path'] for row in facts.get('capture', {}).get('members', [])],
                transferred=state['transferred'],
                accepted=facts.get('accept'),
                preserved=facts.get('preserve'),
                published=facts.get('publish'),
            )
        return {'protocol': protocol.VERSION, 'command': 'status', 'collection_id': self.collection, 'result': result}

    def commission(self, db, request):
        connection = config.identity(request['connection'], 'connection')
        definition_path = self.root / 'connections' / connection / 'connection.json'
        definition = files.read(definition_path)
        schemas.validate('berman.connection/1', definition, definition_path)
        if definition['id'] != connection:
            raise ValueError('Connection identity mismatch')
        harness = request['harness']
        if harness not in ('codex', 'claude'):
            raise ValueError('Unknown native harness')
        selected = request.get('recipe') or definition.get('default_recipe')
        plan_decision = None
        plan_id = request.get('plan')
        if plan_id is not None:
            if request.get('recipe'):
                raise ValueError('Choose either a recipe or a plan')
            plan_id = config.identity(plan_id, 'plan')
            plan = config.Plan(
                SimpleNamespace(root=self.root, path=lambda p: self.root / p), f'plans/{plan_id}/plan.json'
            )
            if plan.id != plan_id or plan.connection != connection:
                raise ValueError('Selected plan belongs to a different connection or identity')
            rows = []
            for row in db.execute('SELECT state FROM runs ORDER BY number'):
                prior = json.loads(row[0])
                claim = prior['decision'].get('plan')
                if claim and claim['id'] == plan_id and prior['connection'] == connection:
                    if 'launch' not in prior['facts'] or not prior['facts']['launch'].get('started'):
                        raise ValueError(f'Existing commission {prior["run"]} has an unresolved plan step')
                    rows.append(
                        {
                            'run': prior['run'],
                            'connection': connection,
                            'started': True,
                            'record': {'plan': claim, 'step': claim['step']},
                        }
                    )
            step = plan.next_step(rows)
            selected = step['recipe']
            plan_decision = plan.reservation(step)
        if selected is None:
            raise ValueError('Choose a recipe or a research plan for this connection')
        store = SimpleNamespace(root=self.root)
        source = recipes.recipe(store, selected, SimpleNamespace(id=connection))
        floor = files.read(self.root / 'collection.json').get('reserved_through', 0)
        imported = (p.name for p in (self.root / 'custody').glob('run-*'))
        floor = max([floor, *(int(run[4:]) for run in imported if evidence.RUN.fullmatch(run))])
        number = max(db.execute('SELECT COALESCE(MAX(number),0) FROM runs').fetchone()[0], floor) + 1
        if not 1 <= number <= 999999:
            raise ValueError('Run number outside supported range')
        run = f'run-{number:03d}'
        checks = [
            {k: row[k] for k in ('id', 'argv', 'timeout', 'stdout_contains') if k in row}
            for row in definition.get('checks', [])
        ]
        assignment = dict(
            schema='berman.assignment/1',
            collection_id=self.collection,
            run=run,
            operation=request['operation'],
            connection=connection,
            harness=harness,
            documents=definition.get('documents', []),
            checks=checks,
            instructions=source['instructions'],
        )
        return dict(
            collection_id=self.collection,
            run=run,
            connection=connection,
            assignment=assignment,
            decision={
                'recipe': selected,
                'plan': plan_decision,
                'recipe_identity': protocol.fingerprint(source),
                'definition_identity': protocol.fingerprint(definition),
                'at': files.now(),
            },
            facts={},
            transferred={},
        )

    def receive(self, stream, size, digest):
        if type(size) is not int or not 0 <= size <= protocol.FILE_LIMIT or not evidence.SHA.fullmatch(digest):
            raise ValueError('Invalid transfer size or digest')
        objects = self.store / 'objects'
        objects.mkdir(exist_ok=True)
        final = objects / digest
        with tempfile.NamedTemporaryFile(dir=objects, prefix='.incoming-') as stage:
            h = hashlib.sha256()
            remaining = size
            while remaining:
                chunk = stream.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise ValueError('Transfer interrupted before declared size')
                stage.write(chunk)
                h.update(chunk)
                remaining -= len(chunk)
            if stream.read(1) or h.hexdigest() != digest:
                raise ValueError('Transfer length or SHA-256 mismatch')
            stage.flush()
            os.fsync(stage.fileno())
            try:
                os.link(stage.name, final)
            except FileExistsError:
                if protocol.hash_file(final) != digest:
                    raise ValueError('Retained object failed hash verification') from None
            directory = os.open(objects, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)

    def advance(self, state, command, request, body):
        facts = state['facts']
        transferred = state['transferred']
        if command in facts:
            raise ValueError(f'{command} already recorded; replay its original operation')
        if command in ('preparation', 'launch', 'capture'):
            self.observe(state, request)
            result = {'observed': command}
        elif command == 'ending':
            if not isinstance(request.get('reason'), str) or not request['reason'].strip():
                raise ValueError('A researcher ending requires a reason')
            result = {'reason': request['reason'], 'at': files.now(), 'operation': request['operation']}
        elif command in ('transfer', 'accept'):
            if 'capture' not in facts:
                raise ValueError('Record the observed inventory before transfer')
            members = protocol.inventory(facts['capture']['members'])
            if command == 'transfer':
                name = request['path']
                if name not in members or members[name] != (request['size'], request['sha256']):
                    raise ValueError('Transfer differs from observed inventory')
                self.receive(body, *members[name])
                transferred[name] = request['sha256']
                return {'transferred': name, 'sha256': transferred[name]}
            for name, (size, digest) in members.items():
                path = self.store / 'objects' / digest
                if transferred.get(name) != digest or path.stat().st_size != size or protocol.hash_file(path) != digest:
                    raise ValueError(f'Unverified or missing transfer: {name}')
            result = {
                'accepted_members': len(members),
                'inventory_sha256': protocol.fingerprint(facts['capture']['members']),
                'at': files.now(),
            }
        elif command == 'exposure':
            if not isinstance(request.get('account'), str) or not request['account'].strip():
                raise ValueError('Record what became visible and how it was handled')
            item = {'at': files.now(), 'account': request['account'], 'operation': request['operation']}
            state.setdefault('exposures', []).append(item)
            return item
        elif command == 'preserve':
            if 'accept' not in facts:
                raise ValueError('Preservation requires accepted evidence')
            members = {name: self.store / 'objects' / digest for name, digest in transferred.items()}
            result = custody.preserve_split_run(self.root, state, members)
        elif command == 'publish':
            if 'preserve' not in facts:
                raise ValueError('Publication requires a preserved recording')
            result = custody.publish_edition(self.root, facts['preserve'])
        else:
            raise ValueError(f'Unknown command: {command}')
        if command not in ('preparation', 'launch', 'capture'):
            facts[command] = result
        return result

    def observe(self, state, request):
        kind = request['command']
        facts = state['facts']
        if kind == 'launch' and 'preparation' not in facts:
            raise ValueError('A launch needs its preparation receipt')
        if kind == 'capture' and 'ending' not in facts:
            raise ValueError('Closeout collection needs an explicit ending')
        observation = request['observation']
        assignment = state['assignment']
        for key in ('run', 'collection_id', 'operation', 'connection', 'harness'):
            if observation.get(key) != assignment[key]:
                raise ValueError(f'Native receipt {key} mismatch')
        if observation.get('assignment_sha256') != files.digest_json(assignment):
            raise ValueError('Native receipt names different assignment bytes')
        schema = 'collection' if kind == 'capture' else kind
        if observation.get('schema') != f'berman.{schema}/1':
            raise ValueError('Unknown native receipt schema')
        if kind == 'capture':
            if observation.get('ending_id') != facts['ending']['operation']:
                raise ValueError('Native collection names a different ending')
            limitations = observation.get('limitations')
            if not isinstance(limitations, list) or not all(isinstance(item, str) for item in limitations):
                raise ValueError('Native collection requires explicit coverage limitations')
            protocol.inventory(observation['members'])
        facts[kind] = observation


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--study', required=True)
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        study = config.identity(args.study, 'study')
        line = sys.stdin.buffer.readline(protocol.HEADER_LIMIT + 1)
        if len(line) > protocol.HEADER_LIMIT or not line.endswith(b'\n'):
            raise ValueError('Invalid command header')
        request = json.loads(line)
        print(json.dumps(Controller(Path.home() / 'research' / study).execute(request, sys.stdin.buffer)))
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
        print(f'Controller: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
