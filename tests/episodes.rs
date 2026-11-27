//! Black-box checks of the built command in disposable homes and independent Git projects.

mod support;

use std::os::fd::AsRawFd;
use std::path::Path;

use serde_json::{Value, json};
use support::{World, text};

const EPISODE: &str = "episodes/decisions";

fn full_episode(actor: &str) {
    let world = World::new();
    let status = world.command(Path::new(actor)).current_dir(&world.project).status().expect("ordinary work");
    assert!(status.success());
    assert!(world.ls("").is_empty(), "ordinary work leaves nothing in the library");
    let state = world.open(EPISODE, actor, &[&world.job]);
    assert_eq!(state, world.open(EPISODE, actor, &[&world.job]));
    assert_eq!(state["initial_project"]["head"], Value::Null);
    assert!(state["initial_project"]["status"].as_str().unwrap().contains("ordinary.txt"));
    let returns = Path::new(state["return"].as_str().unwrap()).to_path_buf();
    assert!(returns.is_dir() && std::fs::read_dir(&returns).unwrap().next().is_none());
    let first = world.cli(&["start", EPISODE]);
    assert_eq!(first["exit"], 0);
    assert_eq!(first, world.cli(&["start", EPISODE]));
    assert_eq!(world.cli(&["status", EPISODE])["ending"], Value::Null);
    // The stable ID names the episode as well as its path does.
    let id = state["id"].as_str().unwrap();
    let resume = ["resume", id, "--request", "after-lunch", "--prompt", "Continue."];
    let second = world.cli(&resume);
    assert_eq!(second, world.cli(&["resume", EPISODE, "--request", "after-lunch", "--prompt", "Continue."]));
    world.refuses(&["resume", EPISODE, "--request", "after-lunch", "--prompt", "Different."], "different instructions");
    std::os::unix::fs::symlink(&world.job, returns.join("do-not-copy")).unwrap();
    let closed = world.cli(&["close", EPISODE, "--reason", "My decision is made."]);
    assert_eq!(closed, world.cli(&["close", EPISODE, "--reason", "My decision is made."]));
    world.refuses(&["start", EPISODE], "closed");
    world.assert_documented(EPISODE);
    assert!(world.read(&format!("{EPISODE}/return/decision.md")).contains("two callers"));
    assert!(!world.exists(&format!("{EPISODE}/return/do-not-copy")));
    assert!(closed["coverage"].to_string().contains("do-not-copy"));
    assert!(world.read(&format!("{EPISODE}/telemetry/parent/conversation.txt")).contains("done"));
    assert_eq!(world.ls(&format!("{EPISODE}/telemetry/events")).len(), 6);
    assert_eq!(world.cli(&["status", EPISODE])["sittings"].as_array().unwrap().len(), 2);
    // What was collected is gone from the return directory. The link was never collected, so it stays.
    assert!(!returns.join("decision.md").exists());
    assert_eq!(closed["preserved"], json!([text(&returns.join("do-not-copy"))]));
    world.refuses(&["library", "rm", "--recursive", EPISODE], "still has a return directory");
    std::fs::remove_file(returns.join("do-not-copy")).unwrap();
    let again = world.cli(&["close", EPISODE, "--reason", "My decision is made."]);
    assert_eq!(
        (&again["sha256"], &again["return"], &again["preserved"]),
        (&closed["sha256"], &Value::Null, &json!([]))
    );
    assert!(!returns.exists());
    // The parent of every return directory can be entered but not listed. With cleanup complete it is empty.
    let parent = returns.parent().unwrap();
    assert_eq!(std::fs::read_dir(parent).unwrap_err().kind(), std::io::ErrorKind::PermissionDenied);
    assert!(support::listed(parent).is_empty());

    let copy = world.base.join("detached");
    world.cli(&["library", "export", EPISODE, &text(&copy)]);
    world.cli(&["library", "rm", "--recursive", EPISODE]);
    std::fs::remove_dir_all(&world.project).unwrap();
    let digest = closed["sha256"].as_str().unwrap();
    assert_eq!(world.cli(&["verify", &text(&copy), "--sha256", digest])["sha256"], digest);
    let review = "reviews/read these";
    let summary =
        world.cli(&["review", &text(&copy), "--question", "What did the actor rule out?", "--output", review]);
    assert_eq!(summary["sources"][0]["sha256"], digest);
    let material = format!("{review}/{}", summary["sources"][0]["path"].as_str().unwrap());
    assert!(world.exists(&format!("{material}/return/decision.md")));
    assert_eq!(world.read(&format!("{review}/question.md")), "What did the actor rule out?\n");
    world.refuses(&["review", &text(&copy), "--question", "Again?", "--output", review], "exists");
    std::fs::write(copy.join("return/decision.md"), "changed").unwrap();
    world.refuses(&["verify", &text(&copy)], "differ");
    world.refuses(&["verify", &text(&copy)], "changed return/decision.md");
    assert_eq!(world.cli(&["verify", &material])["sha256"], digest);
}

