"""Shared helpers reproduce the frozen collector's, and modules reach it only through allowed paths.

The import rules are data, applied to every tools/*.py and development_launchers/*.py, so modules
added later are checked without editing this file. A fresh process that imports every module
confirms the rules at runtime, however the source is written.
"""
import ast
import builtins
import gc
import importlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import berman_collector
import berman_config
import berman_files
import berman_legacy
import berman_records
import fieldwork

TOOLS = Path(__file__).resolve().parent
TOOL_NAMES = {p.stem for p in TOOLS.glob('*.py')}
frozen = berman_collector.capture  # The one production instance of capture_session.py.
EQUIVALENT = ('now', 'digest', 'json_bytes', 'regular', 'read_json', 'atomic')
MISSING = object()
# Byte-verified with capture_session.py (berman_capture.ROUTER_IMPLEMENTATION); they keep their own loading.
CAPTURE_PATH = ('berman_capture_router', 'berman_capture_runtime', 'berman_collector')
OUTSIDE_CAPTURE = {'berman_files', 'berman_legacy', 'berman_records', 'fieldwork',
                   'berman_lifecycle', 'berman_verify', 'berman_support'}
# The archive builder runs as an -I script and is loaded by path; its modules use only each other.
ARCHIVE = ('build_archive', 'archive_select', 'archive_package', 'archive_stewart')
# Run directly under -I, inside the sandbox, or loaded by path: they import no tools module.
STANDALONE = ('berman_files', 'berman_probe', 'capture_session')
# Interpret what others read: besides the standard library they import only these tools modules.
LEAVES = {'berman_lifecycle': {'berman_legacy'}}
# The router re-exports the frozen module; outside the capture path it is a hook entry, never imported.
IMPORTED_ONLY_BY = {'berman_collector': {'berman_capture_runtime', 'berman_capture', 'berman_legacy'},
                    'fieldwork': {'berman_recipes', 'berman_preserve', 'berman_support', 'berman_claude_support'},
                    'berman_capture_router': set()}
# The only files, tests included, that import capture_session or execute it by path.
LOADS_FROZEN = {'berman_collector', 'test_capture_session', 'test_fieldwork'}
# Calls that execute a file's source; their arguments are searched through assigned names.
LOADERS = {'spec_from_file_location', 'SourceFileLoader', 'SourcelessFileLoader', 'run_path'}
LEGACY = {'MARKER', 'RUN', 'SESSION', 'CODEX_HOME'}
# Names production modules may take, by name or attribute; atomic stays qualified so interruption tests reach every caller.
TAKEN_ONLY = {'berman_records': {'ROOT', 'Store', 'closed_by'},
              'berman_capture_runtime': {'Collector', 'LegacyCollector'}, 'berman_legacy': LEGACY}
NEVER_TAKEN = {'berman_files': {'atomic'}}


def functions(path):
    return {n.name: n for n in ast.parse(path.read_text()).body if isinstance(n, ast.FunctionDef)}


def free_names(function):
    """Names a function reads from its module or the builtins: neither parameters nor assigned in it."""
    nodes = list(ast.walk(function))
    local = {n.arg for n in nodes if isinstance(n, ast.arg)}
    local |= {n.id for n in nodes if isinstance(n, ast.Name) and not isinstance(n.ctx, ast.Load)}
    return {n.id for n in nodes if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)} - local


