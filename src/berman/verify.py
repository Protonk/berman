"""Verify the installed software and its runtime dependencies."""

import hashlib
import json
import platform
from berman import environment, schemas
from berman.installation import PACKAGE, version

SOURCE = ('.py', '.json')  # Modules and the packaged record schemas.


def package_identity():
    paths = sorted(p for p in PACKAGE.rglob('*') if p.suffix in SOURCE and p.is_file())
    hashes = {p.relative_to(PACKAGE).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    return {
        'schema': 'berman.software/1',
        'version': version(),
        'source_sha256': hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest(),
        'files_sha256': hashes,
    }


def identity():
    """Installed Berman bytes and the interpreter and dependency versions running them."""
    return {
        **package_identity(),
        'environment': {'python': platform.python_version(), 'dependencies': environment.inspect()[0]},
    }


def dependencies():
    installed, problems = environment.inspect()
    problems.extend(f'{name} is missing' for name, version in installed.items() if version is None)
    if problems:
        raise ValueError(', '.join(problems))
    return ', '.join(f'{name} {version}' for name, version in installed.items())


def software():
    """Read the installed package, evaluate requirements and load each commission schema."""

    def installation():
        package = package_identity()
        return f'Berman {package["version"]} ({package["source_sha256"]})'

    checks = {}
    for name, check in [
        ('installation', installation),
        ('dependencies', dependencies),
        ('schemas', lambda: ', '.join(schemas.check())),
    ]:
        try:
            checks[name] = {'passed': True, 'detail': check()}
        except Exception as exc:  # Damaged third-party metadata and modules can raise their own exceptions.
            checks[name] = {'passed': False, 'detail': f'{type(exc).__name__}: {exc}'}
    return {
        'schema': 'berman.software-verification/1',
        'passed': all(c['passed'] for c in checks.values()),
        'checks': checks,
    }