#[test]
fn codex_episode() {
    full_episode("codex");
}

#[test]
fn claude_episode() {
    full_episode("claude");
}

#[test]
fn empty_return_scoped_review_and_changed_ending() {
    let world = World::new();
    let state = world.open(EPISODE, "codex", &[&world.job]);
    std::fs::write(&world.job, "Change the return.").unwrap();
    let different: Vec<String> = world.opening(EPISODE, "codex", &[&world.job]);
    world.refuses(&different.iter().map(String::as_str).collect::<Vec<_>>(), "different");
    let receipt = world.cli(&["close", EPISODE, "--reason", "The work moved on."]);
    assert!(receipt.to_string().contains("No sitting"));
    assert_eq!(receipt["return"], Value::Null);
    assert!(!Path::new(state["return"].as_str().unwrap()).exists());
    world.refuses(&["close", EPISODE, "--reason", "Different."], "different ending");
    let nested = format!("{EPISODE}/nested/review");
    world.refuses(&["review", EPISODE, "--question", "What happened?", "--output", &nested], "outside");
    assert!(!world.exists(&format!("{EPISODE}/nested")));
    world.refuses(&["verify", EPISODE, "--sha256", "wrong"], "differs");
    world.refuses(&["review", EPISODE, EPISODE, "--question", "When?", "--output", "reviews/when"], "twice");
    assert!(!world.exists("reviews"));
}

#[test]
fn review_two_independently_closed_episodes() {
    let world = World::new();
    let mut ids = Vec::new();
    for (name, actor) in [("episodes/first", "claude"), ("episodes/later", "codex")] {
        ids.push(world.open(name, actor, &[&world.job])["id"].clone());
        world.cli(&["start", name]);
        assert_eq!(world.cli(&["close", name, "--reason", name])["record"], name);
    }
    let output = "reviews/returning-producer";
    let question = "What evidence would change the next decision?";
    let result =
        world.cli(&["review", "episodes/first", ids[1].as_str().unwrap(), "--question", question, "--output", output]);
    let sources = result["sources"].as_array().unwrap();
    assert_eq!(sources.iter().map(|item| item["episode"].clone()).collect::<Vec<_>>(), ids);
    for item in sources {
        let source = format!("{output}/{}", item["path"].as_str().unwrap());
        assert!(world.exists(&format!("{source}/return/decision.md")));
        assert_eq!(world.cli(&["verify", &source])["sha256"], item["sha256"]);
    }
    // A verified review copy survives the removal of its source.
    world.cli(&["library", "rm", "--recursive", "episodes/first"]);
    let kept = format!("{output}/sources/{}", ids[0].as_str().unwrap());
    assert_eq!(world.cli(&["verify", &kept])["sha256"], sources[0]["sha256"]);
}

#[test]
fn ordinary_callback_requires_no_episode_and_no_library() {
    for world in [World::new(), World::bare()] {
        let result = world.callback("codex", None, "ordinary input");
        assert!(result.status.success());
        assert_eq!(serde_json::from_slice::<Value>(&result.stdout).unwrap(), json!({}));
        assert!(
            !world.data().join("locks").exists() || std::fs::read_dir(world.data().join("locks")).unwrap().count() == 0
        );
    }
    assert!(!World::bare().data().exists());
}

#[test]
fn wrong_parent_and_ended_callback_are_ignored() {
    let world = World::new();
    let id = world.open(EPISODE, "codex", &[&world.job])["id"].as_str().unwrap().to_string();
    world.cli(&["start", EPISODE]);
    let events = format!("{EPISODE}/telemetry/events");
    let first = world.ls(&events);
    let mut row = json!({"hook_event_name": "Stop", "session_id": "different"});
    assert!(world.callback("codex", Some(&id), &row.to_string()).status.success());
    assert_eq!(first, world.ls(&events));
    world.cli(&["close", EPISODE, "--reason", "Done."]);
    row["session_id"] = json!("12345678-1234-1234-1234-123456789012");
    assert!(world.callback("codex", Some(&id), &row.to_string()).status.success());
    assert_eq!(first, world.ls(&events));
}

#[test]
fn busy_sitting_and_separation_from_the_library() {
    let world = World::new();
    // A checkout that contains the library would let ordinary project searches reach it.
    support::git(&world.base, &["init", "-q"]);
    world.refuses(&["open", "episodes/inside", "--actor", "claude", "--project", &text(&world.base)], "apart");
    std::fs::remove_dir_all(world.base.join(".git")).unwrap();
    let id = world.open(EPISODE, "claude", &[&world.job])["id"].as_str().unwrap().to_string();
    let lease = std::fs::File::open(world.data().join("locks").join(format!("{id}.sitting"))).unwrap();
    // SAFETY: the descriptor is open for the duration of the call.
    assert_eq!(unsafe { libc::flock(lease.as_raw_fd(), libc::LOCK_EX) }, 0);
    world.refuses(&["close", EPISODE, "--reason", "Done."], "in progress");
    world.refuses(&["start", EPISODE], "in progress");
    assert_eq!(world.cli(&["status", EPISODE])["ending"], Value::Null);
}

