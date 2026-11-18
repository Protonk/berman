"""Researcher-controlled workspace inputs: connections, research plans, and the run catalogue.

A connection describes a project. A research plan says which engagements are wanted and what
they receive. The catalogue associates runs that predate these records with their connection.
Local installation settings (checkout locations, the shared packet root, executables) live in
berman.json and are kept apart from the reusable connection description.
"""
from pathlib import Path
from functools import cached_property
import re

from berman import schemas
from berman.files import Decision, digest, digest_json, read, regular
from berman._legacy.api import RUN

IDENTITY = re.compile(r'[a-z][a-z0-9-]{0,62}')
CAPTURE_ADAPTERS = ('berman-codex', 'berman-claude')
# Selected by workspaces before 0.4; recorded runs may still name it.
REMOVED_ADAPTERS = {'legacy-codex': 'berman-codex'}
# Keys that would let a connection or installation entry place packets elsewhere.
PACKET_ROOT_KEYS = ('packet_root', 'fieldwork', 'packets')
# Text that used to be appended to the opening message.
RETIRED = ('commission', 'revision')


class Complete(Decision):
    """Every step of a research plan has been started; another choice must be recorded."""


def identity(value, kind):
    if not isinstance(value, str) or not IDENTITY.fullmatch(value):
        raise ValueError(f'Invalid {kind} identity: {value!r}')
    return value


def inside(base, relative, kind):
    path = Path(relative)
    if path.is_absolute() or '..' in path.parts:
        raise ValueError(f'{kind} must stay within its directory: {relative}')
    return base / path


class Connection:
    """A named project description plus this workspace's binding to a checkout."""

    def __init__(self, store, ident, entry):
        self.id = identity(ident, 'connection')
        if any(key in entry for key in PACKET_ROOT_KEYS):
            raise Decision(f'Connection {ident} names its own packet location. All packets stay in the shared '
                           'packet root used by local session discovery')
        self.store = store
        self.entry = entry
        self.definition_path = store.path(entry['definition'])
        if not self.definition_path.is_relative_to(store.root):
            raise ValueError(f'Connection definitions are researcher inputs held in Berman: {self.definition_path}')
        self.folder = self.definition_path.parent
        self.definition = read(self.definition_path)
        if (not isinstance(self.definition, dict) or self.definition.get('schema') != 'berman.connection/1'
                or self.definition.get('id') != self.id):
            raise ValueError(f'Invalid connection definition: {self.definition_path}')
        schemas.validate('berman.connection/1', self.definition, self.definition_path)
        if any(key in self.definition for key in PACKET_ROOT_KEYS):
            raise Decision(f'Connection {ident} definition names a packet location; it must use the shared packet root')
        self.checkout = store.path(entry['checkout'])
        self.capture = entry.get('capture', 'berman-codex')
        if self.capture in REMOVED_ADAPTERS:
            raise ValueError(f'The {self.capture} capture adapter was removed; set the capture of connection {ident} '
                             f'to {REMOVED_ADAPTERS[self.capture]} in berman.json. Recorded runs keep their packets')
        if self.capture not in CAPTURE_ADAPTERS:
            raise ValueError(f'Unknown capture adapter for {ident}: {self.capture}')
        self.name = self.definition.get('name') or self.id  # Null is read as absent.

    @cached_property
    def plan(self):
        """Read a selected plan only when choosing or describing planned work."""
        path = self.entry.get('plan')
        plan = None if path is None else Plan(self.store, path)
        if plan and plan.connection != self.id:
            raise ValueError(f'Research plan {plan.id} belongs to {plan.connection}, not {self.id}')
        return plan

    def documents(self):
        return list(self.definition['documents'])

    def checks(self):
        return list(self.definition.get('checks', []))

    def development(self):
        return dict(self.definition.get('development', {}))

    def files(self):
        """Every researcher-controlled file this description depends on."""
        names = [self.definition_path]
        return {str(p.relative_to(self.store.root)): digest(regular(p).read_bytes()) for p in names}

    def reservation(self):
        """The resolved description retained in each new run record."""
        retired = [key for key in RETIRED if key in self.definition]
        if retired:
            raise Decision(f'Connection {self.id} names {" and ".join(retired)}, which no longer reaches a fieldworker: '
                           'the opening message is the same for every connection. Move anything a fieldworker needs '
                           f'into the recipe\'s instructions and remove {" and ".join(retired)} from {self.definition_path}')
        return {'id': self.id, 'name': self.name, 'definition': str(self.definition_path.relative_to(self.store.root)),
                'files_sha256': self.files(), 'digest': digest_json(self.files()),
                'version': self.definition.get('version'), 'documents': self.documents(),
                'checks': self.checks(), 'readers': self.definition.get('readers', []),
                'checkout': str(self.checkout)}


