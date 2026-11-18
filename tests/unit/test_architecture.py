"""The Import Linter contracts hold, each can be shown to fail, and what they cannot say is checked here.

A contract that cannot fail proves nothing. Every contract in pyproject.toml is paired with at
least one violation: the package is copied, one import is appended to one module, and that
contract, by id, must break. The remaining tests cover properties the contract language has no
form for: that every module has a layer, that the executor's hook routing imports the standard
library and nothing else, that every third-party import is a declared dependency, and that the
formatter is never pointed at a byte-sensitive file.
"""

import ast
from concurrent.futures import ThreadPoolExecutor
from importlib import metadata
from pathlib import Path
import pkgutil
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest

import berman
from berman.archive import builder
from berman.installation import PACKAGE
from tests.support.paths import REPO

CONFIG = REPO / 'pyproject.toml'
SETTINGS = tomllib.loads(CONFIG.read_text())
CONTRACTS = {c['id']: c for c in SETTINGS['tool']['importlinter']['contracts']}
# (contract id, module to change, import to append). Each must break the named contract.
VIOLATIONS = (
    ('layers', 'config.py', 'from berman import controller'),
    ('layers', 'capture/claude.py', 'from berman import controller'),
    ('layers', 'archive/builder.py', 'from berman import controller'),
    ('layers', 'files.py', 'from berman import installation'),
    ('layers', 'schemas/__init__.py', 'from berman import files'),
    ('layers', 'checks.py', 'from berman import verify'),
    ('layers', 'protocol.py', 'from berman import custody'),
    ('hooks', 'files.py', 'import jsonschema'),
    ('hooks', 'files.py', 'import packaging.requirements'),
    ('hooks', 'capture/claude.py', 'from berman import schemas'),  # Indirectly, through the confining module.
    ('hooks', 'executor/capture.py', 'from berman import recipes'),  # Indirectly, two modules away.
    ('archive', 'archive/package.py', 'import packaging'),
    ('confined', 'verify.py', 'import jsonschema'),
    ('confined', 'cli.py', 'import packaging'),
)
RUNNER = """
import sys
sys.path.insert(0, sys.argv[1])
from importlinter import cli
raise SystemExit(cli.lint_imports(config_filename=sys.argv[2], limit_to_contracts=tuple(sys.argv[3:]),
                                  no_cache=True, no_logo=True))
"""
# Files whose bytes an edition's reader identity names.
BYTE_SENSITIVE = {'archive/' + name for name in builder.BUILDER}


def hook_path():
    """Every file of the modules the hooks contract names: the executor's routing and all it can import."""
    found = set()
    for module in CONTRACTS['hooks']['source_modules']:
        target = PACKAGE.joinpath(*module.split('.')[1:])
        found.update(target.rglob('*.py') if target.is_dir() else [target.with_suffix('.py')])
    return sorted(path.relative_to(PACKAGE).as_posix() for path in found)


def lint(parent, *contracts):
    """Run the contracts against the berman package found in parent; (exit code, output)."""
    done = subprocess.run(
        [sys.executable, '-I', '-B', '-c', RUNNER, str(parent), str(CONFIG), *contracts],
        capture_output=True,
        text=True,
    )
    return done.returncode, done.stdout + done.stderr


def violated(case):
    """The named contract's result for a copy of the package with one import appended."""
    contract, relative, statement = case
    with tempfile.TemporaryDirectory() as temporary:
        copy = Path(temporary) / 'berman'
        shutil.copytree(PACKAGE, copy, ignore=shutil.ignore_patterns('__pycache__'))
        with (copy / relative).open('a') as stream:
            stream.write('\n' + statement + '\n')
        return lint(temporary, contract)


def top_level_imports(path):
    """The first component of every module a file imports, wherever the import statement is."""
    names = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names.update(alias.name.split('.')[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names.add(node.module.split('.')[0])
    return names


def modules():
    return sorted(m.name for m in pkgutil.walk_packages(berman.__path__, 'berman.'))


class ContractTests(unittest.TestCase):
    def test_contracts_hold_for_the_package(self):
        code, output = lint(PACKAGE.parent)
        self.assertEqual(code, 0, output)
        self.assertIn(f'Contracts: {len(CONTRACTS)} kept, 0 broken.', output)

    def test_each_contract_breaks_when_violated(self):
        self.assertEqual({case[0] for case in VIOLATIONS}, set(CONTRACTS), 'every contract needs a violation')
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(violated, VIOLATIONS))
        for case, (code, output) in zip(VIOLATIONS, results, strict=True):
            contract, relative, statement = case
            with self.subTest(contract=contract, module=relative, statement=statement):
                self.assertEqual(code, 1, output)
                self.assertIn(CONTRACTS[contract]['name'].split()[0], output)
                self.assertIn('Contracts: 0 kept, 1 broken.', output)
                guidance = CONTRACTS[contract]['broken_contract_guidance'].strip().splitlines()[0]
                self.assertIn(' '.join(guidance.split()), ' '.join(output.split()))

    def test_every_module_has_a_layer(self):
        layered = set(re.split(r'\s*[|:]\s*', ' | '.join(CONTRACTS['layers']['layers'])))
        for name in modules():
            if any(layer.startswith(name + '.') for layer in layered):
                continue  # A package whose members are layered one by one.
            with self.subTest(module=name):
                self.assertTrue(
                    any(name == layer or name.startswith(layer + '.') for layer in layered),
                    f'{name} is in no layer of the layers contract',
                )


class StandardLibraryTests(unittest.TestCase):
    def test_hook_routing_imports_only_the_standard_library(self):
        # The contracts name the packages Berman depends on; this refuses every other one too.
        files = hook_path()
        self.assertIn('executor/capture.py', files)
        self.assertIn('capture/codex.py', files)
        for relative in files:
            with self.subTest(file=relative):
                self.assertLessEqual(top_level_imports(PACKAGE / relative), set(sys.stdlib_module_names) | {'berman'})

    def test_third_party_imports_are_declared_dependencies(self):
        declared = {re.match(r'[A-Za-z0-9._-]+', r).group().lower() for r in metadata.requires('berman') or []}
        distributions = metadata.packages_distributions()
        for path in sorted(PACKAGE.rglob('*.py')):
            foreign = top_level_imports(path) - set(sys.stdlib_module_names) - {'berman'}
            for name in sorted(foreign):
                with self.subTest(file=path.relative_to(PACKAGE).as_posix(), module=name):
                    owners = {d.lower() for d in distributions.get(name, [])}
                    self.assertTrue(owners & declared, f'{name} is not provided by a declared dependency')


class FormatterTests(unittest.TestCase):
    def test_byte_sensitive_files_are_never_formatter_targets(self):
        ruff = SETTINGS['tool']['ruff']
        exempt = set(ruff['format']['exclude'])
        for relative in sorted(BYTE_SENSITIVE):
            with self.subTest(file=relative):
                self.assertTrue((PACKAGE / relative).is_file())
                self.assertIn('src/berman/' + relative, exempt)

    def test_exemptions_name_existing_files(self):
        ruff = SETTINGS['tool']['ruff']
        for name in [*ruff['format']['exclude'], *ruff['lint'].get('per-file-ignores', {})]:
            with self.subTest(file=name):
                self.assertTrue((REPO / name).is_file(), f'{name} is exempted but does not exist')


if __name__ == '__main__':
    unittest.main()