#[test]
fn a_mistyped_reference_changes_nothing() {
    let world = World::new();
    world.cli(&["library", "mkdir", "episodes"]);
    world.refuses(&["start", "episodes/nope"], "No such file");
    world.refuses(&["start", "episodes"], "No episode is at episodes");
    assert!(world.ls("episodes").is_empty());
}

#[test]
fn a_sitting_reads_the_transcript_at_its_end_when_no_callback_arrived() {
    let mut world = World::new();
    world.env.insert("FIXTURE_WITHOUT_HOOKS".into(), "1".into());
    world.open(EPISODE, "claude", &[&world.job]);
    let sitting = world.cli(&["start", EPISODE]);
    assert!(sitting["transcript"]["read_at"].is_string());
    assert!(!world.exists(&format!("{EPISODE}/telemetry/events")));
    assert!(world.read(&format!("{EPISODE}/telemetry/parent/conversation.txt")).contains("done"));
    let receipt = world.cli(&["close", EPISODE, "--reason", "The callbacks never ran."]);
    world.assert_documented(EPISODE);
    let limitations = receipt["coverage"]["limitations"].as_array().unwrap();
    assert_eq!(limitations, &[json!("No session callbacks were observed.")]);
}

#[test]
fn what_is_said_in_the_session_after_the_last_sitting_is_not_collected() {
    let world = World::new();
    let state = world.open(EPISODE, "claude", &[&world.job]);
    world.cli(&["start", EPISODE]);
    // The producer resumes the same native session directly, outside Berman, before closing.
    let id = state["id"].as_str().unwrap();
    let transcript = world.base.join(".claude/projects/example").join(format!("{id}.jsonl"));
    let later =
        json!({"sessionId": id, "message": {"role": "assistant", "content": "an ordinary exchange afterwards"}});
    let mut said = std::fs::read_to_string(&transcript).unwrap();
    said.push_str(&format!("{later}\n"));
    std::fs::write(&transcript, said).unwrap();
    let receipt = world.cli(&["close", EPISODE, "--reason", "Collected."]);
    assert_eq!(receipt["coverage"]["limitations"], json!([]));
    for name in ["transcript.jsonl", "conversation.txt"] {
        assert!(!world.read(&format!("{EPISODE}/telemetry/parent/{name}")).contains("an ordinary exchange afterwards"));
    }
}

#[test]
fn a_resumed_sitting_does_not_import_intervening_native_work() {
    let world = World::new();
    let state = world.open(EPISODE, "claude", &[]);
    world.send(EPISODE, None, "Begin.");
    let id = state["id"].as_str().unwrap();
    let native = world.base.join(".claude/projects/example").join(format!("{id}.jsonl"));
    use std::io::Write;
    let mut file = std::fs::OpenOptions::new().append(true).open(native).unwrap();
    writeln!(file, "{}", json!({"sessionId":id, "message":{"role":"assistant","content":"outside correspondence"}}))
        .unwrap();
    let sitting = world.send(EPISODE, Some("continue"), "Continue.");
    assert!(sitting["response"].is_string());
    assert!(sitting["transcript"]["limitation"].is_string());
    let receipt = world.cli(&["close", EPISODE, "--reason", "Done."]);
    assert!(receipt["coverage"].to_string().contains("unattributed work"));
    assert!(!world.read(&format!("{EPISODE}/telemetry/parent/transcript.jsonl")).contains("outside correspondence"));
}

#[test]
fn unreadable_transcript_does_not_discard_the_actor_return() {
    let world = World::new();
    world.open(EPISODE, "claude", &[&world.job]);
    world.cli(&["start", EPISODE]);
    let damaged = world.base.join("damaged");
    std::fs::write(&damaged, "damaged").unwrap();
    let stored = format!("{EPISODE}/telemetry/parent/transcript.jsonl");
    world.cli(&["library", "put", "--replace", &text(&damaged), &stored]);
    let receipt = world.cli(&["close", EPISODE, "--reason", "Keep the account with the gap."]);
    assert!(receipt["coverage"].to_string().contains("Could not verify the staged parent"));
    assert!(world.exists(&format!("{EPISODE}/return/decision.md")));
    // The damaged member stays as found, and the record that names it verifies.
    assert_eq!(world.read(&stored), "damaged");
    world.cli(&["verify", EPISODE]);
}

