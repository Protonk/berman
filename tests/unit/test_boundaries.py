"""Filesystem, import and isolated entry point checks."""
import ast
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import unittest

from berman import files
from berman.installation import PACKAGE
from tests.support.paths import REPO



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


class FileTests(unittest.TestCase):
    def test_files_refuse_linked_sources_and_destinations(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp).resolve()
            real = base / 'original'
            files.write(real, {'key': 'é'})
            (base / 'link').symlink_to(real)
            (base / 'linked').symlink_to(base, target_is_directory=True)
            for path in (base / 'link', base / 'linked/original'):
                with self.subTest(path=path), self.assertRaises(ValueError):
                    files.regular(path)
                with self.assertRaises(ValueError):
                    files.atomic(path, b'changed')
            self.assertEqual(files.read(real), {'key': 'é'})

    def test_recorded_digest_form_is_unchanged(self):
        value = {'b': 1, 'a': ['é', None, 2.5]}
        self.assertEqual(files.digest_json(value),
                         'ccd45ad6c988822e6b70e8a208a206dfec0cdb5b54f156f9cb32e38040adac8e')
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
        self.assertIn('capture/codex.py', reached)
        for relative in sorted(reached):
            name = 'berman.' + relative.removesuffix('.py').removesuffix('/__init__').replace('/', '.')
            with self.subTest(module=name):
                self.assertTrue(any(name == module or name.startswith(module + '.') for module in held))

    def test_runtime_modules_never_reach_the_test_suite_or_edit_the_import_path(self):
        # Which module may import which is stated by the Import Linter contracts in pyproject.toml;
        # tests/unit/test_architecture.py shows that each of them can fail.
        for path in PACKAGE.rglob('*.py'):
            relative = path.relative_to(PACKAGE).as_posix()
            self.assertNotIn('sys.path.insert', path.read_text(), relative)
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.ImportFrom):
                    self.assertFalse((node.module or '').startswith('tests'), relative)
                elif isinstance(node, ast.Import):
                    self.assertFalse(any(a.name.startswith('tests') for a in node.names), relative)



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
