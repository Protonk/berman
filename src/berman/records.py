"""Discover run identities across connections and keep resumable, researcher-side operation records."""
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import time
import uuid

from berman import evidence as recording_evidence
from berman import files, schemas, snapshots
from berman.capture import runtime
from berman.capture.claude import ClaudeCollector
from berman.config import Catalogue, Connection
from berman.files import Decision, digest, read, safe_directory, write
from berman._legacy.api import RUN
from berman.lifecycle import SESSION_MATERIAL, assess, journal_phase, validate_record, validate_request

LOCK_LABELS = {'global': 'the Berman workspace allocation and index', 'connection': 'connection {}',
               'run': '{}', 'installation': 'harness installation resource {}'}
SUPPORT_RANGE = range(900101, 900200)  # Native support trials: run-900101 to run-900199.


def closed_by(root):
    """The files that close a packet's capture, by presence; neither is an engagement ending."""
    return [name for name in ('finalized.json', 'ended.json') if (root / 'session' / name).exists()]


def packet_evidence(root):
    """Presence checks only (no reads, hashes or writes), so they are safe beneath runs/."""
    return {'closed_by': closed_by(root),
            'replacements': sorted(str(p.relative_to(root)) for p in root.glob('session-history/*/replacement.json')),
            'session_material': any((root / name).exists() for name in SESSION_MATERIAL)}