#[test]
fn templates_are_opt_in_and_existing_output_is_preserved() {
    let world = World::new();
    let destination = world.base.join("jobs");
    world.cli(&["template", &text(&destination)]);
    let (note, handover) = (destination.join("note-taking.md"), destination.join("handover.md"));
    assert!(std::fs::read_to_string(&note).unwrap().contains("a reader who was not there"));
    assert!(std::fs::read_to_string(&handover).unwrap().contains("HANDOVER.md"));
    for (selected, other) in [(&note, &handover), (&handover, &note)] {
        let name = format!("{EPISODE}/{}", selected.file_stem().unwrap().to_str().unwrap());
        let state = world.open(&name, "claude", &[selected]);
        let (chosen, unchosen) = (std::fs::read_to_string(selected).unwrap(), std::fs::read_to_string(other).unwrap());
        assert_eq!(state["jobs"], json!([chosen]));
        let opening = state["opening"].as_str().unwrap();
        assert!(opening.contains(chosen.trim_end()));
        assert!(!opening.contains(unchosen.lines().next().unwrap()));
    }
    std::fs::write(&note, "custom").unwrap();
    world.refuses(&["template", &text(&destination)], "exists");
    assert_eq!(std::fs::read_to_string(&note).unwrap(), "custom");
}

#[test]
fn compose_jobs_in_one_actor_opening_and_record() {
    let world = World::new();
    let destination = world.base.join("jobs");
    world.cli(&["template", &text(&destination)]);
    let (note, handover) = (destination.join("note-taking.md"), destination.join("handover.md"));
    let state = world.open(EPISODE, "codex", &[&handover, &note]);
    let jobs = [std::fs::read_to_string(&handover).unwrap(), std::fs::read_to_string(&note).unwrap()];
    assert_eq!(state["jobs"], json!(jobs));
    assert!(state.get("job").is_none());
    let opening = state["opening"].as_str().unwrap();
    assert!(opening.find(jobs[0].trim_end()).unwrap() < opening.find(jobs[1].trim_end()).unwrap());
    assert_eq!(world.open(EPISODE, "codex", &[&handover, &note]), state);
    for selection in [vec![&note, &handover], vec![&handover]] {
        let jobs: Vec<&Path> = selection.iter().map(|path| path.as_path()).collect();
        let arguments = world.opening(EPISODE, "codex", &jobs);
        world.refuses(&arguments.iter().map(String::as_str).collect::<Vec<_>>(), "different instructions");
    }
    std::fs::remove_file(&handover).unwrap();
    std::fs::remove_file(&note).unwrap();
    world.cli(&["start", EPISODE]);
    world.cli(&["close", EPISODE, "--reason", "The opening was delivered."]);
    assert_eq!(world.read(&format!("{EPISODE}/opening.md")), opening);
    assert_eq!(world.json(&format!("{EPISODE}/episode.json"))["jobs"], json!(jobs));
    world.cli(&["verify", EPISODE]);
}

#[test]
fn blank_second_job_leaves_no_episode() {
    let world = World::new();
    let blank = world.base.join("blank.md");
    std::fs::write(&blank, "  \n").unwrap();
    let arguments = world.opening(EPISODE, "claude", &[&world.job, &blank]);
    world.refuses(&arguments.iter().map(String::as_str).collect::<Vec<_>>(), "blank.md");
    assert!(world.ls("").is_empty());
    assert!(!world.data().join("working.noindex/returns").exists() && !world.base.join(".local/state/berman").exists());
}

#[test]
fn edited_opening_refuses_a_sitting() {
    let world = World::new();
    world.open(EPISODE, "claude", &[&world.job]);
    let other = world.base.join("other.md");
    std::fs::write(&other, "Something else.\n").unwrap();
    world.cli(&["library", "put", "--replace", &text(&other), &format!("{EPISODE}/opening.md")]);
    world.refuses(&["start", EPISODE], "opening.md differs");
    assert!(!world.exists(&format!("{EPISODE}/sittings")));
}

#[test]
fn an_episode_keeps_its_identity_when_its_directory_moves() {
    let world = World::new();
    let state = world.open(EPISODE, "claude", &[&world.job]);
    let id = state["id"].as_str().unwrap();
    world.cli(&["start", EPISODE]);
    world.cli(&["library", "mv", EPISODE, "episodes/archive/renamed"]);
    world.refuses(&["status", EPISODE], "No such file");
    assert_eq!(world.cli(&["status", id])["path"], "episodes/archive/renamed");
    let second = world.cli(&["resume", "episodes/archive/renamed", "--request", "moved"]);
    assert_eq!(second["exit"], 0);
    // The actor's return directory has the same path at every sitting.
    let launches = world.launches();
    let returned = |launch: &Value| {
        let arguments = launch["args"].as_array().unwrap();
        let at = arguments.iter().position(|a| a == "--add-dir").unwrap();
        arguments[at + 1].clone()
    };
    assert_eq!(returned(&launches[0]), returned(&launches[1]));
    assert_eq!(returned(&launches[0]), state["return"]);
    let receipt = world.cli(&["close", id, "--reason", "Filed."]);
    assert_eq!(receipt["record"], "episodes/archive/renamed");
    assert_eq!(receipt["episode"], id);
}

