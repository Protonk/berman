"""Guest study identifiers and research plan sequencing."""

import re

from berman import schemas
from berman.files import Decision, digest_json, read

IDENTITY = re.compile(r'[a-z][a-z0-9-]{0,62}')


class Complete(Decision):
    """Every step of a research plan has been started; another choice must be recorded."""


def identity(value, kind):
    if not isinstance(value, str) or not IDENTITY.fullmatch(value):
        raise ValueError(f'Invalid {kind} identity: {value!r}')
    return value


class Plan:
    """Which engagements are wanted for one connection, and in what order."""

    def __init__(self, root, value):
        self.path = (root / value).resolve()
        if not self.path.is_relative_to(root):
            raise ValueError(f'Research plans are researcher inputs held in Berman: {self.path}')
        self.data = read(self.path)
        schemas.validate('berman.research-plan/1', self.data, self.path)
        self.root = root
        self.id = identity(self.data['id'], 'research plan')
        self.connection = identity(self.data['connection'], 'connection')
        self.steps = self.data['steps']
        self.ids = [s['id'] for s in self.steps]
        if len(self.ids) != len(set(self.ids)):
            raise ValueError('Duplicate instruction-sequence step')

    def reservation(self, step):
        return {
            'id': self.id,
            'path': str(self.path.relative_to(self.root)),
            'digest': digest_json(self.data),
            'step': step['id'],
        }

    def next_step(self, started):
        """Each started commission claims one step, and started steps must form a prefix."""
        if len(started) != len(set(started)) or any(step not in self.ids for step in started):
            raise Decision('Conflicting sequence claim; inspect the run records')
        if set(started) != set(self.ids[: len(started)]):
            raise Decision('The recorded fieldwork roles were started out of sequence; inspect their relationship')
        if len(started) == len(self.steps):
            raise Complete(f'The agreed fieldwork sequence is complete (plan {self.id})')
        return self.steps[len(started)]
