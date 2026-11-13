"""Researcher-controlled workspace inputs: connections, research plans, and the run catalogue.

A connection describes a project. A research plan says which engagements are wanted and what
they receive. The catalogue associates runs that predate these records with their connection.
Local installation settings (checkout locations, the shared packet root, executables) live in
berman.json and are kept apart from the reusable connection description.
"""
from pathlib import Path
import re

from berman.files import Decision, digest, digest_json, read, regular
from berman._legacy.api import RUN

IDENTITY = re.compile(r'[a-z][a-z0-9-]{0,62}')
CAPTURE_ADAPTERS = ('legacy-codex', 'berman-codex', 'berman-claude')
# Keys that would let a connection or installation entry place packets elsewhere.
PACKET_ROOT_KEYS = ('packet_root', 'fieldwork', 'packets')


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


def legacy_workspace(config):
    """Interpret the single-project berman.configuration/1 file as one Stewart connection."""
    plan = {'schema': 'berman.research-plan/1', 'id': 'legacy-sequence', 'connection': 'stewart',
            'steps': config['sequence'], 'commitment': config.get('commitment'),
            'meaning': 'Synthesized from a berman.configuration/1 sequence during migration.'}
    return {'schema': 'berman.workspace/1', 'packet_root': config['fieldwork'],
            'codex_home': config['codex_home'], 'python': config['python'], 'codex': config['codex'],
            'default_connection': 'stewart', 'legacy_connection': 'stewart',
            'support_collections': config.get('support_collections', []),
            'legacy_sequence_after': config['sequence_after'],
            'connections': {'stewart': {'definition': 'connections/stewart/connection.json',
                                        'checkout': config['project'], 'plan': plan,
                                        'capture': 'legacy-codex', 'documents': config['documents']}}}


class Connection:
    """A named project description plus this workspace's binding to a checkout."""

    def __init__(self, store, ident, entry):
        self.id = identity(ident, 'connection')
        if any(key in entry for key in PACKET_ROOT_KEYS):
            raise Decision(f'Connection {ident} names its own packet location. All packets stay in the shared '
                           'denied packet root; separate roots are a deferred access-policy migration')
        self.store = store
        self.entry = entry
        self.definition_path = store.path(entry['definition'])
        if not self.definition_path.is_relative_to(store.root):
            raise ValueError(f'Connection definitions are researcher inputs held in Berman: {self.definition_path}')
        self.folder = self.definition_path.parent
        self.definition = read(self.definition_path)
        if self.definition.get('schema') != 'berman.connection/1' or self.definition.get('id') != self.id:
            raise ValueError(f'Invalid connection definition: {self.definition_path}')
        if any(key in self.definition for key in PACKET_ROOT_KEYS):
            raise Decision(f'Connection {ident} definition names a packet location; it must use the shared packet root')
        self.checkout = store.path(entry['checkout'])
        self.capture = entry.get('capture', 'berman-codex')
        if self.capture not in CAPTURE_ADAPTERS:
            raise ValueError(f'Unknown capture adapter for {ident}: {self.capture}')
        self.name = self.definition.get('name', self.id)
        plan = entry.get('plan')
        self.plan = None if plan is None else Plan(store, plan)
        if self.plan and self.plan.connection != self.id:
            raise ValueError(f'Research plan {self.plan.id} belongs to {self.plan.connection}, not {self.id}')

    def documents(self):
        # A legacy configuration supplied its own document selection.
        return list(self.entry.get('documents', self.definition['documents']))

    def commission(self):
        return regular(inside(self.folder, self.definition['commission'], 'Commission')).read_text()

    def revision(self, head):
        template = self.definition['revision']
        if template.count('{head}') != 1:
            raise ValueError(f'Connection {self.id} revision text must contain one {{head}} placeholder')
        return template.replace('{head}', head)

    def checks(self):
        return list(self.definition.get('checks', []))

    def development(self):
        return dict(self.definition.get('development', {}))

    def files(self):
        """Every researcher-controlled file this description depends on."""
        names = [self.definition_path, inside(self.folder, self.definition['commission'], 'Commission')]
        return {str(p.relative_to(self.store.root)): digest(regular(p).read_bytes()) for p in names}

    def reservation(self):
        """The resolved description retained in each new run record."""
        return {'id': self.id, 'name': self.name, 'definition': str(self.definition_path.relative_to(self.store.root)),
                'files_sha256': self.files(), 'digest': digest_json(self.files()),
                'version': self.definition.get('version'), 'documents': self.documents(),
                'commission': self.commission(), 'revision_template': self.definition['revision'],
                'checks': self.checks(), 'readers': self.definition.get('readers', []),
                'checkout': str(self.checkout)}