#[test]
fn an_interrupted_close_converges_when_repeated() {
    let world = World::new();
    let state = world.open(EPISODE, "claude", &[&world.job]);
    world.cli(&["start", EPISODE]);
    let returns = Path::new(state["return"].as_str().unwrap()).to_path_buf();
    for number in 0..6 {
        std::fs::write(returns.join(format!("large-{number}.bin")), berman::files::random(16 * 1024 * 1024)).unwrap();
    }
    let stored = || {
        let size = |name: &str| std::fs::metadata(world.data().join(name)).map(|m| m.len()).unwrap_or(0);
        size("library.db") + size("library.db-wal")
    };
    let before = stored();
    let mut command = world.command(&support::berman());
    command.args(["close", EPISODE, "--reason", "Collected."]);
    let mut closing = command.stdout(std::process::Stdio::null()).stderr(std::process::Stdio::null()).spawn().unwrap();
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(60);
    while stored() < before + 40 * 1024 * 1024 {
        assert!(
            std::time::Instant::now() < deadline && closing.try_wait().unwrap().is_none(),
            "close was not caught collecting"
        );
        std::thread::sleep(std::time::Duration::from_millis(1));
    }
    closing.kill().unwrap();
    closing.wait().unwrap();
    // The ending is recorded, no record is published, and nothing has left the return directory.
    let status = world.cli(&["status", EPISODE]);
    let ending = status["ending"].clone();
    assert_eq!((ending["reason"].as_str(), &status["record"]), (Some("Collected."), &Value::Null));
    assert_eq!(std::fs::read_dir(&returns).unwrap().count(), 7);
    world.refuses(&["start", EPISODE], "closed");
    world.refuses(&["close", EPISODE, "--reason", "Another reason."], "different ending");
    let receipt = world.cli(&["close", EPISODE, "--reason", "Collected."]);
    assert_eq!(world.json(&format!("{EPISODE}/ending.json")), ending);
    assert_eq!((&receipt["return"], &receipt["preserved"]), (&Value::Null, &json!([])));
    assert_eq!(receipt["coverage"]["limitations"], json!([]));
    assert_eq!(world.ls(&format!("{EPISODE}/return")).len(), 7);
    assert!(!returns.exists());
    assert_eq!(world.cli(&["close", EPISODE, "--reason", "Collected."])["sha256"], receipt["sha256"]);
}

#[test]
fn cleanup_resumes_from_the_committed_inventory() {
    use std::os::unix::fs::PermissionsExt;
    let world = World::new();
    let state = world.open(EPISODE, "claude", &[&world.job]);
    world.cli(&["start", EPISODE]);
    let returns = Path::new(state["return"].as_str().unwrap()).to_path_buf();
    std::fs::create_dir(returns.join("notes")).unwrap();
    std::fs::write(returns.join("notes/first.md"), "first").unwrap();
    // Collection can read the directory, and cleanup cannot yet remove anything from it.
    for directory in [returns.join("notes"), returns.clone()] {
        std::fs::set_permissions(directory, std::fs::Permissions::from_mode(0o500)).unwrap();
    }
    let receipt = world.cli(&["close", EPISODE, "--reason", "Collected."]);
    assert_eq!(receipt["coverage"]["limitations"], json!([]));
    assert_eq!(world.read(&format!("{EPISODE}/return/notes/first.md")), "first");
    let left = receipt["preserved"].as_array().unwrap();
    assert_eq!(left.len(), 2, "{left:?}");
    assert_eq!(receipt["return"], json!(text(&returns)));
    for directory in [returns.clone(), returns.join("notes")] {
        std::fs::set_permissions(directory, std::fs::Permissions::from_mode(0o700)).unwrap();
    }
    // Something arrives after collection, and a collected file changes. Neither is in the record, and both stay.
    std::fs::write(returns.join("late.md"), "arrived after collection").unwrap();
    std::fs::write(returns.join("decision.md"), "changed after collection").unwrap();
    let again = world.cli(&["close", EPISODE, "--reason", "Collected."]);
    assert_eq!(again["sha256"], receipt["sha256"]);
    assert_eq!(again["preserved"], json!([text(&returns.join("decision.md")), text(&returns.join("late.md"))]));
    assert!(!returns.join("notes").exists());
    assert!(world.read(&format!("{EPISODE}/return/decision.md")).contains("two callers"));
    std::fs::remove_file(returns.join("late.md")).unwrap();
    std::fs::remove_file(returns.join("decision.md")).unwrap();
    let done = world.cli(&["close", EPISODE, "--reason", "Collected."]);
    assert_eq!((&done["return"], &done["preserved"], &done["sha256"]), (&Value::Null, &json!([]), &receipt["sha256"]));
    world.cli(&["verify", EPISODE]);
}

