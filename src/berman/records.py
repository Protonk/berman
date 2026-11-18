"""Read-only discovery of a co-located workspace: its runs, what their records establish, what is unsettled.

A co-located workspace is a directory with berman.json, made by release 0.26.3 or earlier, where
the research records and the worker's packets shared one machine account. This version
commissions, captures and closes fieldwork only through the controller and executor
(berman.controller, berman.executor). Here such a workspace is evidence to read: nothing in this
module writes a file, takes a lock or runs a harness. A run that is not at rest is reported as
unsettled and has to be closed with the release that opened it. No ending, package or
completeness is inferred for it.
"""

import os
from pathlib import Path

from berman import evidence as recording_evidence
from berman import files, schemas, snapshots
from berman.config import Catalogue, Complete, Connection
from berman.files import Decision, digest, read, safe_directory
from berman._legacy.api import RUN
from berman.lifecycle import SESSION_MATERIAL, assess, describe, journal_phase, outstanding, unsettled, validate_record

SUPPORT_RANGE = range(900101, 900200)  # Native support trials: run-900101 to run-900199.
SUPPORT_SCHEMA = 'berman.support-trial/1'
SUPPORT_CLOSED = ('cleaned', 'abandoned')
PROBLEMS = (OSError, ValueError, KeyError, TypeError, AttributeError)
READ_ONLY = (
    'A co-located workspace is read-only in this version: fieldwork is commissioned and closed only through '
    'the controller and executor (berman remote).'
)


def awaits(reason, version=None):
    """The plain statement for anything this version found unsettled and will not settle."""
    release = f' (Berman {version})' if version else ''
    return (
        f'{reason}. It has to be closed with the release that opened it{release}; this version does not close '
        'co-located fieldwork.'
    )


def closed_by(root):
    """The files that close a packet's capture, by presence; neither is an engagement ending."""
    return [name for name in ('finalized.json', 'ended.json') if (root / 'session' / name).exists()]


def packet_evidence(root):
    """Presence checks only (no reads, hashes or writes), so they are safe beneath runs/."""
    return {
        'closed_by': closed_by(root),
        'replacements': sorted(str(p.relative_to(root)) for p in root.glob('session-history/*/replacement.json')),
        'session_material': any((root / name).exists() for name in SESSION_MATERIAL),
    }


def capture_metadata(root, run):
    """Read a snapshot's minimum evidence, distinguishing absence from damage.

    A false JSON value is damage, never "no metadata". Reading changes no preserved bytes.
    """
    try:
        path = snapshots.resolve(root / 'session') / 'capture.json'
    except FileNotFoundError as exc:
        raise Decision(f'{run}: missing capture metadata: {exc}') from exc
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise Decision(f'{run}: damaged capture metadata: {exc}') from exc
    if not path.exists() and not path.is_symlink():
        if any(
            (root / 'session' / name).exists()
            for name in ('transcript.jsonl', 'captures.jsonl', 'conversation.txt', 'finalized.json', 'snapshots')
        ):
            raise Decision(f'{run}: missing capture metadata for retained session evidence: {path}')
        return None
    value = read(path)
    if not isinstance(value, dict) or not value:
        raise Decision(f'{run}: damaged capture metadata at {path}: expected a nonempty object')
    for name in ('sha256', 'session_id', 'captured_at_utc', 'event'):
        if not isinstance(value.get(name), str) or not value[name]:
            raise Decision(f'{run}: damaged capture metadata at {path}: {name} must be nonempty text')
    if value.get('run') != run or type(value.get('saved_bytes')) is not int or value['saved_bytes'] < 0:
        raise Decision(f'{run}: damaged capture metadata at {path}: invalid run or saved_bytes')
    return value


def unpublished(folder):
    """Whether an operations folder is only what a reservation left when its record never landed.

    A reservation was published by the atomic write of operation.json. If that write failed, or the
    process stopped inside it, the folder made to hold it stayed behind, empty or with the write's
    temporary file. Nothing else exists for that identity, so it is not a run and discovery passes
    over it. A folder with anything else in it, or one that cannot be listed, is still reported.
    """
    try:
        return (
            folder.is_dir()
            and not folder.is_symlink()
            and all(
                entry.name.startswith('.capture-') and entry.is_file() and not entry.is_symlink()
                for entry in folder.iterdir()
            )
        )
    except OSError:
        return False


