"""Packaged record schemas: what they accept, what they refuse and how a refusal reads.

Fixtures under tests/fixtures/records are synthetic. Each invalid one must fail with exactly
the problems expected.json lists, so a refusal's wording is pinned, not merely its occurrence.
"""

import copy
import json
import unittest

from berman import schemas
from berman.workspace import identity
from tests.support.paths import REPO

FIXTURES = REPO / 'tests/fixtures/records'
EXPECTED = json.loads((FIXTURES / 'expected.json').read_text())
# Records this repository ships as examples and test inputs, by the schema each must satisfy.
SHIPPED = {
    'berman.recipe/2': ['examples/minimal-study/recipes', 'tests/fixtures/workspace/recipes'],
    'berman.research-plan/1': ['examples/minimal-study/plans'],
    'berman.connection/1': ['tests/fixtures/workspace/connections'],
}


def folder(name):
    return FIXTURES / schemas.path(name).stem


def fixtures(name, kind):
    return sorted((folder(name) / kind).glob('*.json'))


class FixtureTests(unittest.TestCase):
    def test_every_schema_has_valid_and_invalid_fixtures(self):
        for name in schemas.SUPPORTED:
            with self.subTest(schema=name):
                self.assertTrue(fixtures(name, 'valid'), 'no valid fixture')
                self.assertTrue(fixtures(name, 'invalid'), 'no invalid fixture')
        listed = {p.relative_to(FIXTURES).as_posix() for p in FIXTURES.glob('*/invalid/*.json')}
        self.assertEqual(listed, set(EXPECTED), 'expected.json and the invalid fixtures disagree')

    def test_valid_fixtures_are_accepted_and_returned_untouched(self):
        for name in schemas.SUPPORTED:
            for path in fixtures(name, 'valid'):
                with self.subTest(fixture=path.relative_to(FIXTURES).as_posix()):
                    value = json.loads(path.read_text())
                    before = copy.deepcopy(value)
                    self.assertIs(schemas.validate(name, value, path), value)
                    self.assertEqual(value, before)

    def test_invalid_fixtures_report_exactly_their_problems(self):
        for name in schemas.SUPPORTED:
            for path in fixtures(name, 'invalid'):
                relative = path.relative_to(FIXTURES).as_posix()
                with self.subTest(fixture=relative):
                    value = json.loads(path.read_text())
                    before = copy.deepcopy(value)
                    self.assertEqual(schemas.problems(name, value), EXPECTED[relative])
                    with self.assertRaises(schemas.InvalidRecord) as refused:
                        schemas.validate(name, value, path)
                    self.assertEqual(list(refused.exception.problems), EXPECTED[relative])
                    self.assertEqual(value, before, 'a refused value was changed')

    def test_shipped_examples_and_test_inputs_satisfy_their_schemas(self):
        checked = 0
        for name, roots in SHIPPED.items():
            for path in sorted(p for root in roots for p in (REPO / root).rglob('*.json')):
                with self.subTest(file=path.relative_to(REPO).as_posix()):
                    self.assertEqual(schemas.problems(name, json.loads(path.read_text())), [])
                    checked += 1
        self.assertGreaterEqual(checked, 5)


