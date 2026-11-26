//! What must survive across transcript generations: bytes a later capture did not extend, incomplete
//! final lines, and the last committed generation after an interrupted write.

mod support;

use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

use berman::capture::{check, observe};
use berman::files;
use berman::keys::Store;
use berman::library::{Library, ROOT};
use berman::places::Places;
use serde_json::{Value, json};

const PARENT: &str = "telemetry/parent";

/// A library with one Claude Code episode whose transcript the test writes itself.
struct Session {
    _temporary: tempfile::TempDir,
    library: Library,
    node: i64,
    state: Value,
    id: String,
    transcript: PathBuf,
}

impl Session {
    fn new() -> Session {
        let temporary = tempfile::tempdir().unwrap();
        let base = temporary.path().canonicalize().unwrap();
        let places = Places { data: base.join("data"), returns: base.join("returns"), homes: base.join("homes") };
        let library = Library::create(places, Store::File).unwrap();
        let id = files::uuid();
        let home = base.join("home");
        let state = json!({"id": id, "actor": "claude", "home": home, "project": base});
        let node = library.make_directory(ROOT, "episodes/example").unwrap().id;
        let transcript = home.join("projects/example").join(format!("{id}.jsonl"));
        std::fs::create_dir_all(transcript.parent().unwrap()).unwrap();
        Session { _temporary: temporary, library, node, state, id, transcript }
    }

    /// A transcript line of this session that says something.
    fn line(&self, said: &str) -> String {
        format!("{}\n", json!({"sessionId": self.id, "message": {"role": "assistant", "content": said}}))
    }

    /// Write the transcript and capture it.
    fn capture(&self, content: &str) -> berman::error::Result<Value> {
        std::fs::write(&self.transcript, content).unwrap();
        let event = json!({"hook_event_name": "Stop", "transcript_path": self.transcript});
        observe(&self.library, self.node, &self.state, &self.id, &event)
    }

    fn read(&self, path: &str) -> Vec<u8> {
        self.library.read(self.node, path).unwrap()
    }

    fn names(&self, directory: &str) -> Vec<String> {
        match self.library.find(self.node, directory).unwrap() {
            Some(found) => self.library.children(found.id).unwrap().into_iter().map(|entry| entry.name).collect(),
            None => Vec::new(),
        }
    }

    fn remove(&self, path: &str) {
        self.library.remove(&self.library.expect(self.node, path).unwrap()).unwrap();
    }

    /// Everything the episode holds, to show that a refused capture changed nothing.
    fn tree(&self) -> Vec<(String, Option<String>)> {
        let found = self.library.files(self.node).unwrap();
        found.into_iter().map(|(name, member)| (name, member.sha256)).collect()
    }
}

#[test]
fn a_growing_transcript_adds_history_and_shares_its_bytes() {
    let session = Session::new();
    // About six megabytes that no two chunks share.
    let mut content = String::new();
    for number in 0..6000 {
        content.push_str(&session.line(&files::hex(&files::random(500)).repeat(1 + number % 2)));
    }
    session.capture(&content).unwrap();
    let once = session.library.checkpoint().unwrap();
    assert!(once > content.len() as u64);
    for _ in 0..8 {
        let again = session.capture(&content).unwrap();
        assert_eq!(again["previous_snapshot_is_prefix"], true);
    }
    let rows = session.read(&format!("{PARENT}/captures.jsonl"));
    assert_eq!(rows.iter().filter(|byte| **byte == b'\n').count(), 9);
    // Nine observations of the same bytes hold one copy of them.
    let nine = session.library.checkpoint().unwrap();
    assert!(nine - once < 256 * 1024, "nine observations grew the library by {} bytes", nine - once);
    content.push_str(&session.line("one more line"));
    let grown = session.capture(&content).unwrap();
    assert_eq!(grown["previous_snapshot_is_prefix"], true);
    assert_eq!(grown["saved_bytes"], content.len());
    assert_eq!(grown["sha256"], files::digest(content.as_bytes()));
    assert_eq!(session.read(&format!("{PARENT}/transcript.jsonl")), content.as_bytes());
    // An appended line rewrites only the last chunk of the transcript and of its reading copy.
    let after = session.library.checkpoint().unwrap();
    assert!(after - nine < 3 * 1024 * 1024, "one appended line grew the library by {} bytes", after - nine);
    assert!(session.names("telemetry/displaced").is_empty());
    assert!(check(&session.library, session.node, true).unwrap().is_some());
}