def checked(value, run):
    """An operation record unchanged, or ValueError saying why it is not one.

    Lifecycle rules come first and keep their wording; the schema then refuses structure they never
    looked at. Neither changes the record.
    """
    return schemas.validate('berman.operation/1', validate_record(value, run))


class Store:
    """A co-located workspace as its berman.json describes it; loading and discovery only read."""

    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()
        safe_directory(self.root)
        raw = read(self.root / 'berman.json')
        family = raw.get('schema') if isinstance(raw, dict) else None
        if family == 'berman.configuration/1':
            raise ValueError(
                'The single-project berman.configuration/1 format is not read by this version; read '
                'that workspace with the release that wrote it'
            )
        if family != 'berman.workspace/1':
            raise ValueError('Unsupported berman.json schema')
        schemas.validate('berman.workspace/1', raw, self.root / 'berman.json')
        workspace = raw
        self.workspace = workspace
        history = workspace.get('historical_runs', [])
        if not isinstance(history, list) or any(not isinstance(r, str) or not RUN.fullmatch(r) for r in history):
            raise ValueError('historical_runs must list recorded run identities')
        self.historical_runs = set(history)
        self.field = self.path(workspace['packet_root'])
        self.home = self.path(workspace['codex_home'])
        self.operations = self.root / 'operations'
        # Where the workspace's own release kept each adapter's session registrations.
        self.registries = (self.home / 'berman-capture', self.root / '.claude-fieldwork/sessions')
        self.catalogue = Catalogue(self, workspace)
        self.connections = {ident: Connection(self, ident, entry) for ident, entry in workspace['connections'].items()}
        self.default_connection = workspace.get('default_connection')
        self.edition = workspace.get('edition', 'connections')
        if self.edition not in ('connections', 'stewart'):
            raise ValueError(f'Unknown reading edition: {self.edition}')
        self.legacy_connection = workspace.get('legacy_connection')
        for name in (self.default_connection, self.legacy_connection):
            if name is not None and name not in self.connections:
                raise ValueError(f'Configured connection is not registered: {name}')

    def path(self, value):
        p = Path(value).expanduser()
        return Path(os.path.abspath(p if p.is_absolute() else self.root / p))

    def folder(self, run):
        if not RUN.fullmatch(run):
            raise ValueError(f'Invalid run identity: {run}')
        return self.operations / run

    def record(self, run):
        path = self.folder(run) / 'operation.json'
        if not path.exists():
            return None
        try:
            return checked(read(path), run)
        except ValueError as exc:
            raise ValueError(f'Invalid operation record: {path}: {exc}') from exc

    def attribute(self, run, record):
        if run in getattr(self, 'recording_errors', {}):
            return None, 'damaged published recording authority'
        if run in getattr(self, 'published_recordings', {}):
            attribution = self.published_recordings[run][0]['attribution']
            return attribution['connection'], attribution['basis']
        if record and record.get('connection'):
            return record['connection']['id'], 'operation record'
        catalogued = self.catalogue.connection(run)
        if catalogued:
            return catalogued, 'run catalogue'
        for base in (self.field, self.root / 'runs'):
            metadata = base / run / 'run.json'
            try:
                connection = read(metadata).get('connection') if metadata.exists() else None
            except OSError, ValueError, TypeError, AttributeError:
                connection = None
            if isinstance(connection, str) and connection in self.connections:
                return connection, 'packet metadata'
        if self.legacy_connection:
            return self.legacy_connection, 'no connection record; legacy packet conventions'
        return None, 'unattributed'

    def support(self):
        result = {}
        for relative in self.catalogue.support:
            collection = self.path(relative)
            if not collection.is_relative_to(self.root):
                raise ValueError('Support collections must be within Berman')
            for entry in read(collection / 'source-layout.json')['runs']:
                binding = read(collection / entry['current_root'] / 'session/binding.json')
                if binding['run'] != entry['run']:
                    raise Decision('Support inventory and binding disagree')
                result[entry['run']] = binding['session_id']
        return result

    def discover(self):
        """Every identity across connections; per-run problems are reported, not raised."""
        for run in self.historical_runs:
            if (self.field / run).exists() or not (self.root / 'runs' / run).is_dir():
                raise Decision(f'{run}: opaque history must be preserved under runs/ and have no active packet')
        support = self.support()
        self.support_identities = set(support)
        identities = set()
        problems = {}
        self.discovery_problems = []

        def problem(run, path, error):
            identities.add(run)
            problems.setdefault(run, []).append(f'{run}: cannot inspect {path}: {error}')

        for base in (self.field, self.root / 'runs', self.operations):
            if base.exists():
                safe_directory(base)
                for p in base.iterdir():
                    if RUN.fullmatch(p.name):
                        if p.name in self.historical_runs:
                            continue
                        if base == self.operations and unpublished(p):
                            continue
                        identities.add(p.name)
                        try:
                            safe_directory(p)
                        except (OSError, ValueError) as exc:
                            problem(p.name, p, exc)
        layout = self.root / 'source-layout.json'
        indexed = {r['run'] for r in read(layout)['runs']} - self.historical_runs if layout.exists() else set()
        self.published_recordings = {}
        self.recording_errors = {}
        for path in (self.root / 'recordings').glob('run-*/manifest.json'):
            run = path.parent.name
            if run in self.historical_runs:
                continue
            identities.add(run)
            try:
                if not (path.parent / 'publication.json').exists():
                    continue  # Publication never finished; the run is read from its other records.
                manifest = recording_evidence.validate(read(path))
                publication = read(path.parent / 'publication.json')
                if (
                    manifest['run'] != run
                    or manifest['collection_id'] != recording_evidence.collection(self.root)
                    or publication['manifest_sha256'] != recording_evidence.identity(manifest)
                ):
                    raise Decision('Published recording identity disagrees')
                members = {item['path']: item for item in manifest['entries']}

                def retained(name, member_rows=members, folder=path.parent):
                    source = self.root / name if name.startswith('runs/') else folder / name
                    data = files.regular(source).read_bytes()
                    item = member_rows[name]
                    if item['type'] != 'file' or digest(data) != item['sha256'] or len(data) != item['size']:
                        raise Decision('Retained authority differs from its manifest: ' + name)
                    return data

                recording_evidence.check_authority(manifest, retained, snapshot_bytes=False)
                ref = manifest['evidence']['operation']
                record = None
                if ref:
                    source = self.root / ref if ref.startswith('runs/') else path.parent / ref
                    data = files.regular(source).read_bytes()
                    item = next(item for item in manifest['entries'] if item['path'] == ref)
                    if digest(data) != item['sha256'] or len(data) != item['size']:
                        raise Decision('Retained operation differs from its manifest')
                    record = checked(read(source), run)
                    # A derived discovery view, never written over retained operation bytes.
                    record = dict(
                        record,
                        phase='archived',
                        package=dict(
                            publication, path=str(self.root / recording_evidence.relative(publication['relative_path']))
                        ),
                    )
                self.published_recordings[run] = (manifest, record)
                indexed.add(run)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                self.recording_errors[run] = str(exc)
                problem(run, path, exc)
        identities.update(indexed)
        journals = self.root / 'migrations'
        pending, phases = {}, {}
        if journals.exists():
            for p in journals.glob('run-*.json'):
                if RUN.fullmatch(p.stem) and p.stem not in self.historical_runs:
                    identities.add(p.stem)
                    if p.stem in self.published_recordings:
                        continue
                    try:
                        phases[p.stem] = journal_phase(read(p), p.stem)
                        if phases[p.stem] != 'indexed':
                            pending[p.stem] = phases[p.stem]
                    except (OSError, ValueError, TypeError, AttributeError) as exc:
                        pending[p.stem] = phases[p.stem] = 'damaged'
                        problem(p.stem, p, exc)
        bindings, run_bindings = {}, {}
        for run in identities:
            for base in (self.field, self.root / 'runs'):
                path = base / run / 'session/binding.json'
                try:
                    binding = read(path) if path.exists() else {}
                    if binding.get('session_id'):
                        bindings.setdefault(binding['session_id'], set()).add(run)
                        if binding.get('run') == run:
                            run_bindings.setdefault(run, []).append(binding)
                except OSError, ValueError, TypeError, AttributeError:
                    pass  # locate() reports a damaged binding against its run.
        registered = {}
        for registry in self.registries:
            if not registry.exists():
                continue
            for p in registry.glob('*.json'):
                try:
                    registration = read(p)
                    run = registration.get('run')
                    if run and (not isinstance(run, str) or not RUN.fullmatch(run)):
                        raise ValueError('Registry has an invalid run identity')
                except (OSError, ValueError, TypeError, AttributeError) as exc:
                    if p.stem in bindings:
                        for run in bindings[p.stem]:
                            problem(run, p, exc)
                    else:
                        self.discovery_problems.append(f'Unattributed damaged session registry {p}: {exc}')
                    continue
                if registration.get('workspace') not in (None, str(self.root)):
                    continue  # Another Berman workspace's registration.
                if run and RUN.fullmatch(run):
                    if run in self.historical_runs:
                        continue
                    if run in support and registration.get('session_id') == support[run]:
                        continue
                    identities.add(run)
                    registered.setdefault(run, []).append(
                        {'path': f'{registry.name}/{p.name}', 'retired': bool(registration.get('retired'))}
                    )
        rows = []
        self.support_trials = {}
        for run in sorted(identities, key=lambda r: int(r[4:])):
            row = {
                'run': run,
                'location': 'problem',
                'state': 'needs-attention',
                'binding': None,
                'record': None,
                'migration_pending': run in pending,
                'problems': problems.get(run, []),
                'capture': None,
            }
            candidates = run_bindings.get(run, [])
            if len(candidates) == 1:
                row['binding'] = candidates[0]
            record_error = None
            try:
                if run in self.recording_errors:
                    raise Decision(self.recording_errors[run])
                row['record'] = (
                    self.published_recordings[run][1] if run in self.published_recordings else self.record(run)
                )
            except (OSError, ValueError, KeyError, TypeError) as exc:
                record_error = str(exc)
                row['problems'].append(record_error)
            if (row['record'] or {}).get('purpose') == 'support-trial':
                # A valid support reservation is never research; a damaged one stays a problem row below.
                self.support_trials[run] = {'record': row['record'], 'problems': row['problems']}
                continue
            try:
                if record_error and self.damaged_support(run):
                    # Never research, and no connection is known.
                    row['connection'], row['connection_basis'] = None, 'damaged support-trial marker'
                else:
                    row['connection'], row['connection_basis'] = self.attribute(run, row['record'])
                if not row['problems']:
                    self.locate(row, indexed, pending)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                row.setdefault('connection', self.attribute(run, None)[0])
                row['problems'].append(str(exc))
            evidence = self.evidence(row, record_error, registered.get(run, []), run in indexed, phases.get(run))
            row['lifecycle'] = assess(row['record'], evidence)
            row['started'] = row['lifecycle']['registered']['value']
            row['reading_pending'] = (
                row['location'] == 'archived'
                and row['lifecycle']['packaged']['value'] is True
                and (row['record'] or {}).get('closeout_contract') == 'reading/1'
                and not (self.folder(run) / 'closeout.json').is_file()
            )
            if row['reading_pending']:
                row['state'] = 'reading-pending'
            rows.append(row)
        self.support_identities |= set(self.support_trials)
        return rows

    def evidence(self, row, record_error, registrations, indexed, journal):
        """What discovery observed about one run, for berman.lifecycle.assess."""
        run, location = row['run'], row['location']
        roots = {
            'active': self.field / run,
            'archived': self.root / 'runs' / run,
            'unused': self.folder(run) / 'unused-packet',
            'reserved': self.field / run,
        }
        placed, root = location, roots.get(location)
        if location == 'problem':
            # A problem row's presence evidence counts only from a single packet root without symlinks.
            placed, existing = None, [name for name in ('active', 'archived') if roots[name].exists()]
            try:
                if len(existing) == 1:
                    safe_directory(roots[existing[0]])
                    placed, root = existing[0], roots[existing[0]]
            except OSError, ValueError:
                pass
        packet = packet_evidence(root) if root else {'closed_by': [], 'replacements': [], 'session_material': False}
        return dict(
            packet,
            location=location,
            placed=placed,
            record_error=record_error,
            binding=row['binding'],
            registrations=registrations,
            capture=row['capture'],
            journal=journal,
            indexed=indexed,
            problems=list(row['problems']),
        )

    def locate(self, row, indexed, pending):
        run, record = row['run'], row['record']
        active = self.field / run
        archived = self.root / 'runs' / run
        capture = None
        if active.exists() and archived.exists():
            raise Decision(f'{run} exists in active and archived locations; neither was selected')
        if archived.exists():
            location, state = 'archived', 'archived' if run in indexed else 'migration-pending'
            if run not in indexed and run not in pending:
                raise Decision(f'{run} is in the archive without inventory or migration journal')
            root = archived
        elif active.exists():
            # Presence and the packet's own last capture record only; its native source is never consulted.
            location, root = 'active', active
            capture = capture_metadata(root, run)
            closed = closed_by(root)
            state = (
                closed[0].removesuffix('.json')
                if closed
                else 'registered'
                if (root / 'session/binding.json').exists()
                else 'prepared'
            )
        elif record and record.get('phase') in ('cancelled', 'cancelling'):
            location, state, root = 'unused', record['phase'], self.folder(run) / 'unused-packet'
        elif record and record.get('phase') == 'reserved':
            location, state, root = 'reserved', 'reserved', active
        else:
            raise Decision(f'{run} is recorded but its packet is missing')
        path = root / 'session/binding.json'
        try:
            binding = read(path) if path.exists() else None
            if binding is not None and not isinstance(binding, dict):
                raise ValueError('expected an object')
        except (OSError, ValueError) as exc:
            raise Decision(f'{run}: damaged session binding at {path}: {exc}') from exc
        if binding and binding.get('run') != run:
            raise Decision(f'{run}: session binding belongs to a different run')
        if (
            run not in getattr(self, 'published_recordings', {})
            and record
            and record.get('packet')
            and record['packet'] != str(active)
        ):
            raise Decision(f'{run}: recorded packet {record["packet"]} is outside the shared packet root')
        row.update(location=location, state=state, binding=binding, capture=capture)

    def damaged_support(self, run):
        """Whether a damaged operation record was a support marker: by its identity, or by the purpose it states."""
        if int(run[4:]) in SUPPORT_RANGE:
            return True
        try:
            return read(self.folder(run) / 'operation.json').get('purpose') == 'support-trial'
        except OSError, ValueError, TypeError, AttributeError:
            return False