class Plan:
    """Which engagements are wanted for one connection, and in what order."""

    def __init__(self, store, value):
        if isinstance(value, dict):
            # Only a legacy configuration synthesizes its plan in place.
            self.path, self.data = None, value
        else:
            self.path = store.path(value)
            if not self.path.is_relative_to(store.root):
                raise ValueError(f'Research plans are researcher inputs held in Berman: {self.path}')
            self.data = read(self.path)
        if self.data.get('schema') != 'berman.research-plan/1':
            raise ValueError(f'Invalid research plan: {self.path or value}')
        self.store = store
        self.id = identity(self.data['id'], 'research plan')
        self.connection = identity(self.data['connection'], 'connection')
        self.steps = list(self.data['steps'])
        self.ids = [s['id'] for s in self.steps]
        if len(self.ids) != len(set(self.ids)):
            raise ValueError('Duplicate instruction-sequence step')
        self.commitment = self.data.get('commitment')
        self.relationships = {r['plan']: r for r in self.data.get('relationships', [])}

    def reservation(self, step):
        return {'id': self.id, 'path': str(self.path.relative_to(self.store.root)) if self.path else None,
                'digest': digest_json(self.data), 'step': step['id']}

    def claims(self, row):
        """Whether a run record is associated with this plan (explicitly, or by a pre-plan step id)."""
        record = row['record'] or {}
        plan = record.get('plan')
        if plan:
            return plan.get('id') == self.id
        return row['connection'] == self.connection and record.get('step') in self.ids

    def next_step(self, rows):
        consumed = {}
        for row in rows:
            if row['connection'] != self.connection:
                continue  # Another project's runs neither advance nor invalidate this plan.
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
            elif record.get('plan'):
                continue  # Explicitly associated with a different plan for this connection.
            elif not self.store.catalogue.predates_plans(row['run'], self.connection):
                raise Decision(f'{row["run"]} began outside the recorded research plan; assign its relationship before advancing')
        for step in self.steps:
            if step['id'] not in consumed:
                if any(later in consumed for later in self.ids[self.ids.index(step['id']) + 1:]):
                    raise Decision('The recorded fieldwork roles were started out of sequence; inspect their relationship')
                return step
        raise Complete(f'The agreed fieldwork sequence is complete (plan {self.id}). Record the next instruction choice '
                       'in a research plan before preparing another run')

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
        if data.get('schema') != 'berman.run-catalogue/1':
            raise ValueError('Invalid run catalogue')
        self.data = data
        self.runs = {}
        for row in data['runs']:
            if not RUN.fullmatch(row['run']) or row['run'] in self.runs:
                raise ValueError(f'Invalid or duplicate catalogued run: {row["run"]}')
            self.runs[row['run']] = row
        self.support = list(data.get('support_collections', [])) + list(workspace.get('support_collections', []))
        anchor = workspace.get('legacy_sequence_after')
        self.legacy_anchor = int(anchor[4:]) if anchor else None

    def connection(self, run):
        row = self.runs.get(run)
        return row['connection'] if row else None

    def predates_plans(self, run, connection):
        row = self.runs.get(run)
        if row:
            return row['connection'] == connection and row.get('relationship') == 'predates research plans'
        # Only the migrated single-project configuration still uses its numeric anchor.
        return self.legacy_anchor is not None and int(run[4:]) <= self.legacy_anchor
