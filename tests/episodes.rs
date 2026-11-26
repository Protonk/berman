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
fn closing_finds_a_claude_transcript_when_no_callback_arrived() {
    let mut world = World::new();
    world.env.insert("FIXTURE_WITHOUT_HOOKS".into(), "1".into());
    world.open(EPISODE, "claude", &[&world.job]);
    world.cli(&["start", EPISODE]);
    assert!(!world.exists(&format!("{EPISODE}/telemetry")));
    let receipt = world.cli(&["close", EPISODE, "--reason", "The callbacks never ran."]);
    world.assert_documented(EPISODE);
    assert!(world.read(&format!("{EPISODE}/telemetry/parent/conversation.txt")).contains("done"));
    let limitations = receipt["coverage"]["limitations"].as_array().unwrap();
    assert!(limitations.contains(&json!("No session callbacks were observed.")));
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
