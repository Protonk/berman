"""Check command recording layouts and preservation of original evidence."""

import copy
import csv
import dataclasses
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.run = self.root / "runs/run-005"
        self.run.mkdir(parents=True)
        (self.run / "INSTRUCTIONS.md").write_text("Preserved recording instructions.\n")
        (self.run / "narrative.md").write_text("An account without timestamp headings.\n")
        scratch = self.run / "scratch"
        logs = scratch / "logs"
        logs.mkdir(parents=True)
        for number, exit_code in ((1, 0), (2, 7)):
            stem = logs / f"{number:03d}-sample"
            stem.with_suffix(".json").write_text(json.dumps({"argv": ["git", "status"], "cwd": None, "exit": exit_code}))
            stem.with_suffix(".stdout").write_text("saved output\n")
            stem.with_suffix(".stderr").write_text("")
        git = scratch / "manager-repo/.git"
        (git / "objects").mkdir(parents=True)
        (git / "objects/preserved").write_bytes(b"\0Git object bytes\xff")
        (git / "HEAD").write_text("ref: refs/heads/example\n")
        (git / "empty").mkdir()
        pointer = scratch / "checkouts/worktree/.git"
        pointer.parent.mkdir(parents=True)
        pointer.write_text("gitdir: /historical/run-005/scratch/manager-repo/.git/worktrees/worktree\n")
        self.layout = {"runs": [{"run": "run-005", "original_root": "/historical/run-005", "current_root": "runs/run-005",
                                  "entries": self.inventory(), "git_directories": ["scratch/manager-repo/.git"],
                                  "git_pointers": [{"path": "scratch/checkouts/worktree/.git", "content": pointer.read_text()}]}]}
        (self.root / "source-layout.json").write_text(json.dumps(self.layout))
        for name in ("AGENTS.md", ".gitignore", "SESSION-CAPTURE.txt"):
            (self.root / name).write_text("Fixture support file.\n")
        spec = importlib.util.spec_from_file_location("fixture_archive", Path(__file__).with_name("build_archive.py"))
        self.builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.builder)
        for name in self.builder.CONTEXT_DOCUMENTS:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"Current framing: {name}\n")

    def render(self):
        return self.builder.render(self.builder.select(self.root))[0]

    def package(self, build):
        generated, _ = self.builder.render(build)
        payload = self.builder.assemble(build, generated)
        return dict(generated), payload, self.builder.make_zip(payload, build.edition.prefix)

    def inventory(self, run=None):
        run = run or self.run
        rows = []
        for parent, dirs, files in os.walk(run):
            for name in dirs + files:
                path = Path(parent) / name
                row = {"path": path.relative_to(run).as_posix(), "mode": oct(stat.S_IMODE(path.stat().st_mode))}
                if path.is_dir():
                    row["type"] = "directory"
                else:
                    data = path.read_bytes()
                    row.update(type="file", size=len(data), sha256=hashlib.sha256(data).hexdigest())
                rows.append(row)
        return sorted(rows, key=lambda row: row["path"])

    def test_untimed_commands_keep_exit_values_without_inventing_chronology(self):
        generated = self.render()
        rows = list(csv.DictReader(io.StringIO(generated["review/command-index.csv"].decode())))
        self.assertEqual(len(rows), 2)
        self.assertEqual([row["exit"] for row in rows], ["0", "7"])
        self.assertTrue(all(row["time"] == "" for row in rows))
        self.assertTrue(all(row["time_source"] == "not recorded in command metadata" for row in rows))
        chronology = list(csv.DictReader(io.StringIO(generated["review/chronology.csv"].decode())))
        self.assertEqual(chronology, [])
        self.assertIn(b"RECORDED START: not recorded", generated["review/commands.txt"])
        self.assertEqual(self.inventory(), self.layout["runs"][0]["entries"])

    def test_archive_keeps_internal_git_objects_pointer_and_empty_directory(self):
        build = self.builder.select(self.root)
        payload = self.builder.assemble(build, self.builder.render(build)[0])
        obj = "runs/run-005/scratch/manager-repo/.git/objects/preserved"
        self.assertEqual(payload[obj][0], b"\0Git object bytes\xff")
        self.assertTrue(stat.S_ISDIR(payload["runs/run-005/scratch/manager-repo/.git/empty/"][1]))
        pointer = "runs/run-005/scratch/checkouts/worktree/.git"
        self.assertEqual(payload[pointer][0], (self.root / pointer).read_bytes())
        self.assertIn("ARCHIVE-GUIDE.txt", payload)
        self.assertNotIn("START-HERE.txt", payload)
        for name in self.builder.CONTEXT_DOCUMENTS:
            original = (self.root / name).read_bytes()
            self.assertEqual(payload[name][0], original)
            checksum = hashlib.sha256(original).hexdigest() + "  " + name + "\n"
            self.assertIn(checksum.encode(), payload["checksums.sha256"][0])
        zipped = self.builder.make_zip(payload)
        self.builder.verify_zip(zipped, payload)
        (self.root / obj).write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "Original evidence changed"):
            self.builder.inventory(self.root)

    def test_maintenance_guidance_is_packaged_when_present(self):
        build = self.builder.select(self.root)
        self.assertNotIn("MAINTENANCE.md", self.builder.assemble(build, self.builder.render(build)[0]))
        text = b"Fixture maintenance procedure.\n"
        (self.root / "MAINTENANCE.md").write_bytes(text)
        build = self.builder.select(self.root)
        payload = self.builder.assemble(build, self.builder.render(build)[0])
        self.assertEqual(payload["MAINTENANCE.md"][0], text)
        self.assertIn(f"{hashlib.sha256(text).hexdigest()}  MAINTENANCE.md\n".encode(), payload["checksums.sha256"][0])
        self.builder.verify_zip(self.builder.make_zip(payload), payload)

    def test_mixed_command_layouts_keep_timestamp_and_exit_provenance(self):
        scratch = self.run / "scratch"
        (scratch / "commands").mkdir()
        fixtures = (
            ("commands/001-status.command.json", {"started": "2026-10-23T00:12:00+00:00"}, 3),
            ("commands/20261023T001000.000000Z.argv.json", {}, 2),
            ("timed.command.json", {"time": "2026-10-23T00:11:00Z", "exit": 1}, None),
            ("checkout.json", {"exit": 7}, None),
        )
        for name, fields, separate_exit in fixtures:
            path = scratch / name
            argv = ["python3", "-c", "print('one')\nprint('two')"] if "started" in fields else ["git", "status"]
            path.write_text(json.dumps({"argv": argv, "cwd": None, **fields}))
            suffix = next(s for s in (".command.json", ".argv.json", ".json") if name.endswith(s))
            stem = path.with_name(path.name[:-len(suffix)])
            for extension, text in ((".stdout", "output\n"), (".stderr", "")):
                Path(str(stem) + extension).write_text(text)
            if separate_exit is not None:
                Path(str(stem) + ".exit").write_text(str(separate_exit) + "\n")
        self.layout["runs"][0]["entries"] = self.inventory()
        (self.root / "source-layout.json").write_text(json.dumps(self.layout))
        generated = self.render()
        rows = list(csv.DictReader(io.StringIO(generated["review/command-index.csv"].decode())))
        self.assertEqual(len(rows), 6)
        self.assertEqual([Path(row["metadata"]).name for row in rows[:3]],
                         ["20261023T001000.000000Z.argv.json", "timed.command.json", "001-status.command.json"])
        self.assertEqual([row["exit"] for row in rows[:3]], ["2", "1", "3"])
        self.assertTrue(rows[2]["time_source"].endswith("001-status.command.json#/started"))
        self.assertTrue(rows[2]["exit_source"].endswith("001-status.exit"))
        untimed = next(row for row in rows if row["metadata"].endswith("/checkout.json"))
        self.assertEqual(untimed["time"], "")
        self.assertEqual(untimed["exit"], "7")
        self.assertEqual(untimed["time_source"], "not recorded in command metadata")
        chronology = list(csv.DictReader(io.StringIO(generated["review/chronology.csv"].decode())))
        self.assertEqual([row["kind"] for row in chronology],
                         ["command invocation start (timestamped filename)",
                          "command invocation start (metadata field)",
                          "command invocation start (metadata field)"])
        layout = list(csv.DictReader(io.StringIO(generated["review/recording-layouts.csv"].decode())))[0]
        self.assertIn("scratch/commands/<sequence>-<label>.command.json", layout["metadata_patterns"])
        self.assertEqual(layout["time_location"], "metadata /started | metadata /time | not recorded | timestamped filename")
        self.assertEqual(self.inventory(), self.layout["runs"][0]["entries"])

    def test_started_command_requires_its_separate_exit_record(self):
        build = self.builder.select(self.root)
        path = self.run / "scratch/commands/001-status.command.json"
        path.parent.mkdir()
        path.write_text(json.dumps({"argv": ["git", "status"], "cwd": None, "started": "2026-10-23T00:12:00Z"}))
        with self.assertRaisesRegex(ValueError, "Missing command exit record: .*001-status.exit"):
            self.builder.commands(build, {path.relative_to(self.root).as_posix()})

    def test_nested_auditor_stream_and_malformed_records_remain_locatable(self):
        path = self.run / 'scratch/nested/auditor-trial.jsonl'
        path.parent.mkdir()
        path.write_text('{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"nested stream"}]}}\nnot-json\n[]\n')
        self.layout['runs'][0]['entries'] = self.inventory()
        (self.root / 'source-layout.json').write_text(json.dumps(self.layout))
        generated = self.render()
        self.assertIn(b'nested stream', generated['review/auditor-events.txt'])
        rows = list(csv.DictReader(io.StringIO(generated['review/transcript-map.csv'].decode())))
        rejected = [row for row in rows if row['availability'] == 'preserved raw; not decoded']
        self.assertEqual([row['line'] for row in rejected], ['2', '3'])
        self.assertEqual(path.read_text().splitlines()[-2:], ['not-json', '[]'])

    def test_new_inventory_checks_mtime_while_legacy_inventory_remains_supported(self):
        for entry in self.layout['runs'][0]['entries']:
            entry['mtime_ns'] = (self.run / entry['path']).lstat().st_mtime_ns
        (self.root / 'source-layout.json').write_text(json.dumps(self.layout))
        self.builder.inventory(self.root)
        path = self.run / 'narrative.md'
        previous = path.stat()
        os.utime(path, ns=(previous.st_atime_ns, previous.st_mtime_ns + 1_000_000))
        with self.assertRaisesRegex(ValueError, 'Original evidence changed'):
            self.builder.inventory(self.root)

    def test_native_task_error_is_rendered_as_harness_with_raw_pointer(self):
        build = self.builder.select(self.root)
        path = self.run / 'session/transcript.jsonl'
        path.parent.mkdir(exist_ok=True)
        with path.open('a') as stream:
            stream.write(json.dumps({'type':'response_item','payload':None})+'\n')
            stream.write(json.dumps({'type':'event_msg','payload':{'type':'task_complete','error':{'message':'HTTP 400 trial'}}})+'\n')
        out = self.builder.Outputs()
        self.builder.transcripts(build, {path.relative_to(self.root).as_posix()}, out)
        result = out['review/manager-events.txt']
        self.assertIn(b'ROLE: harness | KIND: task_error', result)
        self.assertIn(b'/payload/error', result)
        self.assertIn(b'HTTP 400 trial', result)

    def test_participant_inquiry_protocol_and_text_notes_reach_reading_aids(self):
        (self.run / 'run.json').write_text(json.dumps({'recording_protocol': 'participant-inquiry/1'}))
        (self.run / 'ASSIGNMENT.md').write_text('Pursue an inquiry while developing Stewart.\n')
        (self.run / 'narrative.md').write_text('')
        (self.run / 'notes').mkdir()
        (self.run / 'notes/one.txt').write_text('Compare two concrete encounters.\n')
        self.layout['runs'][0]['entries'] = self.inventory()
        (self.root / 'source-layout.json').write_text(json.dumps(self.layout))
        generated = self.render()
        row = list(csv.DictReader(io.StringIO(generated['review/recording-layouts.csv'].decode())))[0]
        self.assertEqual(row['recording_protocol'], 'participant-inquiry/1')
        self.assertEqual(row['narrative_timestamp_headings'], 'not requested (placeholder)')
        self.assertIn('notes/one.txt', row['participant_notes'])
        self.assertIn(b'Compare two concrete encounters.', generated['review/run-005.txt'])

    def test_repeated_builds_in_one_process_are_identical(self):
        first, second = (self.package(self.builder.select(self.root)) for _ in range(2))
        self.assertEqual(first, second)
        self.assertNotIn("checksums.sha256", first[0])
        self.assertNotIn(b"  checksums.sha256\n", first[1]["checksums.sha256"][0])

    def test_a_separate_run_edition_leaves_no_state_for_the_cumulative_edition(self):
        other = self.run.parent / "run-006"
        (other / "notes").mkdir(parents=True)
        (other / "INSTRUCTIONS.md").write_text("Another run.\n")
        self.layout["runs"].append({"run": "run-006", "original_root": "/historical/run-006", "current_root": "runs/run-006",
                                    "entries": self.inventory(other), "git_directories": [], "git_pointers": []})
        (self.root / "source-layout.json").write_text(json.dumps(self.layout))
        fresh = self.package(self.builder.select(self.root))
        separate = self.builder.select(self.root, runs=["run-006"])
        self.assertEqual((separate.runs, separate.subset, separate.separate, separate.tree, separate.archive_path),
                         (("run-006",), True, True, False, "archive/stewart-run-006.zip"))
        generated, payload, _ = self.package(separate)
        self.assertNotIn("review/run-005.txt", payload)
        self.assertFalse(any(path.startswith("runs/run-005") for path in payload))
        self.assertEqual([r["run"] for r in json.loads(payload["source-layout.json"][0])["runs"]], ["run-006"])
        self.assertEqual(self.package(self.builder.select(self.root)), fresh)
        self.assertEqual(json.loads(fresh[1]["source-layout.json"][0]), self.layout)

    def test_rendering_reads_the_build_without_changing_it(self):
        build = self.builder.select(self.root)
        before = copy.deepcopy((build.layout, build.entries, build.attributions))
        self.builder.destinations(build, *self.package(build))
        self.assertEqual((build.layout, build.entries, build.attributions), before)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            build.runs = ()
        with self.assertRaises(dataclasses.FrozenInstanceError):
            build.edition.prefix = "elsewhere/"

    def test_edition_record_names_every_builder_module(self):
        tools = Path(__file__).resolve().parent
        record = json.loads(self.render()["review/EDITION.json"])
        expected = {"build_archive.py"} | {path.name for path in tools.glob("archive_*.py")}
        self.assertEqual(set(record["builder_sha256"]), expected)
        self.assertEqual(list(record["builder_sha256"])[:2], ["build_archive.py", "archive_stewart.py"])
        for name, digest in record["builder_sha256"].items():
            self.assertEqual(digest, hashlib.sha256((tools / name).read_bytes()).hexdigest())

    def test_written_edition_verifies_and_a_stale_aid_is_reported(self):
        build = self.builder.select(self.root)
        written = self.builder.edition(build)
        self.assertEqual((written["mode"], written["archive_path"]), ("built and verified", "archive/stewart-runs.zip"))
        self.assertEqual((self.root / "archive/SHA256SUMS").read_text(), f"{written['archive_sha256']}  stewart-runs.zip\n")
        self.assertEqual(self.builder.edition(build, verify=True), dict(written, mode="verified"))
        (self.root / "review/run-005.txt").write_text("edited\n")
        with self.assertRaisesRegex(ValueError, "Generated file is missing or stale: review/run-005.txt"):
            self.builder.edition(build, verify=True)
        self.assertEqual(self.builder.edition(build), written)
        self.assertEqual(self.builder.edition(build, verify=True), dict(written, mode="verified"))
        self.assertEqual(self.inventory(), self.layout["runs"][0]["entries"])

    def test_historical_audit_apparatus_is_not_packaged(self):
        probe = self.root / "handoffs/fieldworker-support-audit/probe_04_migration.py"
        probe.parent.mkdir(parents=True)
        probe.write_text("Historical script.\n")
        _, payload, _ = self.package(self.builder.select(self.root))
        self.assertFalse(any(path.startswith("handoffs/fieldworker-support-audit/") for path in payload))

    def test_selection_refusals(self):
        with self.assertRaisesRegex(ValueError, "The Stewart edition selects Stewart's runs"):
            self.builder.select(self.root, "stewart", ["stewart"])
        with self.assertRaisesRegex(ValueError, "--edition connections needs at least one --connection"):
            self.builder.select(self.root, "connections")
        with self.assertRaisesRegex(ValueError, "No recorded runs for connection\\(s\\): orchard"):
            self.builder.select(self.root, "connections", ["orchard"])
        with self.assertRaisesRegex(ValueError, "Run\\(s\\) outside this edition's connections: run-404"):
            self.builder.select(self.root, runs=["run-404"])
        (self.root / "berman.json").write_text(json.dumps({
            "schema": "berman.workspace/1", "legacy_connection": "orchard",
            "connections": {"orchard": {"definition": "connections/orchard.json", "checkout": "/historical/orchard"}}}))
        (self.root / "connections").mkdir()
        (self.root / "connections/orchard.json").write_text(json.dumps({"name": "Orchard", "readers": []}))
        with self.assertRaisesRegex(ValueError, "No recorded runs are selected for this edition"):
            self.builder.select(self.root)
        build = self.builder.select(self.root, "connections", ["orchard"])
        self.assertEqual((build.edition.prefix, build.archive_path, build.connection("run-005")["basis"]),
                         ("berman-analysis/", "archive/editions/berman-orchard.zip", "legacy single-project convention"))
        self.layout["runs"].append(self.layout["runs"][0])
        (self.root / "source-layout.json").write_text(json.dumps(self.layout))
        with self.assertRaisesRegex(ValueError, "Duplicate run identity in source-layout.json"):
            self.builder.inventory(self.root)


class ArchiveInterpreterTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        tools = self.root / "tools"
        tools.mkdir()
        source = Path(__file__).parent
        for name in ("build_archive.py", "archive_select.py", "archive_package.py", "archive_stewart.py"):
            shutil.copyfile(source / name, tools / name)
        self.script = tools / "build_archive.py"

    def invoke(self, python, *args):
        (self.root / "berman.json").write_text(json.dumps({"python": str(python)}))
        return subprocess.run([sys.executable, "-I", "-B", str(self.script), *args],
                              text=True, capture_output=True, timeout=10, cwd=self.root)

    def test_cli_selects_configured_interpreter_before_reading_inventory(self):
        target = self.root / "configured python"
        target.write_text(f"#!{sys.executable}\nimport json, sys\nprint(json.dumps(sys.argv[1:]))\n")
        target.chmod(0o755)
        # A relative installation path, spaces and every builder argument survive.
        result = self.invoke(target.name, "--verify", "--run", "run-005")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), ["-I", "-B", str(self.script), "--verify", "--run", "run-005"])
        self.assertFalse((self.root / "review").exists())

    def test_same_interpreter_alias_does_not_reexecute(self):
        target = self.root / "python-alias"
        target.symlink_to(sys.executable)
        result = self.invoke(target, "--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--verify", result.stdout)

    def test_missing_configured_interpreter_refuses_before_building(self):
        result = self.invoke(self.root / "missing-python", "--verify")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Archive interpreter configured in", result.stderr)
        self.assertIn("is unavailable", result.stderr)
        self.assertNotIn("stale", result.stderr)
        self.assertFalse((self.root / "review").exists())


if __name__ == "__main__":
    unittest.main()
