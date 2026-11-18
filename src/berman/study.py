"""A study's inputs in the guest: identities, recipes and plans.

The controller reads a recipe, and a plan when one is selected, as it commissions an engagement.
Only the recipe's instructions travel to the host; the plan and a recipe's annotations stay here.
"""

from pathlib import Path
import re

from berman import schemas
from berman.files import Decision, digest_json, read, regular

IDENTITY = re.compile(r'[a-z][a-z0-9-]{0,62}')


class Complete(Decision):
    """Every step of a plan has been started; another choice must be recorded."""


def identity(value, kind):
    if not isinstance(value, str) or not IDENTITY.fullmatch(value):
        raise ValueError(f'Invalid {kind} identity: {value!r}')
    return value


class Plan:
    """Which engagements are wanted for one connection, and in what order."""

    def __init__(self, root, value):
        self.path = (root / value).resolve()
        if not self.path.is_relative_to(root):
            raise ValueError(f'Plans are study inputs held in the guest: {self.path}')
        self.data = read(self.path)
        schemas.validate('berman.research-plan/1', self.data, self.path)
        self.root = root
        self.id = identity(self.data['id'], 'plan')
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


def recipe(root, name, connection):
    """Recipe 2 wording is written for named connections; its instructions are all a recipe says to a fieldworker."""
    if not re.fullmatch(r'[a-z][a-z0-9-]*', name):
        raise ValueError('Invalid recipe name')
    folder = root / 'recipes' / name
    meta = read(folder / 'recipe.json')
    schemas.validate('berman.recipe/2', meta, folder / 'recipe.json')
    if meta['id'] != name:
        raise ValueError('Recipe identity differs from its directory')
    if meta['connections'] and connection not in meta['connections']:
        raise Decision(
            f'Recipe {name} is worded for {", ".join(meta["connections"])}. Select a recipe for {connection}'
        )
    path = Path(meta['instructions'])
    if path.is_absolute() or '..' in path.parts:
        raise ValueError('Recipe file must stay within its directory')
    instructions = regular(folder / path).read_text()
    if instructions.count('{{notes_directory}}') != 1:
        raise ValueError('Instructions must contain exactly one notes_directory placeholder')
    return {'metadata': meta, 'instructions': instructions}