#[test]
fn members_over_the_record_s_limits_are_left_out_of_it_and_kept() {
    let world = World::new();
    let state = world.open(EPISODE, "claude", &[&world.job]);
    world.cli(&["start", EPISODE]);
    let returns = Path::new(state["return"].as_str().unwrap()).to_path_buf();
    for number in 0..4100 {
        std::fs::write(returns.join(format!("note-{number:04}.md")), format!("{number}")).unwrap();
    }
    let events = world.ls(&format!("{EPISODE}/telemetry/events"));
    let transcript = world.cat(&format!("{EPISODE}/telemetry/parent/transcript.jsonl"));
    let receipt = world.cli(&["close", EPISODE, "--reason", "Collected."]);
    // The return comes first and fills the 4,096 files. What it could not bring in stays on disk.
    assert_eq!(world.ls(&format!("{EPISODE}/return")).len(), 4096);
    assert_eq!(receipt["preserved"].as_array().unwrap().len(), 5);
    let limitations: Vec<&str> =
        receipt["coverage"]["limitations"].as_array().unwrap().iter().map(|item| item.as_str().unwrap()).collect();
    assert_eq!(limitations.iter().filter(|item| item.starts_with("Omitted return/")).count(), 5);
    // The sitting and the telemetry are left out of the record, named, and kept beside the episode.
    assert!(!world.exists(&format!("{EPISODE}/sittings")) || world.ls(&format!("{EPISODE}/sittings")).is_empty());
    assert!(limitations.iter().any(|item| item.starts_with("Omitted sittings/000001.json: ")
        && item.ends_with("episodes/decisions.omitted/sittings/000001.json")));
    assert!(limitations.contains(&"The parent transcript is not whole in the record."));
    assert_eq!(world.ls("episodes/decisions.omitted/telemetry/events"), events);
    assert_eq!(world.cat("episodes/decisions.omitted/telemetry/parent/transcript.jsonl"), transcript);
    assert_eq!(world.ls("episodes/decisions.omitted/sittings"), ["000001.json"]);
    let listed = world.json(&format!("{EPISODE}/manifest.json"));
    assert_eq!(listed["members"].as_object().unwrap().len(), 4096 + 5);
    world.cli(&["verify", EPISODE]);
}

#[test]
fn a_close_that_never_verified_leaves_the_return_alone_however_often_it_is_repeated() {
    let world = World::new();
    let state = world.open(EPISODE, "claude", &[&world.job]);
    world.cli(&["start", EPISODE]);
    let returns = Path::new(state["return"].as_str().unwrap()).to_path_buf();
    world.cli(&["library", "mv", &format!("{EPISODE}/opening.md"), "elsewhere.md"]);
    for _ in 0..3 {
        world.refuses(&["close", EPISODE, "--reason", "Collected."], "missing opening.md");
        assert!(std::fs::read_to_string(returns.join("decision.md")).unwrap().contains("two callers"));
    }
    assert_eq!(world.cli(&["status", EPISODE])["preserved"], json!([text(&returns.join("decision.md"))]));
    // With the record whole again the same close verifies, and only then is the return retired.
    world.cli(&["library", "mv", "elsewhere.md", &format!("{EPISODE}/opening.md")]);
    let receipt = world.cli(&["close", EPISODE, "--reason", "Collected."]);
    assert_eq!((&receipt["return"], &receipt["preserved"]), (&Value::Null, &json!([])));
    assert!(world.read(&format!("{EPISODE}/return/decision.md")).contains("two callers"));
    assert!(!returns.exists());
}

#[test]
fn renaming_the_ending_does_not_reopen_an_episode() {
    use std::os::unix::fs::PermissionsExt;
    let world = World::new();
    let state = world.open(EPISODE, "codex", &[&world.job]);
    let id = state["id"].as_str().unwrap();
    world.cli(&["start", EPISODE]);
    let returns = Path::new(state["return"].as_str().unwrap()).to_path_buf();
    std::fs::set_permissions(&returns, std::fs::Permissions::from_mode(0o500)).unwrap();
    let receipt = world.cli(&["close", EPISODE, "--reason", "Collected."]);
    assert_eq!(receipt["preserved"].as_array().unwrap().len(), 1);
    std::fs::set_permissions(&returns, std::fs::Permissions::from_mode(0o700)).unwrap();
    world.cli(&["library", "mv", &format!("{EPISODE}/ending.json"), "ending-moved.json"]);
    let launches = world.launches().len();
    world.refuses(&["start", EPISODE, "--request", "again"], "closed");
    world.refuses(&["resume", EPISODE, "--request", "more"], "closed");
    assert_eq!(world.launches().len(), launches);
    let events = world.ls(&format!("{EPISODE}/telemetry/events"));
    let row = json!({"hook_event_name": "Stop", "session_id": "12345678-1234-1234-1234-123456789012"});
    assert!(world.callback("codex", Some(id), &row.to_string()).status.success());
    assert_eq!(world.ls(&format!("{EPISODE}/telemetry/events")), events);
    assert_eq!(world.cli(&["status", EPISODE])["ending"]["reason"], "Collected.");
}

