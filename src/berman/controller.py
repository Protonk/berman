"""One SSH invocation processes one v1 command against a guest-owned study.

The ledger holds one state per run. Its first collection lives in `facts` and `transferred`.
Each later supplement has its own declaration, facts and transfer map under `supplements`, and
is addressed by the request's `supplement` number. Host receipts for later sittings are kept in
`sittings`. Nothing here reads a host path.
"""

import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from uuid import uuid4

from berman import custody, evidence, files, protocol, schemas, study


class Controller:
    def __init__(self, root):
        self.root = root.resolve(strict=True)
        self.collection = evidence.collection(self.root)
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
                state = self.run_state(db, request['run'])
                if 'preserve' not in state['facts']:
                    raise ValueError('No preserved recording for this run')
                result = custody.verify_occurrence(self.root, state['run'], state['facts'])
                later = [
                    dict(
                        sequence=item['sequence'],
                        **custody.verify_occurrence(self.root, state['run'], item['facts'], item['sequence']),
                    )
                    for item in state.get('supplements', [])
                    if 'preserve' in item['facts']
                ]
                if later:
                    result['supplements'] = later
                return dict(protocol=protocol.VERSION, collection_id=self.collection, command=command, result=result)
            if command in ('assignment', 'closeout'):
                state = self.run_state(db, request['run'])
                value = state['assignment']
                if command == 'closeout':
                    _, _, anchor = self.part(state, request.get('supplement'))
                    if not anchor:
                        raise ValueError('The engagement has no ending declaration')
                    value = dict(collection_id=self.collection, run=state['run'], operation=anchor['operation'])
                return dict(protocol=protocol.VERSION, collection_id=self.collection, command=command, result=value)
            if command == 'status':
                return self.status(db, request.get('run'), request.get('supplement'), request.get('deep') is True)
            operation = protocol.identifier(request['operation'])
            digest = protocol.fingerprint(request)
            previous = db.execute('SELECT * FROM operations WHERE id=?', (operation,)).fetchone()
            if previous:
                if previous['request'] != digest:
                    raise ValueError('Operation identity reused for different input')
                if command == 'transfer':
                    # A replayed transfer also restores an object the store has since lost or
                    # damaged. The receipt stays the original; the repair is its own ledger entry.
                    outcome = self.receive(body, request['size'], request['sha256'])
                    if outcome != 'present':
                        state = self.run_state(db, request['run'])
                        state.setdefault('repairs', []).append(
                            {
                                'at': files.now(),
                                'path': request['path'],
                                'sha256': request['sha256'],
                                'outcome': outcome,
                            }
                        )
                        self.save(db, state)
                return json.loads(previous['receipt'])
            if command == 'commission':
                state = self.commission(db, request)
                result = state['assignment']
            else:
                state = self.run_state(db, request['run'])
                result = self.advance(state, command, request, body)
            self.save(db, state)
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
    def save(db, state):
        db.execute(
            'INSERT INTO runs VALUES (?,?) ON CONFLICT(number) DO UPDATE SET state=excluded.state',
            (int(state['run'][4:]), json.dumps(state)),
        )

    @staticmethod
    def part(state, number):
        """The facts, transfer map and declaration of the first collection, or of one supplement."""
        if number is None:
            return state['facts'], state['transferred'], state['facts'].get('ending')
        items = state.get('supplements', [])
        if type(number) is not int or not 1 <= number <= len(items):
            raise ValueError('Unknown supplement')
        item = items[number - 1]
        return item['facts'], item['transferred'], item['declaration']

    def object_problem(self, transferred, name, size, digest):
        """Why an observed member is not verified guest bytes, or None when it is."""
        if transferred.get(name) != digest:
            return 'never transferred'
        path = self.store / 'objects' / digest
        if not path.is_file():
            return 'transferred, then absent from the guest store'
        if path.stat().st_size != size or protocol.hash_file(path) != digest:
            return 'transferred, then damaged in the guest store'
        return None

    def outstanding(self, facts, transferred, deep=False):
        """Members the host should send again, and how they were checked.

        A member the acceptance already records as missing is not outstanding. The cheap check
        compares lengths; `deep` rereads every object and catches damage of the same length.
        """
        waived = {row['path'] for row in facts.get('accept', {}).get('missing', [])}
        result = []
        for row in facts.get('capture', {}).get('members', []):
            if row['path'] in waived:
                continue
            path = self.store / 'objects' / row['sha256']
            if deep:
                wrong = self.object_problem(transferred, row['path'], row['size'], row['sha256'])
            else:
                wrong = (
                    transferred.get(row['path']) != row['sha256']
                    or not path.is_file()
                    or path.stat().st_size != row['size']
                )
            if wrong:
                result.append(row['path'])
        return result

    @staticmethod
    def run_state(db, run):
        if not evidence.RUN.fullmatch(run):
            raise ValueError('Invalid run')
        row = db.execute('SELECT state FROM runs WHERE number=?', (int(run[4:]),)).fetchone()
        if row is None or json.loads(row['state'])['run'] != run:
            raise ValueError('Unknown run')
        return json.loads(row['state'])

    @staticmethod
    def sitting_count(state):
        """Sittings the host has reported: the launch, when recorded, and each later one."""
        return ('launch' in state['facts']) + len(state.get('sittings', {}))

    def status(self, db, run, supplement=None, deep=False):
        if not run:
            runs = [json.loads(row[0]) for row in db.execute('SELECT state FROM runs ORDER BY number')]
            result = [
                {
                    'run': s['run'],
                    'connection': s['connection'],
                    'harness': s['assignment']['harness'],
                    'facts': list(s['facts']),
                    'sittings': self.sitting_count(s),
                    'supplements': [
                        {'sequence': item['sequence'], 'facts': list(item['facts'])}
                        for item in s.get('supplements', [])
                    ],
                }
                for s in runs
            ]
        else:
            state = self.run_state(db, run)
            facts, transferred, _ = self.part(state, supplement)
            members = facts.get('capture', {}).get('members', [])
            result = dict(
                run=run,
                connection=state['connection'],
                facts=list(facts),
                capture_limits=facts.get('capture', {}).get('limitations'),
                omitted=facts.get('capture', {}).get('omitted', []),
                exposures=len(state.get('exposures', [])),
                observed=[row['path'] for row in members],
                transferred=transferred,
                outstanding=self.outstanding(facts, transferred, deep),
                outstanding_check='sha256' if deep else 'length',
                accepted=facts.get('accept'),
                preserved=facts.get('preserve'),
                published=facts.get('publish'),
                sittings=self.sitting_count(state),
                supplements=[
                    {
                        'sequence': item['sequence'],
                        'reason': item['declaration']['reason'],
                        'facts': list(item['facts']),
                    }
                    for item in state.get('supplements', [])
                ],
                repairs=state.get('repairs', []),
            )
            if supplement is not None:
                result['supplement'] = supplement
        return {'protocol': protocol.VERSION, 'command': 'status', 'collection_id': self.collection, 'result': result}

    def commission(self, db, request):
        connection = study.identity(request['connection'], 'connection')
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
            plan_id = study.identity(plan_id, 'plan')
            plan = study.Plan(self.root, f'plans/{plan_id}/plan.json')
            if plan.id != plan_id or plan.connection != connection:
                raise ValueError('Selected plan belongs to a different connection or identity')
            started_steps = []
            for row in db.execute('SELECT state FROM runs ORDER BY number'):
                prior = json.loads(row[0])
                claim = prior['decision'].get('plan')
                if claim and claim['id'] == plan_id and prior['connection'] == connection:
                    if 'launch' not in prior['facts'] or not prior['facts']['launch'].get('started'):
                        raise ValueError(f'Existing commission {prior["run"]} has an unresolved plan step')
                    started_steps.append(claim['step'])
            step = plan.next_step(started_steps)
            selected = step['recipe']
            plan_decision = plan.reservation(step)
        if selected is None:
            raise ValueError('Choose a recipe or a plan for this connection')
        source = study.recipe(self.root, selected, connection)
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
            outcome = 'stored'
            try:
                os.link(stage.name, final)
            except FileExistsError:
                if protocol.hash_file(final) == digest:
                    outcome = 'present'
                else:
                    # Keep the damaged bytes for inspection under a name no command reads.
                    os.replace(final, objects / f'.damaged-{digest}-{uuid4().hex}')
                    os.link(stage.name, final)
                    outcome = 'replaced damaged bytes'
            directory = os.open(objects, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            return outcome

    def advance(self, state, command, request, body):
        number = request.get('supplement')
        facts, transferred, anchor = self.part(state, number)
        if command == 'sitting':
            # A later sitting of the same engagement: another native process, the same commission.
            count = request.get('number')
            if type(count) is not int or count < 2:
                raise ValueError('A later sitting is numbered from 2')
            later = state.setdefault('sittings', {})
            if str(count) in later:
                raise ValueError('sitting already recorded; replay its original operation')
            self.check_receipt(state, 'launch', request['observation'])
            if request['observation'].get('sitting') != count:
                raise ValueError('Native receipt names another sitting')
            later[str(count)] = request['observation']
            return {'observed': 'sitting', 'number': count}
        if command == 'supplement':
            # Later activity after a frozen collection. It adds a collection; it rewrites nothing.
            if 'capture' not in state['facts']:
                raise ValueError('A supplement follows the first frozen collection')
            if not isinstance(request.get('reason'), str) or not request['reason'].strip():
                raise ValueError('A supplement requires a reason')
            items = state.setdefault('supplements', [])
            declaration = {
                'sequence': len(items) + 1,
                'reason': request['reason'],
                'at': files.now(),
                'operation': request['operation'],
            }
            items.append(
                {'sequence': declaration['sequence'], 'declaration': declaration, 'facts': {}, 'transferred': {}}
            )
            return declaration
        if command == 'exposure':
            if not isinstance(request.get('account'), str) or not request['account'].strip():
                raise ValueError('Record what became visible and how it was handled')
            item = {'at': files.now(), 'account': request['account'], 'operation': request['operation']}
            state.setdefault('exposures', []).append(item)
            return item
        if command in facts:
            raise ValueError(f'{command} already recorded; replay its original operation')
        if command in ('preparation', 'launch', 'ending') and number is not None:
            raise ValueError(f'{command} belongs to the engagement, not to a supplement')
        if command in ('preparation', 'launch'):
            if command == 'launch' and 'preparation' not in facts:
                raise ValueError('A launch needs its preparation receipt')
            self.check_receipt(state, command, request['observation'])
            facts[command] = request['observation']
            return {'observed': command}
        if command == 'capture':
            if not anchor:
                raise ValueError('Closeout collection needs an explicit ending')
            observation = request['observation']
            self.check_receipt(state, 'collection', observation)
            if observation.get('ending_id') != state['facts']['ending']['operation']:
                raise ValueError('Native collection names a different ending')
            if number is None:
                if 'supplement_id' in observation:
                    raise ValueError('A supplement collection needs its supplement number')
            elif observation.get('supplement_id') != anchor['operation'] or observation.get('sequence') != number:
                raise ValueError('Native collection names a different supplement')
            limitations = observation.get('limitations')
            if not isinstance(limitations, list) or not all(isinstance(item, str) for item in limitations):
                raise ValueError('Native collection requires explicit coverage limitations')
            protocol.inventory(observation['members'])
            facts['capture'] = observation
            return {'observed': command}
        if command == 'ending':
            if not isinstance(request.get('reason'), str) or not request['reason'].strip():
                raise ValueError('An ending requires a reason')
            result = {'reason': request['reason'], 'at': files.now(), 'operation': request['operation']}
        elif command in ('transfer', 'accept'):
            if 'capture' not in facts:
                raise ValueError('Record the observed inventory before transfer')
            members = protocol.inventory(facts['capture']['members'])
            if command == 'transfer':
                name = request['path']
                if name not in members or members[name] != (request['size'], request['sha256']):
                    raise ValueError('Transfer differs from observed inventory')
                if 'accept' in facts:
                    # Acceptance fixed what this collection holds. Only a repeat of an earlier
                    # transfer, which repairs the store, is taken after it.
                    raise ValueError(
                        'Acceptance is already recorded for this collection. Evidence that arrives later belongs in a supplement.'
                    )
                self.receive(body, *members[name])
                transferred[name] = request['sha256']
                return {'transferred': name, 'sha256': transferred[name]}
            # Acceptance is the guest's own reading of its bytes. A member that never arrived, or
            # that the store has lost, is named. The commissioner may accept the rest with a reason.
            missing = []
            for name, (size, digest) in members.items():
                problem = self.object_problem(transferred, name, size, digest)
                if problem:
                    missing.append({'path': name, 'size': size, 'sha256': digest, 'problem': problem})
            reason = request.get('missing_reason')
            if reason is not None and (not isinstance(reason, str) or not reason.strip()):
                raise ValueError('Accepting incomplete evidence requires a reason')
            if missing and reason is None:
                names = ', '.join(f'{row["path"]} ({row["problem"]})' for row in missing[:5])
                more = f' and {len(missing) - 5} more' if len(missing) > 5 else ''
                raise ValueError(
                    f'Unverified or missing transfer: {names}{more}. Transfer again, '
                    'or accept what arrived by giving a reason for the missing evidence'
                )
            if reason is not None and not missing:
                raise ValueError('Every observed member is verified; accept without a missing-evidence reason')
            result = {
                'accepted_members': len(members) - len(missing),
                'inventory_sha256': protocol.fingerprint(facts['capture']['members']),
                'at': files.now(),
            }
            if missing:
                result.update(missing=missing, missing_reason=reason)
        elif command == 'preserve':
            if 'accept' not in facts:
                raise ValueError('Preservation requires accepted evidence')
            if number is not None and 'preserve' not in state['facts']:
                raise ValueError('Preserve the first collection before its supplement')
            absent = {row['path'] for row in facts['accept'].get('missing', [])}
            members = protocol.inventory(facts['capture']['members'])
            blobs = {
                name: self.store / 'objects' / digest for name, (_, digest) in members.items() if name not in absent
            }
            result = custody.preserve_recording(self.root, state, blobs, supplement=number)
        elif command == 'publish':
            if 'preserve' not in facts:
                raise ValueError('Publication requires a preserved recording')
            result = custody.publish_edition(self.root, facts['preserve'])
        else:
            raise ValueError(f'Unknown command: {command}')
        facts[command] = result
        return result

    @staticmethod
    def check_receipt(state, schema, observation):
        """A host receipt must name this commission and the exact assignment bytes it acted on."""
        assignment = state['assignment']
        for key in ('run', 'collection_id', 'operation', 'connection', 'harness'):
            if observation.get(key) != assignment[key]:
                raise ValueError(f'Native receipt {key} mismatch')
        if observation.get('assignment_sha256') != files.digest_json(assignment):
            raise ValueError('Native receipt names different assignment bytes')
        if observation.get('schema') != f'berman.{schema}/1':
            raise ValueError('Unknown native receipt schema')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--study', required=True)
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        name = study.identity(args.study, 'study')
        line = sys.stdin.buffer.readline(protocol.HEADER_LIMIT + 1)
        if len(line) > protocol.HEADER_LIMIT or not line.endswith(b'\n'):
            raise ValueError('Invalid command header')
        request = json.loads(line)
        print(json.dumps(Controller(Path.home() / 'research' / name).execute(request, sys.stdin.buffer)))
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
        print(f'Controller: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
