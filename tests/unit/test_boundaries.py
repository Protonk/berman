"""Architectural contracts and observable compatibility, independent of old filenames."""
import ast
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from berman import config, files, records, recovery
from berman.capture import collector
from berman._legacy import api
from berman.installation import PACKAGE, FROZEN, FROZEN_SHA256

frozen = collector.capture
LEGACY = {'RUN', 'SESSION', 'CODEX_HOME'}


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
        for module in (records, recovery, config):
            self.assertFalse(hasattr(module, 'capture'), f'{module.__name__} re-exports the frozen collector')
        for name in ('fieldwork', 'keep'):
            self.assertFalse(hasattr(records, name), f'berman_records re-exports {name}')


    def test_frozen_bytes_are_preserved(self):
        self.assertEqual(hashlib.sha256((PACKAGE / FROZEN).read_bytes()).hexdigest(), FROZEN_SHA256)


class ImportBoundaryTests(unittest.TestCase):
    def test_hook_dependencies_are_fingerprinted(self):
        from berman.capture.adapters import ADAPTERS

        def dependencies(relative, seen):
            if relative in seen:
                return
            seen.add(relative)
            path = PACKAGE / relative
            for parent in path.parents:
                if not parent.is_relative_to(PACKAGE):
                    break
                initializer = parent / '__init__.py'
                if initializer.is_file():
                    dependencies(initializer.relative_to(PACKAGE).as_posix(), seen)
            for module in imports(path):
                target = PACKAGE / module.removeprefix('berman.').replace('.', '/')
                file = target.with_suffix('.py') if target.with_suffix('.py').is_file() else target / '__init__.py'
                dependencies(file.relative_to(PACKAGE).as_posix(), seen)

        for name, adapter in ADAPTERS.items():
            reached = set()
            dependencies(adapter['entry'], reached)
            self.assertLessEqual(reached, set(adapter['implementation']), name)

    def test_package_boundaries(self):
        for path in PACKAGE.rglob('*.py'):
            relative = path.relative_to(PACKAGE).as_posix()
            reached = imports(path)
            if 'berman._legacy.capture_session' in reached:
                self.assertEqual(relative, 'capture/collector.py')
            if relative.startswith('archive/'):
                self.assertTrue(all(m.startswith('berman.archive') for m in reached), relative)
            if relative in ('files.py', 'harnesses/probe.py', 'harnesses/development/probe.py'):
                self.assertFalse(reached, relative)
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

    def test_low_level_command_imports_under_isolation(self):
        result = subprocess.run([sys.executable, '-I', '-B', '-m', 'berman.recovery', '--help'],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