#[test]
fn one_very_long_line_is_captured_whole_and_promptly() {
    let session = Session::new();
    // A single tool result can be tens of megabytes on one line.
    let long = session.line(&"0123456789abcdef".repeat(2 * 1024 * 1024));
    let content = format!("{}{long}{}", session.line("before"), session.line("after"));
    let started = Instant::now();
    let captured = session.capture(&content).unwrap();
    assert!(started.elapsed() < Duration::from_secs(20), "a 32 MiB line took {:?}", started.elapsed());
    assert_eq!(captured["sha256"], files::digest(content.as_bytes()));
    assert_eq!(session.read(&format!("{PARENT}/transcript.jsonl")), content.as_bytes());
    let reader = session.read(&format!("{PARENT}/conversation.txt"));
    assert!(reader.len() > long.len() - 200 && reader.ends_with(b"after\n\n"));
}

#[test]
fn a_rewrite_or_truncation_retains_the_displaced_bytes() {
    let session = Session::new();
    let original = session.line("original");
    let first = session.capture(&original).unwrap();
    let reader = session.read(&format!("{PARENT}/conversation.txt"));
    let replacement = session.line("replacement") + &session.line("continued");
    let second = session.capture(&replacement).unwrap();
    assert_eq!(second["previous_snapshot_is_prefix"], false);
    let kept = format!("telemetry/displaced/{}.jsonl", first["sha256"].as_str().unwrap());
    assert_eq!(session.read(&kept), original.as_bytes());
    assert_eq!(session.read(&format!("telemetry/displaced/{}.txt", first["reader_sha256"].as_str().unwrap())), reader);
    // A transcript cut short is not a prefix of what was captured either.
    let truncated = session.line("replacement");
    let third = session.capture(&truncated).unwrap();
    assert_eq!(third["previous_snapshot_is_prefix"], false);
    let kept = format!("telemetry/displaced/{}.jsonl", second["sha256"].as_str().unwrap());
    assert_eq!(session.read(&kept), replacement.as_bytes());
    assert_eq!(session.read(&format!("{PARENT}/transcript.jsonl")), truncated.as_bytes());
    assert!(check(&session.library, session.node, true).unwrap().is_some());
}

#[test]
fn lost_or_changed_displaced_bytes_are_noticed_and_nothing_is_built_on_them() {
    let session = Session::new();
    let first = session.capture(&session.line("original")).unwrap();
    session.capture(&session.line("replacement")).unwrap();
    for (digest, extension) in [("sha256", ".jsonl"), ("reader_sha256", ".txt")] {
        let path = format!("telemetry/displaced/{}{extension}", first[digest].as_str().unwrap());
        let data = session.read(&path);
        session.library.write(session.node, &path, b"corrupted retained bytes").unwrap();
        let before = session.tree();
        for thorough in [false, true] {
            let refused = check(&session.library, session.node, thorough).unwrap_err().to_string();
            assert!(refused.contains("Retained capture bytes differ"), "{refused}");
        }
        assert!(session.capture(&session.line("a third version")).is_err());
        assert_eq!(session.tree(), before);
        session.remove(&path);
        assert!(check(&session.library, session.node, true).is_err());
        assert!(session.capture(&session.line("a third version")).is_err());
        session.library.write(session.node, &path, &data).unwrap();
        std::fs::write(&session.transcript, session.line("replacement")).unwrap();
        assert!(check(&session.library, session.node, true).unwrap().is_some());
    }
    assert_eq!(session.read(&format!("{PARENT}/transcript.jsonl")), session.line("replacement").as_bytes());
}

#[test]
fn an_incomplete_final_line_keeps_its_own_bytes() {
    let session = Session::new();
    let original = session.line("original");
    let first = session.capture(&format!("{original}partial")).unwrap();
    assert_eq!(first["omitted_partial_tail_bytes"], 7);
    assert_eq!(first["saved_bytes"], original.len());
    let name = first["partial_tail_file"].as_str().unwrap().to_string();
    assert_eq!(session.read(&format!("{PARENT}/{name}")), b"partial");
    let completed = session.capture(&format!("{original}{}", session.line("completed"))).unwrap();
    assert_eq!(completed["previous_snapshot_is_prefix"], true);
    assert_eq!(completed["partial_tail_file"], Value::Null);
    assert!(!session.names(PARENT).contains(&name));
    let kept = format!("telemetry/tails/{}.bin", first["partial_tail_sha256"].as_str().unwrap());
    assert_eq!(session.read(&kept), b"partial");
    session.remove(&kept);
    let before = session.tree();
    assert!(check(&session.library, session.node, true).is_err());
    assert!(session.capture(&format!("{original}{}more", session.line("completed"))).is_err());
    assert_eq!(session.tree(), before);
}

