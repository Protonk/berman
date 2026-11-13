"""Requirement checks that metadata presence alone cannot satisfy."""

from importlib import metadata
import builtins
from types import SimpleNamespace
import unittest
from unittest import mock

from berman import environment, verify


def distribution(version='1', requires=(), python=None):
    return SimpleNamespace(version=version, requires=list(requires), metadata={'Requires-Python': python})


class RequirementTests(unittest.TestCase):
    def inspect(self, packages):
        def find(name):
            if name not in packages:
                raise metadata.PackageNotFoundError(name)
            return packages[name]

        with mock.patch.object(environment.metadata, 'distribution', side_effect=find):
            return environment.inspect()

    def test_markers_select_required_missing_packages_and_exclude_inapplicable_ones(self):
        versions, problems = self.inspect(
            {
                'berman': distribution(
                    requires=(
                        'required; python_version >= "3.14"',
                        'obsolete; python_version < "3.14"',
                        'optional; extra == "tests"',
                        'needed; extra == "tests" or python_version >= "3.14"',
                    )
                )
            }
        )
        self.assertEqual(versions, {'needed': None, 'required': None})
        self.assertEqual(problems, [])

    def test_each_parent_constraint_is_checked_even_after_a_dependency_was_seen(self):
        versions, problems = self.inspect(
            {
                'berman': distribution(requires=('first', 'second')),
                'first': distribution(requires=('shared>=2',)),
                'second': distribution(requires=('shared<2',)),
                'shared': distribution('1.5', requires=('first',)),
            }
        )
        self.assertEqual(versions, {'first': '1', 'second': '1', 'shared': '1.5'})
        self.assertEqual(problems, ['first requires shared>=2; installed shared 1.5'])

    def test_requested_extras_are_followed_through_normalized_names(self):
        versions, problems = self.inspect(
            {
                'berman': distribution(requires=('Some_Package[feature]>=1', 'some-package[other]')),
                'some-package': distribution(
                    requires=(
                        'first; extra == "feature"',
                        'second; extra == "other"',
                        'unused; extra == "unused"',
                    )
                ),
                'first': distribution(),
                'second': distribution(),
            }
        )
        self.assertEqual(versions, {'first': '1', 'second': '1', 'some-package': '1'})
        self.assertEqual(problems, [])

    def test_python_constraints_are_checked(self):
        _, problems = self.inspect(
            {
                'berman': distribution(requires=('old-library',)),
                'old-library': distribution(python='<3.14'),
            }
        )
        self.assertEqual(len(problems), 1)
        self.assertIn('old-library requires Python <3.14', problems[0])

    def test_incompatible_direct_dependencies_fail_software_verification(self):
        original = metadata.distribution

        def old(name):
            if name in ('rich', 'jsonschema'):
                current = original(name)
                return SimpleNamespace(
                    version={'rich': '14.2.0', 'jsonschema': '4.25.1'}[name],
                    requires=current.requires,
                    metadata=current.metadata,
                )
            return original(name)

        with mock.patch.object(environment.metadata, 'distribution', side_effect=old):
            result = verify.software()
        self.assertEqual(result['verdict'], 'failed')
        self.assertEqual(result['failed'], ['dependencies'])
        problems = result['checks']['dependencies']['details']['problems']
        self.assertTrue(any('installed rich 14.2.0' in problem for problem in problems))
        self.assertTrue(any('installed jsonschema 4.25.1' in problem for problem in problems))

    def test_missing_files_and_missing_api_cannot_disappear_from_verification(self):
        original = verify.importlib.import_module
        for broken in ('rich', 'rich.console', 'jsonschema'):

            def load(name, broken=broken):
                if name == broken:
                    raise ModuleNotFoundError(f'synthetic missing {name}')
                return original(name)

            with self.subTest(module=broken), mock.patch.object(verify.importlib, 'import_module', side_effect=load):
                result = verify.dependencies()
            self.assertEqual(result['status'], 'failed')
            self.assertIn(f'synthetic missing {broken}', ' '.join(result['details']['problems']))
        with mock.patch.object(verify.importlib, 'import_module', return_value=SimpleNamespace()):
            result = verify.dependencies()
        self.assertEqual(result['status'], 'failed')
        self.assertIn('Draft202012Validator', ' '.join(result['details']['problems']))

    def test_a_broken_requirement_parser_is_a_failed_check_not_a_traceback(self):
        original = builtins.__import__

        def broken(name, *args, **kwargs):
            if name.startswith('packaging.'):
                raise RuntimeError('synthetic damaged parser')
            return original(name, *args, **kwargs)

        with mock.patch('builtins.__import__', side_effect=broken):
            result = verify.software()
        self.assertEqual(result['verdict'], 'failed')
        self.assertEqual(result['failed'], ['installation', 'dependencies'])
        self.assertIn('packaging does not import', result['checks']['installation']['summary'])
        self.assertIn('synthetic damaged parser', str(result['checks']['dependencies']['details']))
        self.assertEqual(result['checks']['schemas']['status'], 'passed')


if __name__ == '__main__':
    unittest.main()
