"""Number-free preparation, explicit engagement endings, and connection-scoped selection.

Commands select engagements within one connection. An explicit run determines its connection.
Lock order is connection, then affected runs in numeric order, then the short global section for
run allocation and shared inventory updates; slow work never holds the global lock.
"""
import os
from copy import copy
from pathlib import Path
import shlex
import uuid

from berman.capture import adapters
from berman.config import Complete
from berman import workspace
from berman import files
from berman import recovery
from berman.files import Decision, keep, read, write
from berman.installation import command as module_command
from berman.recipes import prepare, recipe, snapshot
from berman.preserve import finish, finishable, preserve
from berman.harnesses.codex import Harnesses
from berman.lifecycle import (BEFORE_SESSION, CANCELLATION, SESSION_MATERIAL, authorized, declared, describe,
                              open_engagement, outstanding, progress_state, unused)
from berman.records import checked_request


class Workflow:
    def __init__(self, store, harness=None):
        self.store = store
        self.harness = harness or Harnesses(store)
        self.touched = set()
        self.request_path = None

    def progress(self):
        """Report durable effects even when a later part of the command failed."""
        result = []
        for run in sorted(self.touched):
            record = self.store.record(run) or {}
            state = progress_state(record, (self.store.root / 'runs' / run).exists())
            result.append({'run': run, 'state': state, 'package': record.get('package'),
                           'record': str(self.store.folder(run) / 'operation.json')})
        return result

    def rows(self):
        return self.store.discover()

    def resolve(self, connection=None, run=None):
        """An explicit run determines its connection; a conflicting connection argument fails."""
        if run:
            rows = self.rows()
            if run in self.store.support_trials:
                trial = Path(self.store.support_trials[run]['record']['support_trial']).parent
                raise Decision(f'{run} is a native support trial, not a research engagement; use berman support-trial '
                               f'collect or cleanup --evidence {trial.as_posix()}')
            row = next((r for r in rows if r['run'] == run), None)
            if row is None:
                raise Decision(f'{run} is not a recorded run in this workspace')
            if connection and row.get('connection') != connection:
                raise Decision(f'{run} belongs to connection {row.get("connection")}, not {connection}')
            connection = row.get('connection')
        return self.store.connection(connection)

    def within(self, rows, connection):
        scoped = [r for r in rows if r.get('connection') == connection.id]
        self.store.require(rows, connection.id)
        return scoped

    def synchronize(self, rows):
        for row in rows:
            record = row['record']
            if record and row['binding'] and not record.get('started_session'):
                record['started_session'] = row['binding']['session_id']
                if record['phase'] in BEFORE_SESSION:
                    record['phase'] = 'active'
                self.store.save(record)

    def requests(self):
        values = []
        for path in sorted((self.store.operations / 'new-requests').glob('*.json')):
            try:
                values.append((path, checked_request(read(path))))
            except ValueError as exc:
                raise Decision(f'Invalid new-run request: {path}: {exc}') from exc
        return values

    def save_request(self, request):
        """Write the combined request this command is working through; an invalid one is never written."""
        write(self.request_path, checked_request(request))

    def cancelled_targets(self):
        return {r['result_run']: r for _, r in self.requests() if r['state'] == 'cancelled'}

    def plan_status(self, connection, rows):
        """Describe optional coordination; reading a broken plan never hides other work."""
        result = {'plan': None, 'next_recipe': None, 'next_note': None, 'plan_error': None}
        try:
            plan = connection.plan
            if plan is None:
                result['next_note'] = 'Choose a recipe with berman new --recipe NAME'
            else:
                result['plan'] = plan.id
                result['next_recipe'] = plan.next_step(rows)['recipe']
        except Complete as exc:
            result['next_note'] = str(exc)
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            result.update(next_note=str(exc), plan_error=str(exc))
        return result

    def status(self):
        from berman import support  # Imported here: berman_support uses berman_verify, which imports this module.
        rows = self.rows()
        held = self.store.occupied()
        cancelled = self.cancelled_targets()
        connections = {}
        for conn in self.store.connections.values():
            planned = self.plan_status(conn, rows)
            pending = [str(p) for p, r in self.requests() if r['state'] == 'pending'
                       and r.get('connection', self.store.legacy_connection) == conn.id]
            connections[conn.id] = {'name': conn.name, **planned,
                                    'capture': conn.capture, 'checkout': str(conn.checkout),
                                    'pending_requests': pending,
                                    'blocked_by': [p for r in rows if r.get('connection') == conn.id for p in r['problems']],
                                    'operation_in_progress': f'connection {conn.id}' in held}
        result = []
        for row in filter(outstanding, rows):
            record, lifecycle = row['record'] or {}, row['lifecycle']
            capture_status = row['capture_status'] or {}
            result.append({'run': row['run'], 'connection': row.get('connection'), 'state': row['state'],
                           'recipe': ((record.get('recipe') or {}).get('metadata') or {}).get('id'),
                           'assignment_origin': record.get('assignment_origin'),
                           'phase': record.get('phase'), 'session': (row['binding'] or {}).get('session_id'),
                           'ending_declared': lifecycle['ending']['value'] is True,
                           'started': row['started'],
                           'prepared_unstarted': row['started'] is False,
                           'record': str(self.store.folder(row['run'])) if record else None,
                           'capture_consistent': capture_status.get('snapshot_consistent'),
                           'last_capture': (capture_status.get('capture') or {}).get('captured_at_utc'),
                           'warnings': capture_status.get('warnings', []) +
                                       (['Combined request cancelled; target retirement pending']
                                        if row['run'] in cancelled and unused(row) else []),
                           'problems': row['problems'],
                           'operation_in_progress': row['run'] in held,
                           'summary': describe(lifecycle, record.get('phase') or row['state']),
                           'unknown': lifecycle['unknown'], 'lifecycle': lifecycle})
        default = connections.get(self.store.default_connection, {})
        return {'runs': result, 'archived': sum(r['location'] == 'archived' for r in rows),
                'workspace_problems': self.store.discovery_problems,
                'default_connection': self.store.default_connection, 'connections': connections,
                'next_recipe': default.get('next_recipe'), 'operations_in_progress': held,
                'support_trials': support.status(self.store),
                'meaning': 'Session activity and declared engagement endings are distinct. No files were changed.'}

    def select(self, rows, run=None, prepared=False, command=None):
        choices = [r for r in rows if (unused(r) if prepared else open_engagement(r))]
        if run:
            choices = [r for r in choices if r['run'] == run]
        if len(choices) != 1:
            names = ', '.join(r['run'] for r in choices) or 'none'
            command = command or ('launch' if prepared else 'finish')
            suffix = ' --reason "description"' if command in ('finish', 'cancel', 'supplement') else ''
            help_text = f'Choose with berman {command} --run RUN{suffix}' if choices else 'Use berman status to inspect the recorded situation'
            raise Decision(f'Expected one {"prepared packet" if prepared else "open engagement"}; found {names}. {help_text}')
        return choices[0]

    def finish(self, reason, run=None, allow_incomplete=None, connection=None):
        conn = self.resolve(connection, run)
        with self.store.lock('connection', conn.id):
            scoped = self.within(self.rows(), conn)
            self.synchronize(scoped)
            # A named run that has already left the packet root is resumed or answered even while
            # another engagement is open: retrying an interrupted finish is the same command.
            if not any(open_engagement(r) and (not run or r['run'] == run) for r in scoped):
                pending = [r for r in scoped if declared(r['record']) and r['record'].get('phase') == 'closing'
                           and (not run or r['run'] == run)]
                if len(pending) == 1:
                    with self.store.lock('run', pending[0]['run']):
                        return preserve(self.store, pending[0]['record'])
                completed = [r for r in scoped if r['lifecycle']['packaged']['value'] and r['record'].get('phase') == 'archived'
                             and (not run or r['run'] == run)]
                if completed:
                    last = completed[-1]['record']
                    return {'run': last['run'], 'state': 'already-archived', 'package': last['package']}
            row = self.select(scoped, run)
            self.touched.add(row['run'])
            with self.store.lock('run', row['run']):
                return finish(self.store, row, reason, allow_incomplete)

    def supplement(self, reason, run=None, connection=None):
        conn = self.resolve(connection, run)
        with self.store.lock('connection', conn.id):
            row = self.select(self.within(self.rows(), conn), run, command='supplement')
            self.touched.add(row['run'])
            with self.store.lock('run', row['run']):
                return self.store.collector_for(row['record']).command('supplement', row['run'], reason=reason)

    def settle(self, rows):
        results = []
        for row in rows:
            record, action = row['record'], authorized(row)
            if action is None:
                continue
            with self.store.lock('run', row['run']):
                if action == 'retire':
                    self.retire(record, record['cancellation']['reason'])
                elif action == 'preserve':
                    self.touched.add(row['run'])
                    results.append(preserve(self.store, record))
                else:
                    # Resume an already initiated migration; finalization alone is not an ending.
                    connection = self.store.connection(row['connection'])
                    record = record or {'schema': 'berman.operation/1', 'run': row['run'],
                                        'project': str(connection.checkout), 'documents': connection.documents()}
                    record.update(phase='closing', started_session=(row['binding'] or {}).get('session_id'))
                    record['migration_authority'] = {'journal': str(self.store.root / 'migrations' / (row['run'] + '.json'))}
                    self.store.save(record)
                    self.touched.add(row['run'])
                    results.append(preserve(self.store, record))
        return results

    def retire(self, record, reason):
        """Keep an unused preparation, including failed checks; never discard a session."""
        run = record['run']
        packet = self.store.field / run
        destination = self.store.folder(run) / 'unused-packet'
        collector = self.store.collector_for(record)
        if any((base / name).exists() for base in (packet, destination) for name in SESSION_MATERIAL):
            raise Decision(f'{run} has session material; it cannot be retired as an unused preparation')
        if not record.get('cancellation'):
            record['cancellation'] = {'at_utc': files.now(), 'reason': reason}
        record['phase'] = 'cancelling'
        self.store.save(record)
        if packet.exists():
            if destination.exists():
                raise Decision('Both unused-preparation locations exist; neither was overwritten')
            if 'unused_inventory' not in record:
                # Hash outside the callback lock; recheck metadata briefly under it.
                record['unused_inventory'] = files.inventory(packet)
                self.store.save(record)
            with collector.locked():
                if any((packet / name).exists() for name in SESSION_MATERIAL):
                    raise Decision(f'{run} has session material; it cannot be retired as an unused preparation')
                if not files.metadata_matches(packet, record['unused_inventory']):
                    raise Decision('Unused packet changed during retirement')
                packet.rename(destination)
        if destination.exists() and files.inventory(destination) != record['unused_inventory']:
            raise Decision('Retired preparation differs from its inventory')
        record['phase'] = 'cancelled'
        self.store.save(record)

    def cancel(self, reason, run=None, connection=None):
        conn = self.resolve(connection, run)
        with self.store.lock('connection', conn.id):
            row = self.select(self.within(self.rows(), conn), run, prepared=True, command='cancel')
            if not row['record'] or not reason.strip():
                raise Decision('Retiring a preparation needs its operation record and a reason')
            with self.store.lock('run', row['run']):
                requests = [(p, r) for p, r in self.requests()
                            if r.get('connection', self.store.legacy_connection) == conn.id and r['state'] != 'complete']
                for path, request in requests:
                    if request['schema'] == 'berman.new-request/1':
                        raise Decision(f'Pending legacy request at {path}; settle it with the original installation before adoption')
                    if request['result_run'] == row['run']:
                        if request['state'] == 'cancelled':
                            reason = request['cancellation']['reason']
                        else:
                            request.update(state='cancelled', cancellation={'reason': reason, 'at_utc': files.now()})
                            write(path, checked_request(request))
                self.retire(row['record'], reason)
            return {'run': row['run'], 'state': 'cancelled', 'preserved_at': str(self.store.folder(row['run'])),
                    'meaning': 'Unused preparation retained; instruction sequence not advanced.'}

    def request(self, rows, finish_previous, connection):
        """Recover the target of a combined request, even after it has moved to the archive."""
        requests = [(p, r) for p, r in self.requests()
                    if r.get('connection', self.store.legacy_connection) == connection.id]
        pending = [(p, r) for p, r in requests if r['state'] == 'pending']
        if len(pending) > 1:
            raise Decision('Several unfinished new-run requests exist; inspect their recorded targets')
        if pending and pending[0][1]['schema'] == 'berman.new-request/1':
            raise Decision(f'Pending legacy request at {pending[0][0]} has no guaranteed reserved assignment; '
                           'settle it with the original installation before adopting this version')
        if not pending and finish_previous:
            available = {r['run'] for r in rows if unused(r)}
            pending = [(p, r) for p, r in requests if r['state'] == 'complete' and r.get('result_run') in available
                       and r['finish_reason'] == finish_previous]
            if len(pending) > 1:
                raise Decision('Several completed requests claim the unused target; inspect their recorded targets')
        if pending:
            path, request = pending[0]
            if finish_previous and request['finish_reason'] != finish_previous:
                raise Decision(f'An earlier combined request is pending at {path}. Run berman new to resume its recorded ending')
            self.request_path = path
            self.touched.add(request['previous_run'])
            if request.get('result_run'):
                self.touched.add(request['result_run'])
            return request
        return None

    def reserve(self, connection, step, selected, plan=None):
        """The short global section: fresh identities and an exact, published assignment."""
        store = self.store
        with store.lock('global', wait=30):
            run = store.allocate(self.rows())
            record = {'schema': 'berman.operation/1', 'run': run, 'phase': 'reserved',
                      'software': workspace.identity(),
                      'reserved_at_utc': files.now(), 'workspace': {'root': str(store.root), 'tag': store.tag},
                      'connection': connection.reservation(), 'recipe': selected,
                      'assignment_origin': 'plan' if plan else 'direct',
                      'recording_protocol': selected['metadata']['recording_protocol'],
                      'project': str(connection.checkout), 'documents': connection.documents(),
                      'packet_root': str(store.field), 'packet': str(store.field / run),
                      'capture': adapters.reservation(connection.capture),
                      'harness': store.harness_identity(connection.capture, run, connection.entry.get('fieldwork_model')),
                      'retained_open_runs': []}
            if plan:
                record.update(plan=plan.reservation(step), step=step['id'])
            recovered = recovery.generated_residue(store, run)
            if recovered:
                record['recovered_writes'] = recovered
            store.save(record)
        return record

    def resume_cancellations(self, connection):
        """Complete only target retirement already authorized by a cancelled request."""
        for _, request in self.requests():
            if request.get('connection') != connection.id or request['state'] != 'cancelled':
                continue
            record = self.store.record(request['result_run'])
            if record is None:
                raise Decision('Cancelled request has no retained target operation: ' + request['result_run'])
            if record.get('connection', {}).get('id') != connection.id:
                raise Decision('Cancelled request and target connection disagree')
            if record['phase'] == 'cancelled':
                continue
            row = next((r for r in self.rows() if r['run'] == request['result_run']), None)
            # A session arriving during cancellation remains a real engagement. Never retire it.
            if row and unused(row):
                self.touched.add(row['run'])
                with self.store.lock('run', row['run']):
                    self.retire(record, request['cancellation']['reason'])

    def new(self, finish_previous=None, keep_open=False, connection=None, recipe_name=None):
        conn = self.store.connection(connection)
        with self.store.lock('connection', conn.id):
            self.touched = set()
            self.request_path = None
            rows = self.rows()
            scoped = self.within(rows, conn)
            request = self.request(scoped, finish_previous, conn)
            if request and keep_open:
                raise Decision('A combined request records an ending; --keep-open cannot resume it')
            # Finish a cancellation before selecting a fresh assignment, never revive its target.
            self.resume_cancellations(conn)
            rows = self.rows()
            scoped = self.within(rows, conn)
            waiting = [r for r in scoped if unused(r)]
            if len(waiting) > 1:
                raise Decision('Several unused preparations exist; use berman cancel --run RUN --reason "description" to retire an unwanted one')
            record = waiting[0]['record'] if waiting else None
            if waiting and not record:
                raise Decision(f'{waiting[0]["run"]} is an unmanaged preparation. Retain or retire that packet before making another')
            if request and (record is None or record['run'] != request['result_run']):
                raise Decision('The combined request has no matching unused reserved target; inspect its recorded runs')
            if record:
                if record.get('phase') == 'launching':
                    raise Decision(f'{record["run"]} has a launch attempt without a session binding. Check that attempt; '
                                   'if it ended before registration, cancel the unused preparation with a reason')
                if recipe_name is not None and recipe_name != record['recipe']['metadata']['id']:
                    raise Decision(f'{record["run"]} retains recipe {record["recipe"]["metadata"]["id"]}; '
                                   'cancel that unused preparation before assigning another recipe')
                if record['project'] != str(conn.checkout):
                    raise Decision('The reserved project differs from current configuration; the run record governs its packet')
                self.store.check_installation(record)
                selected, step, plan = record['recipe'], None, None
            elif recipe_name is not None:
                selected, step, plan = recipe(self.store, recipe_name, conn), None, None
            else:
                plan = conn.plan
                if plan is None:
                    raise Decision(f'No assignment selected for connection {conn.id}; choose a recipe with '
                                   'berman new --recipe NAME or select a study plan')
                step = plan.next_step(rows)
                selected = recipe(self.store, step['recipe'], conn)
            previous = None
            if request:
                previous = next((r for r in scoped if r['run'] == request['previous_run']), None)
                if previous is None:
                    raise Decision('The combined request has no matching previous engagement')
                if declared(previous['record']) and previous['record']['ending']['reason'] != request['finish_reason']:
                    raise Decision('The combined request and recorded ending reason disagree')
            elif finish_previous:
                previous = self.select(scoped)
                finishable(previous, finish_previous)
            opened = [r for r in scoped if open_engagement(r) and authorized(r) is None
                      and (previous is None or r['run'] != previous['run'])]
            if opened and not keep_open:
                names = ', '.join(r['run'] for r in opened)
                advice = ('If it is finished, use berman new --finish-previous "description"; ' if all(r['binding'] for r in opened)
                          else 'It has no session binding, so it cannot be finished yet (berman status shows its capture); ')
                raise Decision(f'{names} has no declared ending. {advice}'
                               'to retain it for continuation while preparing another encounter, use berman new --keep-open')
            # The recorded adapter governs a retry even if future assignments select another one.
            launch_connection = copy(conn)
            if record:
                launch_connection.capture = self.store.adapter(record)
            preflight = self.harness.preflight(launch_connection)
            self.synchronize(scoped)
            if record is None:
                record = self.reserve(conn, step, selected, plan)
            run = record['run']
            self.touched.add(run)
            if finish_previous and request is None:
                request = {'schema': 'berman.new-request/2', 'state': 'pending', 'connection': conn.id,
                           'created_at_utc': files.now(), 'previous_run': previous['run'],
                           'finish_reason': finish_previous, 'result_run': run}
                self.request_path = self.store.operations / 'new-requests' / (uuid.uuid4().hex + '.json')
                self.save_request(request)
            preserved = []
            if request:
                self.touched.add(previous['run'])
                with self.store.lock('run', previous['run']):
                    if previous['location'] == 'archived':
                        previous_record = self.store.record(previous['run'])
                        if not previous_record or not declared(previous_record):
                            raise Decision('The combined request has no matching declared ending in its archived target')
                        if previous_record.get('phase') != 'archived':
                            preserved.append(preserve(self.store, previous_record))
                        else:
                            preserved.append({'run': previous['run'], 'state': 'archived', 'package': previous_record['package']})
                    else:
                        preserved.append(finish(self.store, previous, request['finish_reason']))
                if request['state'] == 'pending':
                    request['previous_preserved'] = preserved[-1]
                    self.save_request(request)
            preserved.extend(self.settle(self.within(self.rows(), conn)))
            scoped = self.within(self.rows(), conn)
            opened = [r for r in scoped if open_engagement(r)]
            with self.store.lock('run', run):
                # Revalidate the published reservation before committing results to it.
                record = self.store.record(run)
                target = next((r for r in scoped if r['run'] == run), None)
                if (target is None or not unused(target) or record.get('phase') in CANCELLATION + ('launching',)
                        or record.get('started_session')):
                    raise Decision(f'{run} changed after its reservation was published; inspect berman status')
                retained = [r['run'] for r in opened]
                if record.get('retained_open_runs') != retained:
                    record.setdefault('retained_open_history', []).append({
                        'at_utc': files.now(), 'previous': record.get('retained_open_runs', []), 'current': retained})
                record['retained_open_runs'] = retained
                self.store.save(record)
                if record['project'] != str(conn.checkout):
                    raise Decision('The reserved project differs from current configuration; the run record governs its packet')
                packet = prepare(self.store, record)
                # If a native session appeared during preparation, do not probe its live notes.
                if any((packet / name).exists() for name in SESSION_MATERIAL):
                    raise Decision(f'{run} started during preparation; inspect its capture before proceeding')
                readiness = self.harness.ensure(run, self.store.folder(run) / 'preparation')
                record = self.store.record(run)
                record.update(phase='ready', readiness=str(self.store.folder(run) / 'preparation/readiness.json'))
                self.store.save(record)
                command = self.command('launch', '--run', run)
                keep(self.store.folder(run) / 'preparation/LAUNCH.txt', (command + '\n').encode())
                if request and request['state'] == 'pending':
                    request['state'] = 'complete'
                    self.save_request(request)
            return {'state': 'ready', 'run': run, 'connection': conn.id, 'recipe': record['recipe']['metadata']['id'],
                    'assignment_origin': record.get('assignment_origin'),
                    'preserved': preserved, 'retained_open_runs': retained,
                    'launch': command, 'packet': str(packet), 'preparation': str(self.store.folder(run) / 'preparation'),
                    'checks_passed': readiness['passed'], 'checks_reused': readiness.get('reused', False),
                    'warnings': (list(preflight.get('warnings', [])) if isinstance(preflight, dict) else []) +
                                ([f'Interrupted writes retained in {self.store.root / ".state/recovery" / run}']
                                 if record.get('recovered_writes') else []),
                    'recovered_writes': record.get('recovered_writes', []),
                    'request': str(self.request_path) if self.request_path else None,
                    'next': 'Launch when wanted. After the first reply, berman status reports whether capture has begun.'}

    def launch(self, run=None, connection=None):
        # Verify and select while locked; release before starting the interactive harness.
        conn = self.resolve(connection, run)
        with self.store.lock('connection', conn.id):
            self.resume_cancellations(conn)
            row = self.select(self.within(self.rows(), conn), run, prepared=True)
            run = row['run']
            if any(r['state'] == 'pending' and r.get('result_run') == run for _, r in self.requests()):
                raise Decision(f'{run} belongs to a pending combined request; resume berman new before launching')
            with self.store.lock('run', run):
                record = self.store.record(run)
                self.store.check_installation(record)
                if not record or record.get('phase') != 'ready':
                    raise Decision('This packet is not ready; run berman new to complete preparation')
                folder = self.store.folder(run) / 'preparation'
                for name in ('ASSIGNMENT.md', 'INSTRUCTIONS.md', 'run.json'):
                    if files.regular(self.store.field / run / name).read_bytes() != files.regular(folder / name).read_bytes():
                        raise Decision(f'{run}: prepared {name} changed; inspect before launch')
                self.harness.ensure(run, folder)
                # A preparation records an observation, not a lock on the shared checkout.
                # Sample launch context after any slow native check, under the same identity.
                context_dir = folder / 'launch-contexts' / uuid.uuid4().hex
                context = snapshot(Path(record['project']), record['documents'], context_dir, require_stable_git=False)
                argv = self.harness.argv(run)
                record['phase'] = 'launching'
                record.setdefault('launch_attempts', []).append({'requested_at_utc': files.now(), 'pid': os.getpid(),
                                                                'context': str(context_dir), 'observed_head': context['head']})
                self.store.save(record)
        if record['harness']['id'] != 'claude':
            os.environ['CODEX_HOME'] = str(self.store.home)
        previous_cwd = os.getcwd()
        try:
            os.chdir(record['project'])
            os.execv(argv[0], argv)
        except OSError as exc:
            with self.store.lock('run', run):
                record['phase'] = 'ready'
                record['launch_attempts'][-1]['exec_error'] = str(exc)
                self.store.save(record)
            raise
        finally:
            os.chdir(previous_cwd)  # exec success never returns; failures and test doubles do.

    def dev(self, harness, args=(), connection=None):
        """Ordinary development: no packet, no fieldwork assignment, no research-plan step.

        The launcher runs in the connection's configured checkout, whatever the caller's directory;
        the launcher itself still refuses any other root.
        """
        conn = self.store.connection(connection)
        launcher = (conn.entry.get('development') or {}).get(harness)
        if not launcher:
            raise Decision(f'No ordinary development launcher is configured for {conn.id} with {harness}. '
                           f'Development needs no fieldwork assignment; configure connections.{conn.id}.development in berman.json')
        path = self.store.path(launcher)
        if not os.access(path, os.X_OK):
            raise Decision(f'Development launcher is unavailable: {path}')
        if not conn.checkout.is_dir():
            raise Decision(f'The configured checkout for {conn.id} is not a directory: {conn.checkout}')
        return {'argv': [str(path), *args], 'cwd': str(conn.checkout), 'connection': conn.id}

    def command(self, *words):
        """Use this installation's interpreter and name the workspace explicitly."""
        return shlex.join(module_command('--root', self.store.root, *words))

    def cheatsheet(self):
        """The commands a person types to start sessions now, per connection; read-only.

        Fieldwork commands follow the recorded situation: a ready packet, an open engagement, or the next
        agreed role. Development commands come from the connection's configured launchers.
        """
        def command(*words):
            return shlex.join(['berman', '--root', str(self.store.root), *words])

        rows = self.rows()
        held = self.store.occupied()
        cancelled = self.cancelled_targets()
        connections = []
        pending_targets = {r['result_run'] for _, r in self.requests() if r['state'] == 'pending' and r.get('result_run')}
        for conn in self.store.connections.values():
            own = [r for r in rows if r.get('connection') == conn.id]
            fieldwork = {'ready': [], 'incomplete': [], 'open': [], 'ending': [], 'attention': []}
            for row in own:
                record, phase = row['record'] or {}, (row['record'] or {}).get('phase')
                if row['problems']:
                    fieldwork['attention'].append({'run': row['run'], 'problem': row['problems'][0]})
                elif row['run'] in cancelled and unused(row):
                    fieldwork['attention'].append({'run': row['run'], 'problem': 'Combined request cancelled; retry retirement'})
                elif unused(row) and phase not in CANCELLATION:
                    fieldwork['ready' if phase == 'ready' and row['run'] not in pending_targets else 'incomplete'].append(row['run'])
                elif open_engagement(row):
                    fieldwork['ending' if declared(record) else 'open'].append(row['run'])
            planned = self.plan_status(conn, rows)
            pending = [str(p) for p, r in self.requests() if r['state'] == 'pending'
                       and r.get('connection', self.store.legacy_connection) == conn.id]
            select = [] if conn.id == self.store.default_connection else ['--connection', conn.id]
            development = []
            launchers = (conn.entry.get('development') or {}).items()
            for harness, launcher in sorted(launchers, key=lambda item: (item[0] not in ('codex', 'claude'), item[0] != 'codex', item[0])):
                path = self.store.path(launcher)
                development.append({'harness': harness, 'launcher': str(path), 'available': os.access(path, os.X_OK),
                                    'command': command('dev', '--harness', harness, *select)})
            several = len(fieldwork['ready']) > 1
            needs_recipe = not planned['next_recipe'] and not (fieldwork['ready'] or fieldwork['incomplete']) and not pending
            choice = ['--recipe', 'RECIPE'] if needs_recipe else []
            commands = {'status': command('status'),
                        'new': command('new', *select),
                        'new_direct': command('new', '--recipe', 'RECIPE', *select),
                        'launch': [command('launch', '--run', run) if several or select else command('launch')
                                   for run in fieldwork['ready']],
                        'finish': [command('finish', '--run', run, '--reason', 'why the engagement ended')
                                   for run in fieldwork['open']],
                        'finish_previous': command('new', '--finish-previous', 'why the engagement ended', *select, *choice),
                        'keep_open': command('new', '--keep-open', *select, *choice),
                        'supplement': [command('supplement', '--run', run, '--reason', 'why this activity is retained')
                                       for run in fieldwork['open']]}
            connections.append({'id': conn.id, 'name': conn.name, 'default': conn.id == self.store.default_connection,
                                'checkout': str(conn.checkout), **planned, 'pending_requests': pending,
                                'operation_in_progress': f'connection {conn.id}' in held,
                                'fieldwork': fieldwork, 'commands': commands, 'development': development})
        return {'connections': connections, 'berman': 'berman',
                'workspace_problems': self.store.discovery_problems,
                'meaning': 'Commands use berman on PATH and select this workspace explicitly. '
                           'Nothing was changed; launching still verifies the packet.'}