#[test]
fn the_current_tail_can_stand_for_the_same_earlier_tail() {
    let session = Session::new();
    let original = session.line("original");
    let first = session.capture(&format!("{original}partial")).unwrap();
    session.capture(&format!("{original}{}partial", session.line("more"))).unwrap();
    session.remove(&format!("telemetry/tails/{}.bin", first["partial_tail_sha256"].as_str().unwrap()));
    let metadata = check(&session.library, session.node, true).unwrap().unwrap();
    assert_eq!(metadata["partial_tail_sha256"], first["partial_tail_sha256"]);
}

#[test]
fn alternating_revisions_keep_one_copy_of_each() {
    let session = Session::new();
    let (first, second) = (session.line("first"), session.line("second"));
    for content in [&first, &second, &first, &second] {
        session.capture(content).unwrap();
    }
    assert_eq!(session.names("telemetry/displaced").len(), 4);
    assert!(check(&session.library, session.node, true).unwrap().is_some());
}

#[test]
fn a_transcript_of_another_session_publishes_nothing() {
    let session = Session::new();
    let other =
        format!("{}\n", json!({"sessionId": files::uuid(), "message": {"role": "user", "content": "elsewhere"}}));
    let refused = session.capture(&other).unwrap_err().to_string();
    assert!(refused.contains("identity differs"), "{refused}");
    assert!(session.tree().is_empty());
    // A path outside the harness's session store is refused before it is read.
    let outside = session.transcript.parent().unwrap().parent().unwrap().parent().unwrap().join("elsewhere.jsonl");
    std::fs::write(&outside, session.line("outside")).unwrap();
    let event = json!({"hook_event_name": "Stop", "transcript_path": outside});
    assert!(observe(&session.library, session.node, &session.state, &session.id, &event).is_err());
    assert!(session.tree().is_empty());
}

fn database_files(data: &Path) -> u64 {
    ["library.db", "library.db-wal"]
        .iter()
        .filter_map(|name| std::fs::metadata(data.join(name)).ok())
        .map(|m| m.len())
        .sum()
}

#[test]
fn the_last_committed_generation_survives_a_killed_writer() {
    let world = support::World::new();
    let episode = "episodes/interrupted-capture";
    let state = world.open(episode, "claude", &[]);
    let id = state["id"].as_str().unwrap();
    world.send(episode, None, "Begin.");
    let committed = world.json(&format!("{episode}/{PARENT}/capture.json"));
    // Make the transcript large enough that the next capture is still writing when it is killed.
    let transcript = world.base.join(".claude/projects/example").join(format!("{id}.jsonl"));
    let mut content = std::fs::read_to_string(&transcript).unwrap();
    let padding = files::hex(&files::random(2048));
    for number in 0..12_000 {
        let said = format!("{number} {padding}");
        content.push_str(&format!("{}\n", json!({"sessionId": id, "message": {"role": "assistant", "content": said}})));
    }
    std::fs::write(&transcript, &content).unwrap();
    let before = database_files(&world.data());
    let event = json!({"hook_event_name": "Stop", "session_id": id, "transcript_path": transcript});
    let mut command = world.command(&support::berman());
    command.args(["capture", "claude"]).env("BERMAN_EPISODE", id).stdin(std::process::Stdio::piped());
    command.stdout(std::process::Stdio::null()).stderr(std::process::Stdio::null());
    let mut writer = command.spawn().unwrap();
    use std::io::Write;
    writer.stdin.take().unwrap().write_all(event.to_string().as_bytes()).unwrap();
    let deadline = Instant::now() + Duration::from_secs(60);
    while database_files(&world.data()) < before + 4 * 1024 * 1024 {
        assert!(Instant::now() < deadline, "the capture never began writing");
        assert!(writer.try_wait().unwrap().is_none(), "the capture finished before it could be interrupted");
        std::thread::sleep(Duration::from_millis(2));
    }
    writer.kill().unwrap();
    writer.wait().unwrap();
    // The interrupted generation is not visible, and the committed one still verifies.
    assert_eq!(world.json(&format!("{episode}/{PARENT}/capture.json")), committed);
    let kept = world.cat(&format!("{episode}/{PARENT}/transcript.jsonl"));
    assert_eq!(files::digest(&kept), committed["sha256"].as_str().unwrap());
    // The next capture reads the source again.
    let receipt = world.cli(&["close", episode, "--reason", "The writer was killed."]);
    assert!(!receipt["coverage"].to_string().contains("Could not verify"));
    assert_eq!(world.cat(&format!("{episode}/{PARENT}/transcript.jsonl")), content.as_bytes());
    world.cli(&["verify", episode]);
}
