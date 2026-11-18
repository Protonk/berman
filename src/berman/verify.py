"""Verify the installed software and its runtime dependencies."""

import hashlib
import json
import platform
import importlib
from berman import environment, files, schemas
from berman.installation import PACKAGE, version

EXIT = {'passed': 0, 'failed': 1, 'incomplete': 3}
PROBLEMS = (OSError, ValueError, KeyError, TypeError, AttributeError)
MEANING = {
    'installation': 'Installed package metadata and runtime resources can be read; no study is loaded.',
    'dependencies': 'Active runtime requirements, including transitive versions and Python constraints, are satisfied, '
    'and the dependency modules Berman uses import. Their versions '
    'are environment facts: nothing fingerprints them and no qualification rests on them.',
    'schemas': 'The packaged record schemas are present, well formed and named for their own record families.',
}


SOURCE = ('.py', '.json')  # Modules and the packaged record schemas.


def identity():
    """Digests of Berman's own installed files, and beside them the environment they run in.

    source_sha256 and files_sha256 identify bytes. environment names the interpreter and
    dependency releases found at this moment: recorded context, not a fingerprint. A dependency
    resolved at install time can differ between two installations of one wheel.
    """
    paths = sorted(p for p in PACKAGE.rglob('*') if p.suffix in SOURCE and p.is_file())
    hashes = {
        p.relative_to(PACKAGE).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in paths
        if not p.name.startswith('test_')
    }
    return {
        'schema': 'berman.software/1',
        'version': version(),
        'source_sha256': hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest(),
        'files_sha256': hashes,
        'environment': {'python': platform.python_version(), 'dependencies': environment.inspect()[0]},
    }


def row(status, summary, required=True, step=None, **details):
    result = {'status': status, 'required': required, 'summary': summary}
    if step:
        result['next'] = step
    result['details'] = details
    return result


def verdict(checks):
    if any(c['status'] == 'failed' for c in checks.values()):
        return 'failed'
    if any(c['required'] and c['status'] != 'passed' for c in checks.values()):
        return 'incomplete'
    return 'passed'


def dependencies():
    """Check requirements and explicit API imports, even when installed files have vanished."""
    try:
        installed, broken = environment.inspect()
    except Exception as exc:
        installed, broken = {}, [f'{type(exc).__name__}: {exc}']
    missing = sorted(name for name, version in installed.items() if version is None)
    # Diagnostic imports only; state computation never uses these libraries directly. Do not
    # discover names from installed files: deleting a package must not erase the check itself.
    required = {
        'jsonschema': {'jsonschema': ('Draft202012Validator',)},
        'rich': {
            'rich': (),
            'rich.console': ('Console',),
            'rich.padding': ('Padding',),
            'rich.text': ('Text',),
        },
    }
    for name, modules in required.items():
        try:
            for module, attributes in modules.items():
                loaded = importlib.import_module(module)
                for attribute in attributes:
                    if not callable(getattr(loaded, attribute)):
                        raise TypeError(f'{module}.{attribute} is not callable')
        except Exception as exc:  # A damaged binary wheel fails here in its own way.
            broken.append(f'{name}: {type(exc).__name__}: {exc}')
    details = {'installed': installed, 'missing': missing, 'problems': broken}
    if missing or broken:
        return row(
            'failed',
            f'{len(missing) + len(broken)} runtime dependency problem(s)',
            step='Reinstall the package with its dependencies',
            **details,
        )
    return row(
        'passed', ', '.join(f'{name} {version}' for name, version in installed.items()) or 'none declared', **details
    )


def packaged_schemas():
    try:
        digests = schemas.check()
    except PROBLEMS + (ImportError,) as exc:
        return row('failed', str(exc), step='Reinstall the package; schema files are part of it')
    return row('passed', f'{len(digests)} record schemas are packaged and well formed', sha256=digests)


def software():
    """Verify installed package resources; never load a workspace or harness home."""
    try:
        installed = identity()
        checks = {'installation': row('passed', f'Berman {installed["version"]} is installed', software=installed)}
    except Exception as exc:  # Third-party metadata and parser failures are installation evidence.
        checks = {'installation': row('failed', f'Installed package cannot be read: {exc}')}
    checks['dependencies'] = dependencies()
    checks['schemas'] = packaged_schemas()
    for name, check in checks.items():
        check['meaning'] = MEANING[name]
    return {
        'schema': 'berman.verification/1',
        'scope': 'software',
        'verdict': verdict(checks),
        'checked_at_utc': files.now(),
        'root': None,
        'code': str(PACKAGE),
        'checks': checks,
        'failed': [n for n, c in checks.items() if c['status'] == 'failed'],
        'not_run': [n for n, c in checks.items() if c['status'] == 'not-run'],
        'not_established': [],
        'not_covered': [
            'Developer regression tests (run separately from the source checkout).',
            'Guest study records, preserved recordings and reading editions.',
            'Native harness execution, hook trust and native capture.',
        ],
        'record': None,
        'meaning': 'Checks the installed software, its dependencies and its packaged schemas.',
    }