def plan_status(connection, rows):
    """How far a selected plan's recorded sequence got; a broken plan never hides other records."""
    result = {'plan': None, 'next_step': None, 'next_recipe': None, 'plan_note': None, 'plan_error': None}
    try:
        plan = connection.plan
        if plan is not None:
            result['plan'] = plan.id
            step = plan.next_step(rows)
            result.update(next_step=step['id'], next_recipe=step['recipe'])
    except Complete:
        result['plan_note'] = 'every step was started'
    except PROBLEMS as exc:
        result['plan_error'] = str(exc)
    return result


def requests(store):
    """Combined finish-and-prepare requests that never completed, and those that cannot be read."""
    pending, problems = [], []
    for path in sorted((store.operations / 'new-requests').glob('*.json')):
        try:
            value = read(path)
            state = value.get('state')
            if state not in ('pending', 'complete', 'cancelled'):
                raise ValueError(f'unknown state {state!r}')
        except PROBLEMS as exc:
            problems.append(f'Combined request {path} cannot be read: {exc}')
            continue
        if state == 'pending':
            pending.append(
                {
                    'path': str(path),
                    'previous_run': value.get('previous_run'),
                    'result_run': value.get('result_run'),
                    'connection': value.get('connection'),
                }
            )
    return pending, problems


