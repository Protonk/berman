"""Structural contracts for the records Berman reads, as packaged JSON Schemas.

A record's `schema` value names its family and version, and that name selects one schema
file beside this module. A schema refuses only structure Berman's code would crash on or
silently misread. A field the code reads as a path, iterates or indexes must have the JSON
type that reading needs, and a record must have the fields that loading it reads. A field the
code converts, such as a timeout or a step identity, is accepted in every form the conversion
takes. A field Berman only stores or prints is not constrained, and null is read as absent.
A schema describes one supported record family. The controller and portable readers also
check agreement between records, custody receipts, inventories and observed bytes.

Validation returns the value it was given. Missing data and invalid data remain distinct.

Any release may deliberately tighten validation, with documented adoption
consequences and regression tests. Historical facts never change meaning silently; use a new
schema name for a materially different record meaning. Validation never rewrites old evidence.

This is the only module that imports jsonschema, and it does so when first used, so that a
command which validates nothing, and `berman verify --software` in a damaged environment,
still start. The executor's hook path and the archive builder read their records directly and
never come here; pyproject.toml's Import Linter contracts hold them to that.
"""

from functools import cache
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).parent
SUPPORTED = (
    'berman.connection/1',
    'berman.recipe/2',
    'berman.research-plan/1',
    'berman.software/1',
    'berman.verification/1',
    'berman.recording/2',
    'berman.capture-current/1',
    'berman.reading-edition/2',
)
KINDS = {
    'object': 'an object',
    'array': 'an array',
    'string': 'a string',
    'integer': 'an integer',
    'number': 'a number',
    'boolean': 'a boolean',
    'null': 'null',
}
SHOWN = 8  # Problems named in one message; the rest are counted.


class InvalidRecord(ValueError):
    """A value does not have the structure its schema requires; `problems` names each place."""

    def __init__(self, name, problems, source=None):
        self.schema, self.problems, self.source = name, tuple(problems), source
        shown = '; '.join(self.problems[:SHOWN])
        if len(self.problems) > SHOWN:
            shown += f'; and {len(self.problems) - SHOWN} more'
        super().__init__(
            f'{source} does not satisfy {name}: {shown}' if source else f'does not satisfy {name}: {shown}'
        )


def path(name):
    """The packaged file for a supported schema name: berman.recipe/2 is recipe-2.json."""
    if name not in SUPPORTED:
        raise KeyError(f'No packaged schema is named {name}')
    return HERE / (name.removeprefix('berman.').replace('/', '-') + '.json')


@cache
def schema(name):
    return json.loads(path(name).read_text(encoding='utf-8'))


class Unavailable(ValueError):
    """Records cannot be checked in this environment, because jsonschema does not import.

    A ValueError on purpose. Every reader then treats it as it treats a record it cannot trust:
    the command line reports it, verification fails the check, and discovery leaves the run's facts
    unknown. Nothing is passed over unchecked.
    """


@cache
def validator(name):
    try:
        from jsonschema import Draft202012Validator
    except Exception as exc:  # Broken installations can raise more than ImportError while loading.
        raise Unavailable(
            f'Records cannot be checked: jsonschema does not import ({exc}). Reinstall Berman with its '
            'dependencies; berman verify --software reports the environment'
        ) from exc
    Draft202012Validator.check_schema(schema(name))
    return Draft202012Validator(schema(name))


def kind(value):
    if value is None:
        return 'null'
    if isinstance(value, bool):
        return 'boolean'
    if isinstance(value, dict):
        return 'object'
    if isinstance(value, list):
        return 'array'
    if isinstance(value, str):
        return 'string'
    return 'integer' if isinstance(value, int) else 'number'


def found(value):
    """What a value is, with a short rendering of a scalar: an integer (12)."""
    name = kind(value)
    if name in ('object', 'array', 'null'):
        return KINDS[name]
    text = json.dumps(value, ensure_ascii=False)
    return f'{KINDS[name]} ({text if len(text) <= 40 else text[:37] + "..."})'


def location(parts):
    """A field's place as an operator would write it: connections.stewart.checks[0].argv."""
    text = ''
    for part in parts:
        text += f'[{part}]' if isinstance(part, int) else ('.' if text else '') + str(part)
    return text


def describe(error):
    """(place, plain statement) for each thing a jsonschema error reports; never the whole offending value."""
    place = list(error.absolute_path)
    subject = location(place) or 'the document'
    rule, expected, value = error.validator, error.validator_value, error.instance
    if rule == 'required':
        return [(place + [name], f'{location(place + [name])} is required') for name in expected if name not in value]
    if rule == 'type':
        kinds = [expected] if isinstance(expected, str) else expected
        text = f'{subject} must be {" or ".join(KINDS[k] for k in kinds)}, not {found(value)}'
    elif rule == 'const':
        text = f'{subject} must be {json.dumps(expected)}, not {found(value)}'
    elif rule == 'enum':
        text = f'{subject} must be one of {", ".join(json.dumps(e) for e in expected)}, not {found(value)}'
    elif rule == 'pattern':
        text = f'{subject} must be {error.schema.get("description", "text matching " + expected)}, not {found(value)}'
    elif rule in ('minLength', 'minItems'):
        text = f'{subject} must not be empty'
    else:
        text = f'{subject}: {error.message if len(error.message) <= 120 else error.message[:117] + "..."}'
    return [(place, text)]


def printable(text):
    """Text that any stream can encode: a lone surrogate read from a record is written as an escape."""
    return text.encode('utf-8', 'backslashreplace').decode('utf-8')


def problems(name, value):
    """Every structural problem in value, ordered by the field it names; empty when the schema is satisfied."""
    found_problems = [item for error in validator(name).iter_errors(value) for item in describe(error)]
    # Array positions order as numbers and precede names, so steps[2] comes before steps[10].
    found_problems.sort(
        key=lambda item: ([(0, part, '') if isinstance(part, int) else (1, 0, part) for part in item[0]], item[1])
    )
    return list(dict.fromkeys(printable(text) for _, text in found_problems))


def validate(name, value, source=None):
    """The same value, unchanged, or InvalidRecord naming source and each problem."""
    found_problems = problems(name, value)
    if found_problems:
        raise InvalidRecord(name, found_problems, source)
    return value


def check():
    """Each packaged schema's digest, after confirming it is well formed and titled with its own name.

    For installation verification. Raises ValueError when a schema file is missing, malformed, not
    a valid Draft 2020-12 schema, or names another family.
    """
    digests = {}
    for name in SUPPORTED:
        try:
            data = path(name).read_bytes()
            loaded = schema(name)
            validator(name)
        except Exception as exc:  # jsonschema's own error type is not imported at module level.
            raise ValueError(f'Packaged schema {name} cannot be used: {type(exc).__name__}: {exc}') from exc
        if loaded.get('title') != name or loaded['properties']['schema'].get('const') != name:
            raise ValueError(f'Packaged schema {path(name).name} does not declare {name}')
        digests[name] = hashlib.sha256(data).hexdigest()
    unlisted = sorted(p.name for p in HERE.glob('*.json') if p not in {path(n) for n in SUPPORTED})
    if unlisted:
        raise ValueError(f'Schema files without a supported name: {", ".join(unlisted)}')
    return digests