def imports(path):
    """Modules a file imports anywhere, the names it takes from each, and the files it loads by path.

    Path loads count as imports, and a loader's path may arrive through assigned names. A tools
    module bound by a from-import, an assignment or an attribute counts as imported, and attributes
    read through it count as taken. taken holds names bound by from-imports and attributes read
    through a module; bound holds from-import names and attributes read other than to call them.
    """
    tree = ast.parse(path.read_text())
    nodes = list(ast.walk(tree))
    modules, aliases, taken, bound, loaded, assigned, scope = set(), {}, {}, {}, set(), {}, {}
    for node in nodes:  # Each node's enclosing function, or the module.
        inner = node if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)) else scope.get(node, tree)
        for child in ast.iter_child_nodes(node):
            scope[child] = inner
    assignments = [(n.targets if isinstance(n, ast.Assign) else [n.target], n.value) for n in nodes
                   if isinstance(n, (ast.Assign, ast.AnnAssign, ast.NamedExpr)) and n.value is not None]
    assignments += [([n.target], n.iter) for n in nodes if isinstance(n, (ast.For, ast.comprehension))]
    for targets, value in assignments:
        for target in targets:
            for name in ast.walk(target):
                if isinstance(name, ast.Name):
                    assigned.setdefault((scope[value], name.id), []).append(value)

    def constants(expressions):
        return {n.value for e in expressions for n in ast.walk(e) if isinstance(n, ast.Constant) and isinstance(n.value, str)}

    def reached(expressions, seen):
        """Constants in expressions and in every value the names they read are assigned in the nearest scope."""
        found = constants(expressions)
        for n in [n for e in expressions for n in ast.walk(e) if isinstance(n, ast.Name)]:
            where = scope[n]
            while (where, n.id) not in assigned and where is not tree:
                where = scope[where]
            if (where, n.id) not in seen:
                seen.add((where, n.id))
                found |= reached(assigned.get((where, n.id), ()), seen)
        return found

    def module(node):
        if isinstance(node, ast.Name):
            return aliases.get(node.id)
        if isinstance(node, ast.Attribute) and node.attr in TOOL_NAMES and module(node.value):
            return node.attr

    for node in nodes:
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name.split('.')[0])
                aliases[alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            modules.add(node.module.split('.')[0])
            names = {alias.name for alias in node.names}
            taken.setdefault(node.module, set()).update(names)
            bound.setdefault(node.module, set()).update(names)
            for alias in node.names:
                if alias.name in TOOL_NAMES:
                    modules.add(alias.name)
                    aliases[alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, 'id', None)
            arguments = [*node.args, *(keyword.value for keyword in node.keywords)]
            # Constants written in the call count as they are; those reached through names, only as tools modules.
            if name in LOADERS or (isinstance(func, ast.Name) and name in ('exec', 'compile')):
                loaded |= {Path(s).stem for s in constants(arguments) if s.endswith('.py')}
                loaded |= {Path(s).stem for s in reached(arguments, set()) if s.endswith('.py')} & TOOL_NAMES
            elif name in ('__import__', 'import_module', 'run_module') and node.args:
                modules |= {s.split('.')[0] for s in constants(node.args[:1])}
                modules |= {s.split('.')[0] for s in reached(node.args[:1], set())} & TOOL_NAMES
    changed = True
    while changed:
        changed = False
        for targets, value in assignments:
            source = module(value)
            for target in targets:
                if source and isinstance(target, ast.Name) and aliases.get(target.id) != source:
                    aliases[target.id] = source
                    changed = True
    called = {id(node.func) for node in nodes if isinstance(node, ast.Call)}
    for node in nodes:
        if isinstance(node, ast.Attribute) and (source := module(node.value)):
            taken.setdefault(source, set()).add(node.attr)
            if node.attr in TOOL_NAMES:
                modules.add(node.attr)
            if id(node) not in called:
                bound.setdefault(source, set()).add(node.attr)
    return modules | loaded, taken, bound, loaded


def frozen_copies():
    """Import every non-test tools module, then report what this process holds from capture_session.py."""
    for name in sorted(TOOL_NAMES):
        if not name.startswith('test_') and name != 'capture_session':
            importlib.import_module(name)
    objects = gc.get_objects()
    copies = [m for m in objects if isinstance(m, types.ModuleType)
              and (getattr(m, '__file__', None) or '').endswith('capture_session.py')]
    strays = [f'{f.__module__}.{f.__qualname__}' for f in objects if isinstance(f, types.FunctionType)
              and f.__code__.co_filename.endswith('capture_session.py') and f.__globals__ is not vars(frozen)]
    holders = [f'{name}.{key}' for name, module in sorted(sys.modules.items())
               if name in TOOL_NAMES and name not in CAPTURE_PATH and not name.startswith('test_')
               for key, value in vars(module).items()
               if value is frozen or getattr(value, '__module__', None) == frozen.__name__]
    print(json.dumps({'copies': len(copies), 'collector': all(m is frozen for m in copies),
                      'strays': sorted(strays), 'holders': holders}))


class FrozenEquivalenceTests(unittest.TestCase):
    def test_helpers_are_statement_for_statement_copies(self):
        old, new = functions(TOOLS / 'capture_session.py'), functions(TOOLS / 'berman_files.py')
        for name in EQUIVALENT:
            self.assertEqual(ast.dump(new[name]), ast.dump(old[name]), name)
            function = getattr(berman_files, name)
            self.assertEqual((function.__module__, function.__qualname__, function.__code__.co_firstlineno),
                             ('berman_files', name, new[name].lineno), f'berman_files.{name} is not its def')
            self.assertEqual(function.__code__.co_code, getattr(frozen, name).__code__.co_code, name)
            for free in free_names(new[name]) - set(EQUIVALENT):
                self.assertIs(vars(berman_files).get(free, vars(builtins).get(free, MISSING)),
                              vars(frozen).get(free, vars(builtins).get(free, MISSING)), f'{name} reads {free}')

    def test_outputs_match_on_fixtures(self):
        value = {'ü': [1, 2.5, None, True, False], 'nested': {'z': 'é', 'a': []}}
        self.assertEqual(berman_files.json_bytes(value), frozen.json_bytes(value))
        self.assertEqual(berman_files.digest(b'bytes'), frozen.digest(b'bytes'))
        self.assertRegex(berman_files.now(), r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d{6})?Z$')
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            frozen.atomic(base / 'frozen.json', frozen.json_bytes(value))
            berman_files.write(base / 'shared.json', value)
            self.assertEqual((base / 'shared.json').read_bytes(), (base / 'frozen.json').read_bytes())
            self.assertEqual(berman_files.read(str(base / 'shared.json')), frozen.read_json(base / 'frozen.json'))
            berman_files.keep(base / 'shared.json', frozen.json_bytes(value))
            with self.assertRaisesRegex(berman_files.Decision, 'Existing artifact differs'):
                berman_files.keep(base / 'shared.json', b'other')
            (base / 'link').symlink_to(base / 'frozen.json')
            (base / 'linked').symlink_to(base, target_is_directory=True)
            for module in (berman_files, frozen):
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
        self.assertEqual(berman_files.digest_json(value),
                         'ccd45ad6c988822e6b70e8a208a206dfec0cdb5b54f156f9cb32e38040adac8e')
        self.assertEqual(berman_files.digest_json(value),
                         frozen.digest(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()))


class ImportBoundaryTests(unittest.TestCase):
    def test_modules_reach_the_frozen_collector_only_through_allowed_paths(self):
        tools = {p.stem: p for p in TOOLS.glob('*.py')}
        for name, path in tools.items():
            modules, taken, bound, loaded = imports(path)
            if 'capture_session' in modules:
                self.assertIn(name, LOADS_FROZEN, f'{name} imports or executes capture_session.py')
            if name.startswith('test_') or name in CAPTURE_PATH:
                continue
            for target, allowed in IMPORTED_ONLY_BY.items():
                if target in modules:
                    self.assertIn(name, allowed, f'{name} imports {target}')
            for source, allowed in TAKEN_ONLY.items():
                self.assertLessEqual(taken.get(source, set()), allowed, f'{name} takes helpers from {source}')
            for source, refused in NEVER_TAKEN.items():
                self.assertFalse(bound.get(source, set()) & refused, f'{name} binds {refused} from {source}')
            if name != 'berman_legacy':
                for source in CAPTURE_PATH:
                    self.assertNotIn('capture', taken.get(source, set()), f'{name} reaches the frozen module')
            if name in ARCHIVE:
                outside = {m for m in modules - set(ARCHIVE) if m not in sys.stdlib_module_names}
                self.assertFalse(outside, f'{name} imports beyond the archive builder and the standard library')
            if name in LEAVES:
                outside = {m for m in modules - LEAVES[name] if m not in sys.stdlib_module_names}
                self.assertFalse(outside, f'{name} imports beyond {sorted(LEAVES[name])} and the standard library')
        for name in CAPTURE_PATH:
            self.assertFalse(imports(tools[name])[0] & OUTSIDE_CAPTURE,
                             f'{name} must not depend on modules outside the verified capture path')
        standalone = [tools[name] for name in STANDALONE] + list((TOOLS / 'development_launchers').glob('*.py'))
        for path in standalone:
            self.assertFalse(imports(path)[0] & set(tools), f'{path.name} imports a tools module')

    def test_importing_every_module_leaves_one_frozen_copy(self):
        code = 'import sys; sys.path.insert(0, sys.argv[1]); import test_boundaries; test_boundaries.frozen_copies()'
        result = subprocess.run([sys.executable, '-I', '-B', '-c', code, str(TOOLS)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {'copies': 1, 'collector': True, 'strays': [], 'holders': []})

    def test_legacy_interface_is_narrow_and_shares_one_instance(self):
        public = {k for k in vars(berman_legacy) if not k.startswith('_')}
        self.assertEqual(public, LEGACY)
        for name in public:
            self.assertIs(getattr(berman_legacy, name), getattr(frozen, name), name)
        self.assertIs(berman_config.Decision, berman_files.Decision)
        self.assertIs(berman_records.Decision, berman_files.Decision)
        self.assertTrue(issubclass(berman_config.Complete, berman_files.Decision))
        for module in (berman_records, fieldwork, berman_config):
            self.assertFalse(hasattr(module, 'capture'), f'{module.__name__} re-exports the frozen collector')
        for name in ('fieldwork', 'keep'):
            self.assertFalse(hasattr(berman_records, name), f'berman_records re-exports {name}')

    def test_low_level_command_imports_under_isolation(self):
        result = subprocess.run([sys.executable, '-I', '-B', str(TOOLS / 'fieldwork.py'), '--help'],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