#[test]
fn one_episode_s_directory_cannot_hold_another() {
    let world = World::new();
    world.open("episodes/parent", "claude", &[]);
    let inside = world.opening("episodes/parent/child", "claude", &[]);
    world.refuses(&inside.iter().map(String::as_str).collect::<Vec<_>>(), "cannot hold another episode");
    let record = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/golden/python-0.26/claude/record");
    world.refuses(&["import", &text(&record), "episodes/parent/imported"], "cannot hold another episode");
    assert_eq!(world.ls("episodes/parent"), ["episode.json", "opening.md"]);
    world.open("episodes/other", "claude", &[]);
    world.refuses(&["library", "mv", "episodes/other", "episodes/parent/other"], "cannot hold another episode");
    world.cli(&["library", "mkdir", "episodes/group"]);
    world.cli(&["library", "mv", "episodes/other", "episodes/group/other"]);
    world.refuses(&["library", "mv", "episodes/group", "episodes/parent/group"], "cannot hold another episode");
    // A file the producer puts into an open episode's directory is not the record's, and closing says so.
    let note = world.base.join("note.md");
    std::fs::write(&note, "mine").unwrap();
    world.cli(&["library", "put", &text(&note), "episodes/parent/note.md"]);
    world.refuses(&["close", "episodes/parent", "--reason", "Done."], "unlisted note.md");
    world.cli(&["library", "mv", "episodes/parent/note.md", "note.md"]);
    let receipt = world.cli(&["close", "episodes/parent", "--reason", "Done."]);
    assert!(!world.json("episodes/parent/manifest.json")["members"].to_string().contains("note.md"));
    // Closing the other episode afterwards changes nothing in this record.
    world.cli(&["close", "episodes/group/other", "--reason", "Done."]);
    assert_eq!(world.cli(&["verify", "episodes/parent"])["sha256"], receipt["sha256"]);
}

#[test]
fn cleanup_does_nothing_through_a_link() {
    use std::os::unix::fs::PermissionsExt;
    let world = World::new();
    let state = world.open(EPISODE, "claude", &[&world.job]);
    world.cli(&["start", EPISODE]);
    let returns = Path::new(state["return"].as_str().unwrap()).to_path_buf();
    std::fs::create_dir(returns.join("notes")).unwrap();
    std::fs::write(returns.join("notes/first.md"), "first").unwrap();
    for directory in [returns.join("notes"), returns.clone()] {
        std::fs::set_permissions(directory, std::fs::Permissions::from_mode(0o500)).unwrap();
    }
    world.cli(&["close", EPISODE, "--reason", "Collected."]);
    for directory in [returns.clone(), returns.join("notes")] {
        std::fs::set_permissions(directory, std::fs::Permissions::from_mode(0o700)).unwrap();
    }
    // The collected directory is replaced by a link to one elsewhere that holds a file with the same bytes.
    let outside = world.base.join("outside");
    std::fs::create_dir_all(outside.join("empty")).unwrap();
    std::fs::write(outside.join("first.md"), "first").unwrap();
    std::fs::remove_dir_all(returns.join("notes")).unwrap();
    std::os::unix::fs::symlink(&outside, returns.join("notes")).unwrap();
    let again = world.cli(&["close", EPISODE, "--reason", "Collected."]);
    assert_eq!(std::fs::read_to_string(outside.join("first.md")).unwrap(), "first");
    assert_eq!(again["preserved"], json!([text(&returns.join("notes"))]));
    // Then the whole return directory is replaced by a link. Nothing behind it is touched.
    let moved = world.base.join("moved-return");
    std::fs::rename(&returns, &moved).unwrap();
    std::os::unix::fs::symlink(&outside, &returns).unwrap();
    let last = world.cli(&["close", EPISODE, "--reason", "Collected."]);
    assert!(outside.join("empty").is_dir() && outside.join("first.md").is_file());
    assert_eq!(last["preserved"], json!([text(&returns)]));
}

#[test]
fn a_version_saved_under_a_collected_name_is_put_back() {
    use std::os::unix::fs::PermissionsExt;
    let world = World::new();
    let state = world.open(EPISODE, "claude", &[&world.job]);
    world.cli(&["start", EPISODE]);
    let returns = Path::new(state["return"].as_str().unwrap()).to_path_buf();
    std::fs::set_permissions(&returns, std::fs::Permissions::from_mode(0o500)).unwrap();
    world.cli(&["close", EPISODE, "--reason", "Collected."]);
    std::fs::set_permissions(&returns, std::fs::Permissions::from_mode(0o700)).unwrap();
    // An interrupted cleanup had set the collected file aside, and an editor has since saved a newer version
    // under its name. The collected one goes, the newer one stays where it is.
    let aside = returns.with_file_name(format!("{}.retiring", returns.file_name().unwrap().to_string_lossy()));
    std::fs::create_dir(&aside).unwrap();
    std::fs::rename(returns.join("decision.md"), aside.join("0")).unwrap();
    std::fs::write(returns.join("decision.md"), "a newer version the producer has not received").unwrap();
    let again = world.cli(&["close", EPISODE, "--reason", "Collected."]);
    assert_eq!(again["preserved"], json!([text(&returns.join("decision.md"))]));
    assert!(std::fs::read_to_string(returns.join("decision.md")).unwrap().starts_with("a newer version"));
    assert!(!aside.exists());
    // A version that was set aside but is not the one collected is returned beside the newer file.
    std::fs::create_dir(&aside).unwrap();
    std::fs::write(aside.join("0"), "another version, never collected").unwrap();
    let last = world.cli(&["close", EPISODE, "--reason", "Collected."]);
    assert_eq!(last["preserved"].as_array().unwrap().len(), 2);
    assert_eq!(
        std::fs::read_to_string(returns.join("decision.md.earlier-1")).unwrap(),
        "another version, never collected"
    );
}