def support_trials(store):
    """Native support trials the workspace never closed, from the latest discover(); never research."""
    result = []
    for run, found in sorted(store.support_trials.items()):
        record, problems = found['record'], list(found['problems'])
        receipt, phase = store.root / record['support_trial'], None
        if not receipt.exists():
            problems.append(f'{run}: its marker names {record["support_trial"]}, which was never written')
        else:
            try:
                trial = read(receipt)
                if not isinstance(trial, dict) or trial.get('schema') != SUPPORT_SCHEMA or trial.get('run') != run:
                    raise ValueError(f'not the {SUPPORT_SCHEMA} receipt of {run}')
                phase = trial.get('phase')
            except PROBLEMS as exc:
                problems.append(f'{run}: {receipt} cannot be read: {exc}')
        if phase in SUPPORT_CLOSED and not problems:
            continue
        result.append(
            {
                'run': run,
                'adapter': record['capture']['adapter'],
                'phase': phase,
                'evidence': Path(record['support_trial']).parent.as_posix(),
                'problems': problems,
                'opened_with': (record.get('software') or {}).get('version'),
            }
        )
    return result


def status(store):
    """What a co-located workspace holds and what in it is unsettled. Reads only; settles nothing."""
    rows = store.discover()
    connections = {}
    for conn in store.connections.values():
        connections[conn.id] = {
            'name': conn.name,
            'checkout': str(conn.checkout),
            **plan_status(conn, rows),
            'problems': [p for r in rows if r.get('connection') == conn.id for p in r['problems']],
        }
    runs, waiting = [], []
    for row in filter(outstanding, rows):
        record, lifecycle = row['record'] or {}, row['lifecycle']
        reason, version = unsettled(row), (record.get('software') or {}).get('version')
        if reason:
            waiting.append(f'{row["run"]}: ' + awaits(reason, version))
        runs.append(
            {
                'run': row['run'],
                'connection': row.get('connection'),
                'state': row['state'],
                'recipe': ((record.get('recipe') or {}).get('metadata') or {}).get('id'),
                'assignment_origin': record.get('assignment_origin'),
                'phase': record.get('phase'),
                'session': (row['binding'] or {}).get('session_id'),
                'ending_declared': lifecycle['ending']['value'] is True,
                'started': row['started'],
                'record': str(store.folder(row['run'])) if record else None,
                'last_capture': (row['capture'] or {}).get('captured_at_utc'),
                'problems': row['problems'],
                'summary': describe(lifecycle, record.get('phase') or row['state']),
                'unsettled': reason,
                'opened_with': version,
                'unknown': lifecycle['unknown'],
                'lifecycle': lifecycle,
            }
        )
    pending, unreadable = requests(store)
    for request in pending:
        waiting.append(
            f'{request["path"]}: '
            + awaits(
                f'a combined request to end {request["previous_run"]} and prepare {request["result_run"]} never completed'
            )
        )
    trials = support_trials(store)
    for trial in trials:
        if not trial['problems']:
            waiting.append(
                f'{trial["run"]}: '
                + awaits(f'support trial {trial["evidence"]} was left at phase {trial["phase"]}', trial['opened_with'])
            )
    return {
        'runs': runs,
        'archived': sum(r['location'] == 'archived' for r in rows) + len(store.historical_runs),
        'historical_runs': sorted(store.historical_runs),
        'workspace_problems': store.discovery_problems + unreadable,
        'default_connection': store.default_connection,
        'connections': connections,
        'pending_requests': pending,
        'support_trials': trials,
        'unsettled': waiting,
        'meaning': READ_ONLY + ' Session activity and declared engagement endings are distinct facts, and none '
        'is inferred. No files were changed.',
    }
