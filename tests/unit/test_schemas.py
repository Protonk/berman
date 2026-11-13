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

    def test_a_long_report_is_counted_and_bounded_whatever_the_record_holds(self):
        long_text = 'commission text that fills a record ' * 40
        wrong = {
            name: [long_text] for name in ('packet', 'project', 'commission', 'readiness', 'purpose', 'support_trial')
        }
        wrong.update(
            {name: long_text for name in ('documents', 'capture', 'harness', 'workspace', 'package', 'ending', 'plan')}
        )
        value = {'schema': 'berman.operation/1', 'run': 'run-001', 'phase': 'ready', **wrong}
        with self.assertRaises(schemas.InvalidRecord) as refused:
            schemas.validate('berman.operation/1', value)
        text = str(refused.exception)
        self.assertEqual(len(refused.exception.problems), 13)
        self.assertTrue(
            text.startswith('does not satisfy berman.operation/1: capture must be an object or null, not a string ("')
        )
        self.assertTrue(text.endswith('; and 5 more'), text)
        # A container is named, never printed; a scalar is shown only as far as it identifies itself.
        self.assertIn('commission must be a string or null, not an array', text)
        self.assertLessEqual(max(len(problem) for problem in refused.exception.problems), 110)
        self.assertLess(len(text), 1000)

    def test_a_long_scalar_is_shortened(self):
        record = {'schema': 'berman.operation/1', 'run': 'run-001', 'phase': 'ready'}
        self.assertEqual(schemas.problems('berman.operation/1', dict(record, phase='x' * 500)), [])
        found = schemas.problems('berman.operation/1', dict(record, run='r' * 500, phase=5))
        self.assertEqual(found, ['phase must be a string, not an integer (5)'])
        found = schemas.problems('berman.operation/1', dict(record, packet=['p' * 500]))
        self.assertEqual(found, ['packet must be a string or null, not an array'])
        found = schemas.problems('berman.operation/1', dict(record, documents='d' * 500))
        self.assertEqual(found, ['documents must be an array or null, not a string ("' + 'd' * 36 + '...)'])

    def test_a_problem_can_always_be_printed(self):
        # Found in review: a lone surrogate read from a record made the message itself unprintable,
        # and one such record took plain status down with it.
        record = {
            'schema': 'berman.operation/1',
            'run': 'run-001',
            'phase': 'ready',
            'documents': '\ud83d',
            'bad\udc00key': 1,
        }
        for problem in schemas.problems(
            'berman.operation/1', dict(record, capture={'implementation_sha256': {'a\ud83d': 5}})
        ):
            problem.encode('utf-8')
        found = schemas.problems('berman.operation/1', record)
        self.assertEqual(found, ['documents must be an array or null, not a string ("\\ud83d")'])

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