#[test]
fn a_changed_record_does_not_strand_the_return_or_lose_what_was_collected() {
    let world = World::new();
    let state = world.open(EPISODE, "claude", &[&world.job]);
    world.cli(&["start", EPISODE]);
    let returns = Path::new(state["return"].as_str().unwrap()).to_path_buf();
    std::fs::write(returns.join("second.md"), "second").unwrap();
    std::os::unix::fs::symlink(&world.job, returns.join("link")).unwrap();
    let receipt = world.cli(&["close", EPISODE, "--reason", "Collected."]);
    assert_eq!(receipt["preserved"], json!([text(&returns.join("link"))]));
    assert!(!returns.join("second.md").exists());
    // The producer removes the manifest and a member, then deals with the leftover and closes again.
    world.cli(&["library", "rm", &format!("{EPISODE}/manifest.json")]);
    world.cli(&["library", "rm", &format!("{EPISODE}/return/second.md")]);
    std::fs::remove_file(returns.join("link")).unwrap();
    world.refuses(&["close", EPISODE, "--reason", "Collected."], "manifest.json");
    // Nothing was collected a second time over what the record still holds, and the return is finished with.
    assert!(world.read(&format!("{EPISODE}/return/decision.md")).contains("two callers"));
    assert!(!returns.exists());
    assert_eq!(world.cli(&["status", EPISODE])["return"], Value::Null);
    world.cli(&["library", "rm", "--recursive", EPISODE]);
}

#[test]
fn cleanup_keeps_a_file_whose_copy_left_the_record() {
    use std::os::unix::fs::PermissionsExt;
    let world = World::new();
    let state = world.open(EPISODE, "claude", &[&world.job]);
    world.cli(&["start", EPISODE]);
    let returns = Path::new(state["return"].as_str().unwrap()).to_path_buf();
    std::fs::set_permissions(&returns, std::fs::Permissions::from_mode(0o500)).unwrap();
    world.cli(&["close", EPISODE, "--reason", "Collected."]);
    std::fs::set_permissions(&returns, std::fs::Permissions::from_mode(0o700)).unwrap();
    // The record's copy is removed before cleanup has run. The file on disk is then the only copy, and it stays.
    world.cli(&["library", "rm", &format!("{EPISODE}/return/decision.md")]);
    world.refuses(&["close", EPISODE, "--reason", "Collected."], "missing return/decision.md");
    assert!(std::fs::read_to_string(returns.join("decision.md")).unwrap().contains("two callers"));
    assert_eq!(world.cli(&["status", EPISODE])["preserved"], json!([text(&returns.join("decision.md"))]));
}

#[test]
fn a_command_that_inherits_a_sitting_s_environment_finds_the_same_places() {
    let mut world = World::new();
    let state = world.open(EPISODE, "claude", &[&world.job]);
    // A sitting is given the library's directory. With it, every place must come out as it does without it.
    world.env.insert("BERMAN_LIBRARY".into(), text(&world.data()));
    assert_eq!(world.cli(&["status", EPISODE])["return"], state["return"]);
    assert_eq!(world.cli(&["start", EPISODE])["exit"], 0);
    let receipt = world.cli(&["close", EPISODE, "--reason", "Collected."]);
    assert_eq!(receipt["coverage"]["limitations"], json!([]));
    assert!(world.exists(&format!("{EPISODE}/return/decision.md")));
    world.env.insert("BERMAN_LIBRARY".into(), "relative/place".into());
    world.refuses(&["library", "where"], "must be an absolute path");
}

#[test]
fn a_later_login_s_environment_does_not_move_the_return_or_a_dedicated_home() {
    let mut world = World::new();
    let state = world.open(EPISODE, "claude", &[&world.job]);
    let home = |world: &World| String::from_utf8(world.run(&["harness-home", "codex"]).stdout).unwrap();
    let dedicated = home(&world);
    world.cli(&["start", EPISODE]);
    // A different state directory, as another login session might supply.
    world.env.insert("XDG_STATE_HOME".into(), text(&world.base.join("another-state")));
    assert_eq!(world.cli(&["status", EPISODE])["return"], state["return"]);
    assert_eq!(home(&world), dedicated);
    let receipt = world.cli(&["close", EPISODE, "--reason", "Collected."]);
    assert_eq!(receipt["coverage"]["limitations"], json!([]));
    assert!(world.read(&format!("{EPISODE}/return/decision.md")).contains("two callers"));
    assert!(!Path::new(state["return"].as_str().unwrap()).exists());
}
