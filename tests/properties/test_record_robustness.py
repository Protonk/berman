"""Records of any shape are accepted or reported as invalid data; none crashes a reader or is changed by one.

Starting from the valid operation-record fixtures, one field at a time is replaced by an
arbitrary JSON value or removed. Fully random documents are tried too, but they fail at the
first check; the near-valid ones are what reach the code behind validation.
"""

import copy
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from hypothesis import given
from hypothesis import strategies as st

from berman import schemas
from berman._legacy.api import RUN
from berman.files import json_bytes
from berman.lifecycle import FACTS
from berman.records import Store
from berman.workflow import Workflow
from tests.support.paths import REPO
from tests.support.properties import budget
from tests.support.workspace import FixtureHarness, git_repository, workspace

VALID = [json.loads(p.read_text()) for p in sorted((REPO / 'tests/fixtures/records/operation-1/valid').glob('*.json'))]
scalars = (
    st.none()
    | st.booleans()
    | st.integers(-5, 5)
    | st.floats(allow_nan=False, allow_infinity=False)
    | st.text(max_size=6)
)
values = st.recursive(
    scalars,
    lambda inner: st.lists(inner, max_size=3) | st.dictionaries(st.text(max_size=5), inner, max_size=3),
    max_leaves=8,
)


@st.composite
def near_valid(draw):
    """A valid operation record with one field, at any depth, replaced by any JSON value or removed."""
    record = copy.deepcopy(draw(st.sampled_from(VALID)))
    target = record
    while True:
        key = draw(st.sampled_from(sorted(target)))
        if isinstance(target[key], dict) and target[key] and draw(st.booleans()):
            target = target[key]
            continue
        if draw(st.booleans()):
            del target[key]
        else:
            target[key] = draw(values)
        return record


class RobustnessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        base = Path(cls.temporary.name).resolve()
        (base / 'codex').mkdir()
        workspace(base / 'berman', base, base / 'stewart', documents=['README.md'])
        git_repository(base / 'stewart', {'README.md': 'Project context\n'})
        cls.store = Store(base / 'berman')
        cls.workflow = Workflow(cls.store, FixtureHarness(cls.store))

    def place(self, document):
        """Write a document as an operation record, under the run it names when that is an identity."""
        named = document.get('run') if isinstance(document, dict) else None
        run = named if isinstance(named, str) and RUN.fullmatch(named) else 'run-001'
        path = self.store.folder(run) / 'operation.json'
        path.parent.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, path.parent, ignore_errors=True)
        path.write_bytes(json_bytes(document))
        return run, path

    def read(self, run):
        """(record, None) or (None, refusal). Any exception but ValueError is a crash and fails the test."""
        try:
            return self.store.record(run), None
        except ValueError as refusal:
            return None, refusal

    @budget(300)
    @given(near_valid() | values)
    def test_a_record_is_read_back_exactly_or_refused_as_invalid_data(self, document):
        run, path = self.place(document)
        try:
            before = path.read_bytes(), path.stat().st_mtime_ns
            record, refusal = self.read(run)
            if refusal is None:
                self.assertEqual(record, document)
                self.assertEqual(json_bytes(record), before[0])
            else:
                self.assertIn(str(path), str(refusal))
            self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before, 'reading changed the record')
        finally:
            shutil.rmtree(path.parent)

    @budget(200)
    @given(near_valid())
    def test_discovery_and_status_report_any_record_without_failing(self, document):
        run, path = self.place(document)
        try:
            record, refusal = self.read(run)
            rows = {row['run']: row for row in self.store.discover()}
            status = self.workflow.status()
            self.workflow.cheatsheet()
            if refusal is not None:
                # Invalid data is a reported problem; what the record held is unknown, never a negative.
                row = rows[run]
                self.assertIn(str(refusal), row['problems'])
                self.assertIsNone(row['lifecycle']['ending']['value'])
                self.assertIsNone(row['lifecycle']['packaged']['value'])
                self.assertIn(run, [r['run'] for r in status['runs']])
            elif record.get('purpose') == 'support-trial':
                self.assertNotIn(run, rows)  # Never research, whatever else the marker says.
            else:
                row = rows[run]
                for name in FACTS:
                    fact = row['lifecycle'][name]
                    self.assertEqual(fact['value'] is None, bool(fact.get('unknown')), name)
        finally:
            shutil.rmtree(path.parent)

    @budget(200)
    @given(near_valid())
    def test_a_record_that_can_be_saved_is_the_record_that_is_read(self, document):
        named = document.get('run')
        run = named if isinstance(named, str) and RUN.fullmatch(named) else 'run-001'
        folder = self.store.folder(run)
        try:
            try:
                self.store.save(document)
            except ValueError, KeyError, TypeError:
                self.assertFalse(folder.exists(), 'a refused record left something behind')
                return
            self.assertEqual((folder / 'operation.json').read_bytes(), json_bytes(document))
            self.assertEqual(self.store.record(run), document)
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    @budget(300)
    @given(values | near_valid(), st.sampled_from(schemas.SUPPORTED))
    def test_validation_reports_and_never_changes_a_value(self, value, name):
        before = copy.deepcopy(value)
        found = schemas.problems(name, value)
        self.assertTrue(all(isinstance(problem, str) and problem for problem in found))
        if not found:
            self.assertIs(schemas.validate(name, value), value)
        self.assertEqual(value, before)


if __name__ == '__main__':
    unittest.main()