class Plan:
    """Which engagements are wanted for one connection, and in what order."""

    def __init__(self, store, value):
        self.path = store.path(value)
        if not self.path.is_relative_to(store.root):
            raise ValueError(f'Research plans are researcher inputs held in Berman: {self.path}')
        self.data = read(self.path)
        if not isinstance(self.data, dict) or self.data.get('schema') != 'berman.research-plan/1':
            raise ValueError(f'Invalid research plan: {self.path or value}')
        schemas.validate('berman.research-plan/1', self.data, self.path)
        self.store = store
        self.id = identity(self.data['id'], 'research plan')
        self.connection = identity(self.data['connection'], 'connection')
        self.steps = list(self.data['steps'])
        self.ids = [s['id'] for s in self.steps]
        if len(self.ids) != len(set(self.ids)):
            raise ValueError('Duplicate instruction-sequence step')

    def reservation(self, step):
        return {'id': self.id, 'path': str(self.path.relative_to(self.store.root)) if self.path else None,
                'digest': digest_json(self.data), 'step': step['id']}

    def claims(self, row):
        """Whether a run record is associated with this plan (explicitly, or by a pre-plan step id)."""
        record = row['record'] or {}
        if record.get('assignment_origin') == 'direct':
            return False
        plan = record.get('plan')
        if plan:
            return plan.get('id') == self.id
        return row['connection'] == self.connection and record.get('step') in self.ids

    def next_step(self, rows):
        consumed = {}
        for row in rows:
            if row['connection'] != self.connection or not self.claims(row):
                continue  # Only this plan's recorded assignments affect its sequence.
            record = row['record'] or {}
            started = row['started']
            if started is None:
                reasons = '; '.join(row['lifecycle']['registered']['unknown'])
                raise Decision(f'Cannot determine the next role: {row["run"]} needs attention and its starting state is unknown ({reasons})')
            if not started:
                continue
            if self.claims(row):
                step = record.get('step', (record.get('plan') or {}).get('step'))
                if step not in self.ids or step in consumed:
                    raise Decision(f'Conflicting sequence claim for {step}; inspect the run records')
                consumed[step] = row['run']
        for step in self.steps:
            if step['id'] not in consumed:
                if any(later in consumed for later in self.ids[self.ids.index(step['id']) + 1:]):
                    raise Decision('The recorded fieldwork roles were started out of sequence; inspect their relationship')
                return step
        raise Complete(f'The agreed fieldwork sequence is complete (plan {self.id}). Record the next instruction choice '
                       'in a plan or choose a recipe with berman new --recipe NAME')

    def fulfilled(self, rows):
        """True only when every step has been started; a conflict leaves fulfilment undetermined."""
        try:
            self.next_step(rows)
        except Complete:
            return True
        except Decision:
            return False
        return False


class Catalogue:
    """Explicit associations for runs that predate connection records, and support-trial exclusions."""

    def __init__(self, store, workspace):
        self.store = store
        path = workspace.get('catalogue')
        data = read(store.path(path)) if path else {'schema': 'berman.run-catalogue/1', 'runs': []}
        if not isinstance(data, dict) or data.get('schema') != 'berman.run-catalogue/1':
            raise ValueError('Invalid run catalogue')
        schemas.validate('berman.run-catalogue/1', data, store.path(path) if path else None)
        self.data = data
        self.runs = {}
        for row in data['runs']:
            if not RUN.fullmatch(row['run']) or row['run'] in self.runs:
                raise ValueError(f'Invalid or duplicate catalogued run: {row["run"]}')
            self.runs[row['run']] = row
        self.support = list(data.get('support_collections', [])) + list(workspace.get('support_collections', []))

    def connection(self, run):
        row = self.runs.get(run)
        return row['connection'] if row else None

    def predates_plans(self, run, connection):
        row = self.runs.get(run)
        return bool(row) and row['connection'] == connection and row.get('relationship') == 'predates research plans'
