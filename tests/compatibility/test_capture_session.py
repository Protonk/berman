"""Exercise capture boundaries and preservation using isolated fixtures."""
from tests.support.paths import REPO

import importlib.util
import json
from pathlib import Path
import shutil
import os
import stat
import subprocess
import sys
import tempfile
import unittest

from berman._legacy import capture_session as module
from berman.installation import PACKAGE


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.home = self.base / "codex"
        self.field = self.base / "fieldwork"
        self.analysis = self.base / "analysis"
        self.source = self.home / "sessions/2026/rollout-test-session.jsonl"
        self.source.parent.mkdir(parents=True)
        self.collector = module.Collector(self.field, self.home, self.analysis)
        self.run = self.make_run("run-004")
        self.records = [{"type": "session_meta", "payload": {"id": "test-session"}},
                        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"text": "Assignment"}]}}]
        self.save()

    def make_run(self, name):
        root = self.field / name
        root.mkdir(parents=True)
        (root / "scratch").mkdir()
        for name in ("INSTRUCTIONS.md", "narrative.md"):
            (root / name).write_text("")
        return root

    def save(self, tail=b""):
        self.source.write_bytes(b"".join((json.dumps(r) + "\n").encode() for r in self.records) + tail)

    def event(self, name, **values):
        return {"hook_event_name": name, "session_id": "test-session", "transcript_path": str(self.source),
                "cwd": "/a/source/repository", "turn_id": "turn-one", **values}

    def register(self, run="run-004"):
        return self.collector.hook(self.event("UserPromptSubmit", prompt=module.MARKER + run + "\nDo the assignment."))

    def answer(self, text="Finished."):
        self.records.append({"type": "response_item", "payload": {"type": "message", "role": "assistant",
                             "content": [{"type": "output_text", "text": text}]}})
        self.save()

    def test_exact_marker_registers_without_early_capture(self):
        self.assertEqual(self.register(), {})
        binding = json.loads((self.run / "session/binding.json").read_text())
        self.assertEqual(binding["session_id"], "test-session")
        self.assertEqual(binding["start_turn_id"], "turn-one")
        self.assertFalse((self.run / "session/transcript.jsonl").exists())

    def test_capture_without_git_log_does_not_create_one(self):
        self.assertFalse((self.run / "git-commands.log").exists())
        self.register()
        self.answer()
        self.assertEqual(self.collector.hook(self.event("Stop", last_assistant_message="Finished.")), {})
        self.assertEqual((self.run / "session/transcript.jsonl").read_bytes(), self.source.read_bytes())
        self.assertFalse((self.run / "git-commands.log").exists())

    def test_capture_preserves_optional_git_log(self):
        log = self.run / "git-commands.log"
        original = b"2026-10-21T12:00:00Z | git status\n"
        log.write_bytes(original)
        self.register()
        self.answer()
        self.assertEqual(self.collector.hook(self.event("Stop", last_assistant_message="Finished.")), {})
        self.assertEqual(log.read_bytes(), original)

    def test_incidental_quoted_and_old_markers_are_ignored(self):
        for prompt in ["Example:\n" + module.MARKER + "run-004",
                       "~~~text\n" + module.MARKER + "run-004\n~~~",
                       "> " + module.MARKER + "run-004", "Fieldwork-Run: run-004",
                       "Stewart-Fieldwork-Run-Giraffe: run-004"]:
            result = self.collector.hook(self.event("UserPromptSubmit", prompt=prompt))
            self.assertNotIn('continue', result)
        self.assertFalse((self.run / "session").exists())
        self.assertFalse(self.collector.state.exists())

    def test_marker_may_follow_blank_lines(self):
        self.collector.hook(self.event("UserPromptSubmit", prompt="\n\n" + module.MARKER + "run-004"))
        self.assertTrue((self.run / "session/binding.json").is_file())

    def test_bad_paths_and_missing_runs_are_refused(self):
        for run in ["../run-004", "run-004/other", "run-005", "run-004 "]:
            with self.assertRaises(ValueError):
                self.register(run)
        self.assertFalse((self.run / "session").exists())

    def test_unregistered_stop_is_a_noop(self):
        self.assertEqual(self.collector.hook(self.event("Stop")), {})
        self.assertFalse(self.collector.state.exists())

    def test_final_message_and_tool_records_are_captured(self):
        self.register()
        self.records += [{"type": "response_item", "payload": {"type": "custom_tool_call", "name": "exec",
                         "call_id": "tool-1", "input": "read a file"}},
                         {"type": "response_item", "payload": {"type": "custom_tool_call_output",
                         "call_id": "tool-1", "output": "file bytes"}}]
        self.answer()
        self.assertEqual(self.collector.hook(self.event("Stop", last_assistant_message="Finished.")), {})
        self.assertEqual((self.run / "session/transcript.jsonl").read_bytes(), self.source.read_bytes())
        info = json.loads((self.run / "session/capture.json").read_text())
        self.assertTrue(info["last_assistant_message_present"])
        readable = (self.run / "session/conversation.txt").read_text()
        for text in ["Finished.", "read a file", "file bytes", "/payload/output"]:
            self.assertIn(text, readable)

    def test_partial_tail_is_reported_and_recovered(self):
        self.register()
        self.save(b'{"incomplete":')
        self.collector.hook(self.event("Interrupt"))
        info = json.loads((self.run / "session/capture.json").read_text())
        self.assertEqual(info["omitted_partial_tail_bytes"], len(b'{"incomplete":'))
        self.assertEqual((self.run / "session" / info["partial_tail_file"]).read_bytes(), b'{"incomplete":')
        self.answer()
        self.collector.hook(self.event("Stop", last_assistant_message="Finished."))
        self.assertEqual((self.run / "session/transcript.jsonl").read_bytes(), self.source.read_bytes())

    def test_wrong_source_session_is_refused(self):
        self.register()
        self.records[0]["payload"]["id"] = "other-session"
        self.save()
        with self.assertRaises(ValueError):
            self.collector.hook(self.event("Stop"))
        self.assertFalse((self.run / "session/transcript.jsonl").exists())

    def test_unrecognized_format_is_preserved(self):
        self.register()
        self.source.write_bytes(b"new transcript format\n")
        self.collector.hook(self.event("Stop"))
        self.assertEqual((self.run / "session/transcript.jsonl").read_bytes(), self.source.read_bytes())
        info = json.loads((self.run / "session/capture.json").read_text())
        self.assertEqual(info["unparsed_jsonl_lines"], [1])

    def test_missing_final_message_is_reported(self):
        self.register()
        result = self.collector.hook(self.event("Stop", last_assistant_message="Not flushed yet."))
        self.assertIn("systemMessage", result)
        self.assertTrue((self.run / "session/transcript.jsonl").exists())
        self.assertFalse(self.collector.command("refresh", "run-004")["last_assistant_message_present"])
        self.answer("Not flushed yet.")
        self.assertTrue(self.collector.command("refresh", "run-004")["last_assistant_message_present"])

    def test_source_rewrite_keeps_previous_bytes(self):
        self.register()
        self.answer()
        self.collector.hook(self.event("Stop"))
        previous = self.source.read_bytes()
        self.records[1]["payload"]["content"][0]["text"] = "rewritten assignment"
        self.save()
        self.collector.hook(self.event("Stop"))
        kept = list((self.run / "session").glob("previous-*.jsonl"))
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].read_bytes(), previous)

    def test_finalization_freezes_later_hooks(self):
        self.register()
        self.answer()
        self.collector.hook(self.event('Stop'))
        final = self.collector.command("finalize", "run-004")
        frozen = (self.run / "session/transcript.jsonl").read_bytes()
        self.answer("A later conversation.")
        self.collector.hook(self.event("Stop"))
        self.assertEqual((self.run / "session/transcript.jsonl").read_bytes(), frozen)
        self.assertEqual(self.collector.command("finalize", "run-004"), final)

    def test_migration_protects_archived_capture(self):
        self.register()
        self.answer()
        self.collector.hook(self.event('Stop'))
        self.collector.command("finalize", "run-004")
        moved = self.analysis / "runs/run-004"
        moved.parent.mkdir(parents=True)
        shutil.move(str(self.run), moved)
        before = (moved / "session/captures.jsonl").read_bytes()
        self.collector.hook(self.event("SessionEnd"))
        self.assertEqual((moved / "session/captures.jsonl").read_bytes(), before)
        self.assertTrue(self.collector.command("status", "run-004")["finalized"])
        moved.rename(moved.with_name('misplaced-packet'))
        warning = self.collector.hook(self.event('Stop'))
        self.assertIn('active packet run-004 is missing', warning['systemMessage'])

    def test_thread_reuse_ends_previous_registration(self):
        self.register()
        self.answer()
        next_run = self.make_run("run-005")
        self.register("run-005")
        before = (self.run / "session/transcript.jsonl").read_bytes()
        self.answer("Second run done.")
        self.collector.hook(self.event("Stop", last_assistant_message="Second run done."))
        self.assertEqual((self.run / "session/transcript.jsonl").read_bytes(), before)
        self.assertTrue((self.run / "session/ended.json").exists())
        self.assertIn(b"Second run done.", (next_run / "session/transcript.jsonl").read_bytes())
        self.collector.command("finalize", "run-004")
        self.assertEqual((self.run / "session/transcript.jsonl").read_bytes(), before)

    def test_session_directory_symlink_is_refused(self):
        target = self.base / "elsewhere"
        target.mkdir()
        (self.run / "session").symlink_to(target)
        with self.assertRaises(ValueError):
            self.register()
        self.assertFalse(list(target.iterdir()))

    def test_existing_unowned_session_files_are_preserved(self):
        (self.run / "session").mkdir()
        (self.run / "session/capture.json").write_text("existing data")
        with self.assertRaises(ValueError):
            self.register()
        self.assertEqual((self.run / "session/capture.json").read_text(), "existing data")

    def test_another_session_cannot_claim_the_run(self):
        self.register()
        with self.assertRaises(ValueError):
            self.collector.hook(self.event("UserPromptSubmit", session_id="different",
                                prompt=module.MARKER + "run-004"))

    def test_hook_cli_ignores_unmarked_prompts_and_reports_errors_without_blocking(self):
        executable = [sys.executable, "-I", "-B", str(PACKAGE / "_legacy/capture_session.py"), "hook",
                      '--codex-home', str(self.home), '--fieldwork-root', str(self.field), '--analysis-root', str(self.analysis)]
        for prompt, expected_error in [("An ordinary conversation.", False), (module.MARKER + "../bad", True)]:
            result = subprocess.run(executable, input=json.dumps(self.event("UserPromptSubmit", prompt=prompt)),
                                    text=True, capture_output=True, check=True)
            output = json.loads(result.stdout)
            self.assertEqual("systemMessage" in output, expected_error)
            self.assertNotIn("decision", output)
            self.assertNotIn("continue", output)
        self.assertFalse(self.collector.state.exists())

    def test_migrated_capture_enters_archive_readers_automatically(self):
        self.register()
        self.answer()
        self.collector.hook(self.event('Stop'))
        self.collector.command("finalize", "run-004")
        moved = self.analysis / "runs/run-004"
        moved.parent.mkdir(parents=True)
        shutil.move(str(self.run), moved)
        pointer = moved / "scratch/manager-004/.git"
        pointer.parent.mkdir()
        pointer.write_text("gitdir: /an/external/git/worktree\n")
        prompt = pointer.parent / "audits/run-004/PROMPT.md"
        prompt.parent.mkdir(parents=True)
        prompt.write_text("A preserved assignment.\n")
        entries = []
        for parent, dirs, files in os.walk(moved):
            for name in dirs + files:
                p = Path(parent) / name
                row = {"path": p.relative_to(moved).as_posix(), "mode": oct(stat.S_IMODE(p.stat().st_mode))}
                if p.is_dir():
                    row.update(type="directory")
                else:
                    data = p.read_bytes()
                    row.update(type="file", size=len(data), sha256=module.digest(data))
                entries.append(row)
        layout = {"runs": [{"run": "run-004", "current_root": "runs/run-004", "original_root": str(self.run),
                           "entries": sorted(entries, key=lambda r: r["path"]),
                           "git_pointer": {"path": "scratch/manager-004/.git", "content": pointer.read_text()}}]}
        (self.analysis / "source-layout.json").write_text(json.dumps(layout))
        from berman.archive import builder
        for name in builder.CONTEXT_DOCUMENTS:
            path = self.analysis / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"Current framing: {name}\n")
        build = builder.select(self.analysis)
        self.assertEqual(build.runs, ("run-004",))
        generated, _ = builder.render(build)
        self.assertIn(b"runs/run-004/session/transcript.jsonl", generated["review/transcript-map.csv"])
        self.assertIn(b"Finished.", generated["review/manager-events.txt"])
        self.assertIn(b"runs/run-004/session/conversation.txt", generated["review/run-004.txt"])
        self.assertIn(b"ASSIGNMENT SOURCE: runs/run-004/scratch/manager-004/audits/run-004/PROMPT.md", generated["review/run-004.txt"])


if __name__ == "__main__":
    unittest.main()