class MessageTests(unittest.TestCase):
    def test_a_refusal_names_the_source_the_schema_and_each_place(self):
        value = {'schema': 'berman.workspace/1', 'python': 7, 'connections': {'orchard': {'definition': 'c.json'}}}
        with self.assertRaises(schemas.InvalidRecord) as refused:
            schemas.validate('berman.workspace/1', value, '/synthetic/study/berman.json')
        self.assertEqual(
            str(refused.exception),
            '/synthetic/study/berman.json does not satisfy berman.workspace/1: codex is required; '
            'codex_home is required; connections.orchard.checkout is required; packet_root is required; '
            'python must be a string, not an integer (7)',
        )
        self.assertIsInstance(refused.exception, ValueError)
        self.assertEqual(refused.exception.schema, 'berman.workspace/1')
        self.assertEqual(refused.exception.source, '/synthetic/study/berman.json')

    def test_a_long_report_is_counted_and_never_echoes_the_record(self):
        secret = 'commission text that a message must not repeat ' * 20
        steps = [{'id': f'step-{n}', 'recipe': secret, 'commission': secret} for n in range(3)] + [{} for _ in range(6)]
        value = {
            'schema': 'berman.research-plan/1',
            'id': 'p',
            'connection': 'c',
            'steps': steps,
            'commitment': [secret],
        }
        with self.assertRaises(schemas.InvalidRecord) as refused:
            schemas.validate('berman.research-plan/1', value)
        text = str(refused.exception)
        self.assertEqual(len(refused.exception.problems), 13)
        self.assertTrue(text.startswith('does not satisfy berman.research-plan/1: commitment must be a string or null'))
        self.assertTrue(text.endswith('; and 5 more'), text)
        self.assertNotIn('commission text', text)
        self.assertLess(len(text), 600)

    def test_a_long_scalar_is_shortened(self):
        found = schemas.problems(
            'berman.operation/1', {'schema': 'berman.operation/1', 'run': 'run-001', 'phase': 'x' * 500}
        )
        self.assertEqual(found, [])
        found = schemas.problems('berman.operation/1', {'schema': 'berman.operation/1', 'run': 'r' * 500, 'phase': 5})
        self.assertEqual(found, ['phase must be a string, not an integer (5)'])
        found = schemas.problems(
            'berman.operation/1',
            {'schema': 'berman.operation/1', 'run': 'run-001', 'phase': 'ready', 'packet': ['p' * 500]},
        )
        self.assertEqual(found, ['packet must be a string, not an array'])
        found = schemas.problems(
            'berman.operation/1',
            {'schema': 'berman.operation/1', 'run': 'run-001', 'phase': 'ready', 'documents': 'd' * 500},
        )
        self.assertEqual(found, ['documents must be an array, not a string ("' + 'd' * 36 + '...)'])

    def test_a_record_of_another_family_is_refused_by_name(self):
        found = schemas.problems(
            'berman.recipe/2', json.loads((folder('berman.research-plan/1') / 'valid/minimal.json').read_text())
        )
        self.assertIn('schema must be "berman.recipe/2", not a string ("berman.research-plan/1")', found)

    def test_an_unsupported_name_has_no_schema(self):
        for name in ('berman.workspace/2', 'berman.configuration/1', 'stewart.migration/1', '../../etc/passwd'):
            with self.subTest(name=name), self.assertRaisesRegex(KeyError, 'No packaged schema is named'):
                schemas.validate(name, {})


class PackagedSchemaTests(unittest.TestCase):
    def test_each_schema_file_declares_its_own_name(self):
        digests = schemas.check()
        self.assertEqual(tuple(digests), schemas.SUPPORTED)
        for name in schemas.SUPPORTED:
            with self.subTest(schema=name):
                loaded = schemas.schema(name)
                self.assertEqual(loaded['title'], name)
                self.assertEqual(loaded['$id'], 'urn:berman:schema:' + schemas.path(name).stem)
                self.assertEqual(loaded['$schema'], 'https://json-schema.org/draft/2020-12/schema')
                self.assertIn('schema', loaded['required'])
                self.assertTrue(loaded['description'].strip())

    def test_schemas_stay_open_to_fields_they_do_not_name(self):
        # Records grow, and researcher inputs carry explanatory fields. A closed object would turn
        # a newer or annotated record into reported damage.
        def closed(node, where):
            if isinstance(node, dict):
                if node.get('additionalProperties') is False or 'unevaluatedProperties' in node:
                    yield where
                for key, child in node.items():
                    yield from closed(child, f'{where}/{key}')
            elif isinstance(node, list):
                for index, child in enumerate(node):
                    yield from closed(child, f'{where}/{index}')

        for name in schemas.SUPPORTED:
            with self.subTest(schema=name):
                self.assertEqual(list(closed(schemas.schema(name), '')), [])

    def test_schema_files_are_part_of_the_software_identity(self):
        recorded = identity()
        self.assertEqual(schemas.problems('berman.software/1', recorded), [])
        for name, digest in schemas.check().items():
            with self.subTest(schema=name):
                self.assertEqual(recorded['files_sha256']['schemas/' + schemas.path(name).name], digest)
        self.assertEqual(recorded['environment']['dependencies']['jsonschema'].split('.')[0], '4')


if __name__ == '__main__':
    unittest.main()