def capture_metadata(root, run):
    """Read a snapshot's minimum evidence, distinguishing absence from damage.

    The collector's historical status reader treated false JSON values as no metadata. Do
    this check before calling it, without changing the collector or any preserved bytes.
    """
    try:
        path = snapshots.resolve(root / 'session') / 'capture.json'
    except FileNotFoundError as exc:
        raise Decision(f'{run}: missing capture metadata: {exc}') from exc
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise Decision(f'{run}: damaged capture metadata: {exc}') from exc
    if not path.exists() and not path.is_symlink():
        if any((root / 'session' / name).exists() for name in
               ('transcript.jsonl', 'captures.jsonl', 'conversation.txt', 'finalized.json', 'snapshots')):
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

    A reservation is published by the atomic write of operation.json. If that write fails, or the
    process stops inside it, the folder made to hold it stays behind, empty or with the write's
    temporary file. Nothing else exists for that identity yet, so it is not a run: discovery passes
    over it and the retried command reserves the same identity. A folder with anything else in it,
    or one that cannot be listed, is still reported.
    """
    try:
        return folder.is_dir() and not folder.is_symlink() and all(
            entry.name.startswith('.capture-') and entry.is_file() and not entry.is_symlink()
            for entry in folder.iterdir())
    except OSError:
        return False


def checked(value, run):
    """An operation record unchanged, or ValueError saying why it is not one.

    Lifecycle rules come first and keep their wording; the schema then refuses structure they never
    looked at. Neither changes the record.
    """
    return schemas.validate('berman.operation/1', validate_record(value, run))


def checked_request(value):
    """A combined new-run request unchanged, or ValueError; lifecycle rules first, then structure."""
    checked = validate_request(value)
    return schemas.validate(checked['schema'], checked)


def runtime_adapters():
    from berman.config import CAPTURE_ADAPTERS
    return CAPTURE_ADAPTERS


class Store:
    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()
        safe_directory(self.root)
        raw = read(self.root / 'berman.json')
        family = raw.get('schema') if isinstance(raw, dict) else None
        if family == 'berman.configuration/1':
            raise ValueError('The single-project berman.configuration/1 format is no longer read; describe the '
                             'project as a connection in a berman.workspace/1 file')
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
        self.python = self.path(workspace['python'])
        # Hook commands run the installed package in isolated mode, so they name an interpreter that
        # has it. Project checks use the configured general interpreter above.
        self.runtime_python = self.path(workspace.get('runtime_python', workspace['python']))
        self.codex = self.path(workspace['codex'])
        self.claude = self.path(workspace.get('claude', '~/.local/bin/claude'))
        self.claude_home = self.path(workspace.get('claude_home', '~/.claude'))
        self.operations = self.root / 'operations'
        self.lock_dir = self.root / '.berman-locks'
        self.tag = digest(str(self.root).encode())[:8]
        self.collectors = {'berman-codex': runtime.Collector(self.field, self.home, self.root),
                           'berman-claude': ClaudeCollector(self.field, self.claude_home, self.root)}
        self.collector = self.collectors['berman-codex']
        self.catalogue = Catalogue(self, workspace)
        self.connections = {ident: Connection(self, ident, entry) for ident, entry in workspace['connections'].items()}
        self.default_connection = workspace.get('default_connection')
        # Adapters this installation keeps qualified even while no connection selects them.
        self.qualified_adapters = tuple(workspace.get('qualified_adapters', ()))
        for name in self.qualified_adapters:
            if name not in runtime_adapters():
                raise ValueError(f'Unknown capture adapter in qualified_adapters: {name}')
        self.edition = workspace.get('edition', 'connections')
        if self.edition not in ('connections', 'stewart'):
            raise ValueError(f'Unknown reading edition: {self.edition}')
        self.legacy_connection = workspace.get('legacy_connection')
        for name in (self.default_connection, self.legacy_connection):
            if name is not None and name not in self.connections:
                raise ValueError(f'Configured connection is not registered: {name}')
        # The assigned packet must never reopen the research repository or a project.
        roots = [('Berman', self.root), ('the shared packet root', self.field)]
        roots += [(f'connection {c.id} checkout', c.checkout) for c in self.connections.values()]
        for i, (left, a) in enumerate(roots):
            for right, b in roots[i + 1:]:
                if a == b or a in b.parents or b in a.parents:
                    if left.startswith('connection') and right.startswith('connection'):
                        continue  # Two projects may share a parent; neither is a research root.
                    raise ValueError(f'{left} and {right} overlap; research and project roots must be disjoint')

    def path(self, value):
        p = Path(value).expanduser()
        return Path(os.path.abspath(p if p.is_absolute() else self.root / p))

    def connection(self, name=None):
        name = name or self.default_connection
        if name is None:
            raise Decision('Choose a connection with --connection; no default connection is configured')
        if name not in self.connections:
            raise Decision(f'Unknown connection {name}; registered connections: {", ".join(sorted(self.connections))}')
        return self.connections[name]

    @property
    def project(self):
        """The default connection's checkout, for compatibility with single-project callers."""
        return self.connection().checkout

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

    def save(self, record):
        path = self.folder(record['run']) / 'operation.json'
        try:
            checked(record, record['run'])
        except ValueError as exc:
            raise ValueError(f'Invalid operation record: {path}: {exc}') from exc
        write(path, record)

    def adapter(self, record):
        """The adapter a run was reserved with. Records made before adapters were named used legacy-codex."""
        if record is None:
            return 'berman-codex'
        return (record.get('capture') or {}).get('adapter', 'legacy-codex')

    def collector_for(self, record):
        self.check_installation(record)
        name = self.adapter(record)
        if name not in self.collectors:
            raise Decision(f'{record["run"]} was recorded with the removed {name} adapter. Its preserved packet is '
                           'read in place; it can no longer be captured, supplemented or migrated')
        collector = self.collectors[name]
        return collector.for_run(record['run']) if record else collector

    def check_installation(self, record):
        """Never reinterpret a reservation under a different local installation."""
        expected = (record or {}).get('harness') or {}
        claude = expected.get('id') == 'claude'
        current = {'binary': str(self.claude if claude else self.codex), 'python': str(self.python),
                   'runtime_python': str(self.runtime_python),
                   'home': str(self.claude_home if claude else self.home)}
        current['profile'] = f'berman-{self.tag}-{record["run"]}' if record else None
        changed = [f'{key}: recorded {expected[key]}, configured {value}' for key, value in current.items()
                   if key in expected and expected[key] != value]
        workspace = (record or {}).get('workspace') or {}
        if workspace.get('root', str(self.root)) != str(self.root):
            changed.append(f'workspace: recorded {workspace["root"]}, configured {self.root}')
        if changed:
            raise Decision(f'{record["run"]}: harness installation differs from its reservation (' + '; '.join(changed)
                           + '). Restore the recorded settings before continuing; relocation requires an explicit recorded transition.')

    def harness_identity(self, adapter, run, model=None):
        claude = adapter == 'berman-claude'
        result = {'id': 'claude' if claude else 'codex',
                  'binary': str(self.claude if claude else self.codex),
                  'home': str(self.claude_home if claude else self.home),
                  'python': str(self.python)}
        if self.runtime_python != self.python:
            result['runtime_python'] = str(self.runtime_python)
        if claude:
            result['session_id'] = str(uuid.uuid4())
            if model is not None:
                if not isinstance(model, str) or not model.strip():
                    raise ValueError('The configured fieldwork model must be a nonempty string')
                result['model'] = model
        return result

    def lock_path(self, kind, name=None):
        if kind == 'global':
            return self.root / '.berman.lock'
        self.lock_dir.mkdir(exist_ok=True)
        return self.lock_dir / f'{kind}-{name}.lock'

    @contextmanager
    def lock(self, kind, name=None, wait=0):
        """Lock order: connection, runs in numeric order, then the short global section."""
        label = LOCK_LABELS[kind].format(name)
        fd = os.open(self.lock_path(kind, name), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            deadline = time.monotonic() + wait
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise Decision(f'Another Berman operation is using {label}; retry this same command '
                                       'when it finishes') from None
                    time.sleep(0.02)
            yield
        finally:
            os.close(fd)

    def occupied(self):
        """Read-only probe of held locks for status; never creates a lock file."""
        held = []
        candidates = [('global', None, self.root / '.berman.lock')]
        if self.lock_dir.exists():
            for path in sorted(self.lock_dir.glob('*.lock')):
                kind, _, name = path.stem.partition('-')
                if kind in LOCK_LABELS:
                    candidates.append((kind, name, path))
        for kind, name, path in candidates:
            if not path.exists():
                continue
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                held.append(LOCK_LABELS[kind].format(name))
            finally:
                os.close(fd)
        return held

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
            except (OSError, ValueError, TypeError, AttributeError):
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
                    continue  # Publication is unfinished; the resumable operation still owns it.
                manifest = recording_evidence.validate(read(path))
                publication = read(path.parent / 'publication.json')
                if (manifest['run'] != run or manifest['collection_id'] != recording_evidence.collection(self.root)
                        or publication['manifest_sha256'] != recording_evidence.identity(manifest)):
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
                    record = dict(record, phase='archived', package=dict(publication, path=str(self.root / recording_evidence.relative(publication['relative_path']))))
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
                except (OSError, ValueError, TypeError, AttributeError):
                    pass  # locate() reports a damaged binding against its run.
        registered = {}
        for collector in self.collectors.values():
            if not collector.state.exists():
                continue
            for p in collector.state.glob('*.json'):
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
                    registered.setdefault(run, []).append({'path': f'{collector.state.name}/{p.name}',
                                                           'retired': bool(registration.get('retired'))})
        rows = []
        self.support_trials = {}
        for run in sorted(identities, key=lambda r: int(r[4:])):
            row = {'run': run, 'location': 'problem', 'state': 'needs-attention', 'binding': None,
                   'record': None, 'migration_pending': run in pending, 'problems': problems.get(run, []),
                   'capture_status': None}
            candidates = run_bindings.get(run, [])
            if len(candidates) == 1:
                row['binding'] = candidates[0]
            record_error = None
            try:
                if run in self.recording_errors:
                    raise Decision(self.recording_errors[run])
                row['record'] = (self.published_recordings[run][1] if run in self.published_recordings else self.record(run))
            except (OSError, ValueError, KeyError, TypeError) as exc:
                record_error = str(exc)
                row['problems'].append(record_error)
            if (row['record'] or {}).get('purpose') == 'support-trial':
                # A valid support reservation is never research; a damaged one stays a problem row below.
                self.support_trials[run] = {'record': row['record'], 'problems': row['problems']}
                continue
            try:
                if record_error and self.damaged_support(run):
                    # Never research, and no connection is known: it blocks every connection until recovered.
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
            row['reading_pending'] = (row['location'] == 'archived' and row['lifecycle']['packaged']['value'] is True
                                      and (row['record'] or {}).get('closeout_contract') == 'reading/1'
                                      and not (self.folder(run) / 'closeout.json').is_file())
            if row['reading_pending']:
                row['state'] = 'reading-pending'
            rows.append(row)
        self.support_identities |= set(self.support_trials)
        return rows

    def evidence(self, row, record_error, registrations, indexed, journal):
        """What discovery observed about one run, for berman_lifecycle.assess."""
        run, location = row['run'], row['location']
        roots = {'active': self.field / run, 'archived': self.root / 'runs' / run,
                 'unused': self.folder(run) / 'unused-packet', 'reserved': self.field / run}
        placed, root = location, roots.get(location)
        if location == 'problem':
            # A problem row's presence evidence counts only from a single packet root without symlinks.
            placed, existing = None, [name for name in ('active', 'archived') if roots[name].exists()]
            try:
                if len(existing) == 1:
                    safe_directory(roots[existing[0]])
                    placed, root = existing[0], roots[existing[0]]
            except (OSError, ValueError):
                pass
        packet = packet_evidence(root) if root else {'closed_by': [], 'replacements': [], 'session_material': False}
        return dict(packet, location=location, placed=placed, record_error=record_error, binding=row['binding'],
                    registrations=registrations, capture=row['capture_status'], journal=journal, indexed=indexed,
                    problems=list(row['problems']))

    def locate(self, row, indexed, pending):
        run, record = row['run'], row['record']
        active = self.field / run
        archived = self.root / 'runs' / run
        status = None
        if active.exists() and archived.exists():
            raise Decision(f'{run} exists in active and archived locations; neither was selected')
        if archived.exists():
            location, state = 'archived', 'archived' if run in indexed else 'migration-pending'
            if run not in indexed and run not in pending:
                raise Decision(f'{run} is in the archive without inventory or migration journal')
            root = archived
        elif active.exists():
            location, root = 'active', active
            capture_metadata(root, run)
            status = self.collector_for(record).status(run)
            state = status['state']
            if state in ('damaged', 'unbound-session-files', 'identity-collision'):
                raise Decision(f'{run}: {state}: {status["warnings"]}')
        elif record and record.get('phase') in ('cancelled', 'cancelling'):
            location, state, root = 'unused', record['phase'], self.folder(run) / 'unused-packet'
        elif record and record.get('phase') == 'reserved':
            location, state, root = 'reserved', 'reserved', active
        else:
            raise Decision(f'{run} is recorded but its packet is missing; recover it before allocating another identity')
        binding = read(root / 'session/binding.json') if (root / 'session/binding.json').exists() else None
        if binding and binding.get('run') != run:
            raise Decision(f'{run}: session binding belongs to a different run')
        if run not in getattr(self, 'published_recordings', {}) and record and record.get('packet') and record['packet'] != str(active):
            raise Decision(f'{run}: recorded packet {record["packet"]} is outside the shared packet root')
        row.update(location=location, state=state, binding=binding, capture_status=status)

    def require(self, rows, connection):
        """Problems on a connection's own runs block its operations; others are only reported."""
        if getattr(self, 'discovery_problems', []):
            raise Decision(self.discovery_problems[0] + '; recover its identity before allocating or selecting a run')
        for row in rows:
            if row['problems'] and row.get('connection') in (connection, None):
                raise Decision(row['problems'][0])

    def damaged_support(self, run):
        """Whether a damaged operation record was a support marker: by its identity, or by the purpose it states."""
        if int(run[4:]) in SUPPORT_RANGE:
            return True
        try:
            return read(self.folder(run) / 'operation.json').get('purpose') == 'support-trial'
        except (OSError, ValueError, TypeError, AttributeError):
            return False

    def allocate(self, rows):
        """The next research identity; the support range neither sets research numbering nor receives it."""
        number = max((int(run[4:]) for run in {r['run'] for r in rows} | self.historical_runs
                      if int(run[4:]) not in SUPPORT_RANGE), default=0) + 1
        while number in SUPPORT_RANGE or f'run-{number:03d}' in getattr(self, 'support_identities', set()):
            number += 1
        if number > 999999:
            raise Decision('The supported run identity range is exhausted')
        return f'run-{number:03d}'

    def allocate_support(self, rows):
        """The smallest support identity that no research row or support identity uses; research numbering is unaffected."""
        used = {r['run'] for r in rows} | getattr(self, 'support_identities', set())
        for number in SUPPORT_RANGE:
            if f'run-{number}' not in used:
                return f'run-{number}'
        raise Decision(f'The support identity range run-{SUPPORT_RANGE[0]} to run-{SUPPORT_RANGE[-1]} is exhausted')
