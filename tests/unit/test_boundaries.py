"""Architectural contracts and observable compatibility, independent of old filenames."""
import ast
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import unittest

from berman import config, files, records
from berman.capture import collector
from berman._legacy import api
from berman.installation import PACKAGE, FROZEN, FROZEN_SHA256
from tests.support.paths import REPO

frozen = collector.capture
LEGACY = {'RUN', 'SESSION'}


def imports(path):
    """Resolve Berman import targets; ignore imported values that are not modules."""
    result = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            result.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.add(node.module)
            result.update(node.module + '.' + a.name for a in node.names)
    return {m for m in result if m.startswith('berman.') and
            ((PACKAGE / (m.removeprefix('berman.').replace('.', '/') + '.py')).is_file()
             or (PACKAGE / m.removeprefix('berman.').replace('.', '/') / '__init__.py').is_file())}


class CompatibilityTests(unittest.TestCase):
    def test_outputs_match_on_fixtures(self):
        value = {'ü': [1, 2.5, None, True, False], 'nested': {'z': 'é', 'a': []}}
        self.assertEqual(files.json_bytes(value), frozen.json_bytes(value))
        self.assertEqual(files.digest(b'bytes'), frozen.digest(b'bytes'))
        self.assertRegex(files.now(), r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d{6})?Z$')
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            frozen.atomic(base / 'frozen.json', frozen.json_bytes(value))
            files.write(base / 'shared.json', value)
            self.assertEqual((base / 'shared.json').read_bytes(), (base / 'frozen.json').read_bytes())
            self.assertEqual(files.read(str(base / 'shared.json')), frozen.read_json(base / 'frozen.json'))
            files.keep(base / 'shared.json', frozen.json_bytes(value))
            with self.assertRaisesRegex(files.Decision, 'Existing artifact differs'):
                files.keep(base / 'shared.json', b'other')
            (base / 'link').symlink_to(base / 'frozen.json')
            (base / 'linked').symlink_to(base, target_is_directory=True)
            for module in (files, frozen):
                with self.assertRaisesRegex(ValueError, 'Expected a regular file'):
                    module.regular(base / 'link')
                with self.assertRaisesRegex(ValueError, 'Symlink in capture path'):
                    module.regular(base / 'linked/frozen.json')
                with self.assertRaisesRegex(ValueError, 'Symlink in capture destination'):
                    module.atomic(base / 'link', b'')
                with self.assertRaisesRegex(ValueError, 'Symlink in capture destination'):
                    module.atomic(base / 'linked/new.json', b'')
            self.assertEqual(sorted(p.name for p in base.iterdir()), ['frozen.json', 'link', 'linked', 'shared.json'])


    def test_recorded_digest_form_is_unchanged(self):
        value = {'b': 1, 'a': ['é', None, 2.5]}
        self.assertEqual(files.digest_json(value),
                         'ccd45ad6c988822e6b70e8a208a206dfec0cdb5b54f156f9cb32e38040adac8e')
        self.assertEqual(files.digest_json(value),
                         frozen.digest(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()))


    def test_frozen_interface_is_narrow_and_shares_one_instance(self):
        public = {k for k in vars(api) if not k.startswith('_')}
        self.assertEqual(public, LEGACY)
        for name in public:
            self.assertIs(getattr(api, name), getattr(frozen, name), name)
        self.assertIs(config.Decision, files.Decision)
        self.assertIs(records.Decision, files.Decision)
        self.assertTrue(issubclass(config.Complete, files.Decision))
        for module in (records, config):
            self.assertFalse(hasattr(module, 'capture'), f'{module.__name__} re-exports the frozen collector')
        for name in ('fieldwork', 'keep', 'write'):
            self.assertFalse(hasattr(records, name), f'berman.records holds {name}, and it only reads')


    def test_frozen_bytes_are_preserved(self):
        self.assertEqual(hashlib.sha256((PACKAGE / FROZEN).read_bytes()).hexdigest(), FROZEN_SHA256)


class ImportBoundaryTests(unittest.TestCase):
    def test_everything_hook_routing_imports_is_held_to_the_hooks_contract(self):
        # The executor's router runs on every turn of a fieldworker's session. Whatever it can import,
        # at any depth and wherever the import statement is, must be named by the contract that keeps
        # third-party packages out.
        contracts = tomllib.loads((REPO / 'pyproject.toml').read_text())['tool']['importlinter']['contracts']
        held = next(c for c in contracts if c['id'] == 'hooks')['source_modules']

        def dependencies(relative, seen):
            if relative in seen:
                return
            seen.add(relative)
            for module in imports(PACKAGE / relative):
                target = PACKAGE / module.removeprefix('berman.').replace('.', '/')
                file = target.with_suffix('.py') if target.with_suffix('.py').is_file() else target / '__init__.py'
                dependencies(file.relative_to(PACKAGE).as_posix(), seen)

        reached = set()
        dependencies('executor/capture.py', reached)
        self.assertIn(FROZEN, reached)
        for relative in sorted(reached):
            name = 'berman.' + relative.removesuffix('.py').removesuffix('/__init__').replace('/', '.')
            with self.subTest(module=name):
                self.assertTrue(any(name == module or name.startswith(module + '.') for module in held))

    def test_runtime_modules_never_reach_the_test_suite_or_edit_the_import_path(self):
        # Which module may import which is stated by the Import Linter contracts in pyproject.toml;
        # tests/unit/test_architecture.py shows that each of them can fail.
        for path in PACKAGE.rglob('*.py'):
            relative = path.relative_to(PACKAGE).as_posix()
            if relative != FROZEN:
                self.assertNotIn('sys.path.insert', path.read_text(), relative)
                for node in ast.walk(ast.parse(path.read_text())):
                    if isinstance(node, ast.ImportFrom):
                        self.assertFalse((node.module or '').startswith('tests'), relative)
                    elif isinstance(node, ast.Import):
                        self.assertFalse(any(a.name.startswith('tests') for a in node.names), relative)

    def test_importing_runtime_modules_loads_the_frozen_collector_once(self):
        code = """
import importlib, json, pkgutil, sys, berman
for module in pkgutil.walk_packages(berman.__path__, 'berman.'):
    if module.name != 'berman.__main__':
        importlib.import_module(module.name)
from berman.capture import collector
copies = [m for m in sys.modules.values() if (getattr(m, '__file__', '') or '').endswith('/capture_session.py')]
print(json.dumps({'copies': len(copies), 'same': copies == [collector.capture]}))
"""
        result = subprocess.run([sys.executable, '-I', '-B', '-c', code], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {'copies': 1, 'same': True})

    def test_fieldwork_entry_points_import_under_isolation(self):
        for module in ('berman.executor', 'berman.controller', 'berman.connector', 'berman.custody'):
            with self.subTest(module=module):
                result = subprocess.run([sys.executable, '-I', '-B', '-m', module, '--help'],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_hook_entry_point_loads_no_third_party_package(self):
        code = """
import sys
before = set(sys.modules)
import berman.executor
print(sorted(m for m in set(sys.modules) - before if m.partition('.')[0] not in {*sys.stdlib_module_names, 'berman'}))
"""
        result = subprocess.run([sys.executable, '-I', '-B', '-c', code], capture_output=True, text=True)
        self.assertEqual((result.returncode, result.stdout.strip()), (0, '[]'), result.stderr)
