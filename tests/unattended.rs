//! Unattended sittings: a producer corresponds with a scripted actor through Berman's ordinary commands.
//!
//! These checks establish Berman's handling of the scripted native output. They are not evidence of
//! how the real Claude Code or Codex CLIs behave.

mod support;

use std::os::unix::process::CommandExt;
use std::process::{Child, Stdio};
use std::time::{Duration, Instant};

use serde_json::{Value, json};
use support::{ACTORS, World, berman, git, text};

fn wait_for(mut condition: impl FnMut() -> bool) {
    let deadline = Instant::now() + Duration::from_secs(20);
    while !condition() {
        assert!(Instant::now() < deadline, "condition not reached");
        std::thread::sleep(Duration::from_millis(50));
    }
}

/// Run Berman in its own process group so a test can signal it alone or with the actor.
fn background(world: &World, arguments: &[&str]) -> Child {
    let mut command = world.command(&berman());
    command.args(arguments).stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::piped()).process_group(0);
    let child = command.spawn().expect("berman starts");
    let episode = arguments[1];
    wait_for(|| {
        let recorded = world.run(&["library", "cat", &format!("{episode}/sittings/000001.json")]);
        let started =
            serde_json::from_slice::<Value>(&recorded.stdout).is_ok_and(|sitting| sitting.get("pid").is_some());
        started && !world.launches().is_empty()
    });
    std::thread::sleep(Duration::from_millis(300));
    child
}

fn limitations(value: &Value) -> Vec<String> {
    value["limitations"].as_array().unwrap().iter().map(|item| item.as_str().unwrap().to_string()).collect()
}

#[test]
fn conversation_without_an_additional_job() {
    let world = World::new();
    for actor in ACTORS {
        let episode = format!("episodes/{actor}-plain");
        let state = world.open(&episode, actor, &[]);
        assert_eq!(state["jobs"], json!([]));
        let opening = state["opening"].as_str().unwrap();
        assert!(opening.contains("no additional job") && !opening.contains("Continue"));
        let messages = ["What is in this checkout?", "Which test is slowest?", "Leave it for now."];
        let mut sittings = vec![world.send(&episode, None, messages[0])];
        for (number, message) in messages.iter().enumerate().skip(1) {
            sittings.push(world.send(&episode, Some(&format!("follow-up-{}", number + 1)), message));
        }
        for (number, (sitting, message)) in sittings.iter().zip(messages).enumerate() {
            assert_eq!(sitting["attended"], false);
            assert_eq!(sitting["exit"], 0);
            assert_eq!(sitting["response"], format!("done {}: {message}", number + 1));
            assert_eq!(sitting["denials"], if actor == "claude" { json!([]) } else { Value::Null });
            assert_eq!(sitting["limitations"], json!([]));
            assert_eq!(sitting["native_session"], sittings[0]["native_session"]);
        }
        let status = world.cli(&["status", &episode]);
        assert_eq!(status["ending"], Value::Null);
        assert_eq!(status["sittings"], json!(sittings));
        let returns = std::path::PathBuf::from(status["return"].as_str().unwrap());
        assert_eq!(std::fs::read_dir(&returns).unwrap().count(), 0);
        let receipt = world.cli(&["close", &episode, "--reason", "I know enough to decide."]);
        assert_eq!(receipt["coverage"]["limitations"], json!([]));
        assert_eq!((&receipt["return"], &receipt["preserved"]), (&Value::Null, &json!([])));
        assert!(!returns.exists());
        world.assert_documented(&episode);
        assert!(!world.exists(&format!("{episode}/return")));
        assert!(
            world.read(&format!("{episode}/telemetry/parent/transcript.jsonl")).contains("Message from the producer")
        );
        let conversation = world.read(&format!("{episode}/telemetry/parent/conversation.txt"));
        for (number, message) in messages.iter().enumerate() {
            assert!(conversation.contains(&format!("done {}: {message}", number + 1)));
        }
        assert!(world.exists(&format!("{episode}/sittings/000003.stdout")));
        assert_eq!(world.json(&format!("{episode}/sittings/000003.json")), sittings[2]);
    }
}

#[test]
fn additional_jobs_stay_apart_from_responses() {
    let world = World::new();
    let templates = world.base.join("jobs");
    world.cli(&["template", &text(&templates)]);
    for actor in ACTORS {
        let episode = format!("episodes/{actor}-jobs");
        let state = world.open(&episode, actor, &[&world.job, &templates.join("handover.md")]);
        let opening = state["opening"].as_str().unwrap();
        assert!(opening.contains("these additional jobs") && opening.contains("HANDOVER.md"));
        let first = world.send(&episode, None, "Pick up where the last session stopped.");
        let second = world.send(&episode, Some("check"), "Did the reader change?");
        let returns = std::path::PathBuf::from(state["return"].as_str().unwrap());
        let returned = std::fs::read_to_string(returns.join("decision.md")).unwrap();
        for sitting in [&first, &second] {
            let response = sitting["response"].as_str().unwrap();
            assert!(response.starts_with("done") && !response.contains(returned.trim()));
        }
        world.cli(&["close", &episode, "--reason", "The handover question is settled."]);
        assert_eq!(world.read(&format!("{episode}/return/decision.md")), returned);
        assert_eq!(world.ls(&format!("{episode}/return")), ["decision.md"]);
        assert_eq!(world.json(&format!("{episode}/sittings/000002.json"))["response"], second["response"]);
        world.cli(&["verify", &episode]);
    }
}

#[test]
fn attendance_belongs_to_each_sitting_of_one_session() {
    let world = World::new();
    for actor in ACTORS {
        let episode = format!("episodes/{actor}-mixed");
        world.open(&episode, actor, &[&world.job]);
        let attended = world.cli(&["start", &episode, "--prompt", "Look at the failing build first."]);
        assert_eq!(attended["attended"], true);
        assert!(attended.get("response").is_none());
        let bound = world.cli(&["status", &episode])["session"].clone();
        assert!(bound.is_string());
        let second = world.send(&episode, Some("away"), "Carry on while I am out.");
        let third = world.cli(&["resume", &episode, "--request", "back"]);
        let fourth = world.send(&episode, Some("later"), "Summarize where things stand.");
        assert_eq!(second["response"], "done 2: Carry on while I am out.");
        assert_eq!(fourth["response"], "done 4: Summarize where things stand.");
        assert_eq!((&second["native_session"], &fourth["native_session"]), (&bound, &bound));
        let status = world.cli(&["status", &episode]);
        let attendance: Vec<&Value> = status["sittings"].as_array().unwrap().iter().map(|s| &s["attended"]).collect();
        assert_eq!(attendance, [&json!(true), &json!(false), &json!(true), &json!(false)]);
        assert_eq!(status["ending"], Value::Null);
        assert!(third["exit"] == 0 && status["sittings"][2] == third);
        world.cli(&["close", &episode, "--reason", "Back at the desk."]);
        world.cli(&["verify", &episode]);
    }
}

#[test]
fn repeated_requests_return_the_recorded_sitting() {
    let world = World::new();
    let episode = "episodes/repeated";
    world.open(episode, "codex", &[]);
    let first = world.send(episode, None, "Begin with the tests.");
    assert_eq!(world.send(episode, None, "Begin with the tests."), first);
    world.refuses(&world.sending(episode, None, "Something else."), "different instructions");
    world.refuses(&["start", episode, "--prompt", "Begin with the tests."], "different instructions");
    let second = world.send(episode, Some("next"), "Then the docs.");
    assert_eq!(world.send(episode, Some("next"), "Then the docs."), second);
    world.refuses(&["resume", episode, "--request", "next", "--prompt", "Then the docs."], "different");
    assert_eq!(world.launches().len(), 2);
    world.refuses(&["start", episode, "--unattended"], "--prompt");
    world.refuses(&world.sending(episode, Some("blank"), " "), "nonempty prompt");
    assert_eq!(world.cli(&["status", episode])["sittings"].as_array().unwrap().len(), 2);
}

#[test]
fn unsuccessful_and_silent_sittings_report_no_response() {
    let mut world = World::new();
    for actor in ACTORS {
        let episode = format!("episodes/{actor}-failing");
        world.open(&episode, actor, &[]);
        world.env.insert("FIXTURE_FAIL".into(), "1".into());
        let failed = world.cli_code(&world.sending(&episode, None, "Try the migration."), 1);
        world.env.remove("FIXTURE_FAIL");
        assert_eq!((&failed["response"], &failed["exit"]), (&Value::Null, &json!(1)));
        let found = limitations(&failed);
        assert_eq!(found.len(), 2);
        assert!(found[0].contains("reports an error") || found[0].contains("turn failed"));
        assert!(found[1].contains("the turn could not finish"));
        world.env.insert("FIXTURE_SILENT".into(), "1".into());
        let silent = world.send(&episode, Some("silent"), "Anything?");
        world.env.remove("FIXTURE_SILENT");
        assert_eq!((&silent["exit"], &silent["response"]), (&json!(0), &Value::Null));
        assert!(limitations(&silent)[0].contains("holds no "));
        world.env.insert("FIXTURE_DENY".into(), "1".into());
        let denied = world.send(&episode, Some("denied"), "Write the note.");
        world.env.remove("FIXTURE_DENY");
        assert_eq!(denied["response"], "done 3: Write the note.");
        // Codex reports refusals to the actor only, so Berman has none to list.
        match actor {
            "claude" => assert_eq!(denied["denials"].as_array().unwrap().len(), 1),
            _ => assert_eq!(denied["denials"], Value::Null),
        }
        assert_eq!(silent["denials"], Value::Null);
        let after = world.send(&episode, Some("after"), "Try again more simply.");
        assert_eq!(after["response"], "done 4: Try again more simply.");
        assert_eq!(world.cli(&["status", &episode])["ending"], Value::Null);
        let receipt = world.cli(&["close", &episode, "--reason", "The migration waits for review."]);
        let found = limitations(&receipt["coverage"]);
        assert!(found.iter().any(|item| item.starts_with("sittings/000001.json: ")));
        assert!(found.iter().any(|item| item.starts_with("sittings/000002.json: ")));
    }
}

#[test]
fn interrupt_reaches_the_actor_and_leaves_the_episode_open() {
    let mut world = World::new();
    let episode = "episodes/interrupted";
    world.open(episode, "claude", &[]);
    world.env.insert("FIXTURE_SLEEP".into(), "30".into());
    let process = background(&world, &["start", episode, "--unattended", "--prompt", "Run the long check."]);
    // SAFETY: the process group is the one this test created for the launcher and its actor.
    assert_eq!(unsafe { libc::killpg(process.id() as i32, libc::SIGINT) }, 0);
    let output = process.wait_with_output().unwrap();
    assert_eq!(output.status.code(), Some(130), "{}", String::from_utf8_lossy(&output.stderr));
    let sitting: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!((&sitting["exit"], &sitting["response"]), (&json!(-libc::SIGINT), &Value::Null));
    assert!(limitations(&sitting).last().unwrap().contains("ended on signal 2"));
    world.env.remove("FIXTURE_SLEEP");
    assert_eq!(
        world.send(episode, Some("shorter"), "Run the quick check.")["response"],
        "done 2: Run the quick check."
    );
}

/// Repeat the first request until the episode is free, as a producer would after losing the launcher.
fn retry(world: &World, episode: &str) -> Value {
    let mut retried = None;
    wait_for(|| {
        let result = world.run(&world.sending(episode, None, "Survey the modules."));
        let said = String::from_utf8_lossy(&result.stderr);
        if said.contains("in progress") || said.contains("still running") {
            return false;
        }
        assert!(result.status.success(), "{said}");
        retried = Some(serde_json::from_slice(&result.stdout).unwrap());
        true
    });
    retried.unwrap()
}

#[test]
fn lost_launcher_neither_relaunches_nor_invents_an_exit() {
    let mut world = World::new();
    let episode = "episodes/lost";
    world.open(episode, "codex", &[]);
    world.env.insert("FIXTURE_SLEEP".into(), "2".into());
    let mut process = background(&world, &["start", episode, "--unattended", "--prompt", "Survey the modules."]);
    process.kill().unwrap();
    process.wait().unwrap();
    world.refuses(&world.sending(episode, None, "Survey the modules."), "in progress");
    let sitting = retry(&world, episode);
    assert_eq!(sitting["exit"], Value::Null);
    // The output reached the library without the launcher, so the retry can report what the actor said.
    assert_eq!(sitting["response"], "done 1: Survey the modules.");
    assert_eq!(limitations(&sitting), ["The launcher did not observe the process exit."]);
    assert_eq!(world.send(episode, None, "Survey the modules."), sitting);
    assert_eq!(world.launches().len(), 1);
    let receipt = world.cli(&["close", episode, "--reason", "Enough for today."]);
    assert!(limitations(&receipt["coverage"]).contains(&"sittings/000001.json: process exit unobserved.".to_string()));
}

#[test]
fn an_actor_that_drops_the_lease_still_holds_the_episode() {
    let mut world = World::new();
    let episode = "episodes/dropped";
    world.open(episode, "codex", &[]);
    world.env.insert("FIXTURE_SLEEP".into(), "2".into());
    world.env.insert("FIXTURE_DROP_LEASE".into(), "1".into());
    let mut process = background(&world, &["start", episode, "--unattended", "--prompt", "Survey the modules."]);
    process.kill().unwrap();
    process.wait().unwrap();
    world.refuses(&world.sending(episode, None, "Survey the modules."), "still running as process");
    world.refuses(&["close", episode, "--reason", "Too early."], "still running as process");
    world.refuses(&world.sending(episode, Some("other"), "Something else."), "still running as process");
    assert_eq!(retry(&world, episode)["response"], "done 1: Survey the modules.");
    assert_eq!(world.launches().len(), 1);
}

#[test]
fn an_opening_that_started_no_session_can_be_tried_again() {
    let mut world = World::new();
    for actor in ACTORS {
        let episode = format!("episodes/{actor}-again");
        world.open(&episode, actor, &[]);
        world.env.insert("FIXTURE_NO_SESSION".into(), "1".into());
        let failed = world.cli_code(&world.sending(&episode, None, "Begin."), 1);
        world.env.remove("FIXTURE_NO_SESSION");
        assert_eq!(failed["response"], Value::Null);
        assert!(limitations(&failed).last().unwrap().contains("could not reach the service"));
        assert_eq!(world.cli_code(&world.sending(&episode, None, "Begin."), 1), failed);
        world.refuses(&world.sending(&episode, Some("next"), "More."), "No native session is known");
        let again = ["start", &episode, "--request", "again", "--unattended", "--prompt", "Begin."];
        let retried = world.cli(&again);
        assert_eq!(retried["opening"], true);
        assert_eq!(retried["response"], "done 1: Begin.");
        let launches = world.launches().len();
        assert_eq!(world.cli(&again), retried);
        assert_eq!(world.launches().len(), launches);
        world.refuses(&world.sending(&episode, Some("again"), "Begin."), "different");
        assert_eq!(world.send(&episode, Some("next"), "More.")["response"], "done 2: More.");
        world.refuses(&["start", &episode, "--request", "third", "--prompt", "Begin."], "already started");
        world.cli(&["close", &episode, "--reason", "The second attempt reached the actor."]);
        assert!(world.read(&format!("{episode}/telemetry/parent/conversation.txt")).contains("done 2: More."));
    }
}

#[test]
fn codex_hook_binds_an_attended_second_opening() {
    let mut world = World::new();
    let episode = "episodes/second-opening";
    world.open(episode, "codex", &[]);
    world.env.insert("FIXTURE_NO_SESSION".into(), "1".into());
    world.cli_code(&["start", episode], 1);
    world.env.remove("FIXTURE_NO_SESSION");
    world.refuses(&["resume", episode, "--request", "next"], "No native session is known");
    assert_eq!(world.cli(&["status", episode])["session"], Value::Null);
    world.cli(&["start", episode, "--request", "again", "--prompt", "Begin with the tests."]);
    assert!(world.cli(&["status", episode])["session"].is_string());
    assert_eq!(world.cli(&["resume", episode, "--request", "next"])["exit"], 0);
}

#[test]
fn a_callback_before_the_transcript_exists_is_not_a_gap() {
    let mut world = World::new();
    let episode = "episodes/early";
    world.env.insert("FIXTURE_EARLY_HOOK".into(), "1".into());
    let state = world.open(episode, "claude", &[]);
    world.send(episode, None, "Begin.");
    let events = format!("{episode}/telemetry/events");
    let rows: Vec<Value> = world.ls(&events).iter().map(|name| world.json(&format!("{events}/{name}"))).collect();
    let early: Vec<&Value> = rows.iter().filter(|row| row["event"]["hook_event_name"] == "SessionStart").collect();
    assert_eq!(early.len(), 1);
    assert_eq!(early[0]["observation"], "The native transcript did not exist yet.");
    assert!(early[0].get("limitation").is_none());
    // Once a transcript has been captured, its absence is a gap.
    let gone = world.base.join(".claude/projects/example/gone.jsonl");
    let event = json!({"hook_event_name": "Stop", "session_id": state["id"], "transcript_path": gone});
    assert!(world.callback("claude", state["id"].as_str(), &event.to_string()).status.success());
    let receipt = world.cli(&["close", episode, "--reason", "Checked."]);
    let found = limitations(&receipt["coverage"]);
    assert_eq!(found.iter().filter(|item| item.contains("gone.jsonl")).count(), 1);
    assert!(!found.iter().any(|item| item.contains("SessionStart") || item.contains("did not exist")));
}

#[test]
fn codex_output_binds_a_session_no_callback_bound() {
    let mut world = World::new();
    let episode = "episodes/unreviewed";
    world.env.insert("FIXTURE_WITHOUT_HOOKS".into(), "1".into());
    world.open(episode, "codex", &[]);
    let first = world.send(episode, None, "Look around.");
    assert!(!world.exists(&format!("{episode}/telemetry")));
    assert_eq!(world.cli(&["status", episode])["session"], first["native_session"]);
    assert_eq!(world.send(episode, Some("more"), "And the docs?")["response"], "done 2: And the docs?");
    let receipt = world.cli(&["close", episode, "--reason", "The callbacks were never reviewed."]);
    assert!(limitations(&receipt["coverage"]).contains(&"No session callbacks were observed.".to_string()));
    assert!(world.read(&format!("{episode}/telemetry/parent/conversation.txt")).contains("done 2"));
}

#[test]
fn output_naming_another_session_does_not_rebind() {
    let mut world = World::new();
    for actor in ACTORS {
        let episode = format!("episodes/{actor}-other");
        world.open(&episode, actor, &[]);
        let bound = world.send(&episode, None, "Begin.")["native_session"].clone();
        world.env.insert("FIXTURE_REPORTED_SESSION".into(), "another-session".into());
        let sitting = world.send(&episode, Some("next"), "Continue.");
        world.env.remove("FIXTURE_REPORTED_SESSION");
        assert_eq!(sitting["native_session"], "another-session");
        assert!(limitations(&sitting)[0].contains(&format!("not the bound session {}", bound.as_str().unwrap())));
        assert_eq!(world.cli(&["status", &episode])["session"], bound);
    }
}

#[test]
fn the_checkout_can_change_between_every_encounter() {
    let mut world = World::new();
    for actor in ACTORS {
        world.project = world.base.join(format!("{actor} checkout"));
        std::fs::create_dir(&world.project).unwrap();
        let project = world.project.clone();
        git(&project, &["init", "-q"]);
        let episode = format!("episodes/{actor}-changing");
        let state = world.open(&episode, actor, &[]);
        std::fs::write(project.join("plan.md"), "A colleague changed direction.\n").unwrap();
        git(&project, &["add", "plan.md"]);
        git(&project, &["commit", "-q", "-m", "Change direction"]);
        git(&project, &["switch", "-q", "-c", "elsewhere"]);
        assert_eq!(world.send(&episode, None, "What changed?")["exit"], 0);
        std::fs::remove_file(project.join("ordinary.txt")).unwrap();
        git(&project, &["rm", "-q", "plan.md"]);
        git(&project, &["commit", "-q", "-m", "Someone finished the work"]);
        assert_eq!(world.send(&episode, Some("again"), "And now?")["response"], "done 2: And now?");
        std::fs::write(project.join("late.md"), "Another change before closing.\n").unwrap();
        git(&project, &["add", "late.md"]);
        git(&project, &["commit", "-q", "-m", "Late change"]);
        let receipt = world.cli(&["close", &episode, "--reason", "The work went another way."]);
        assert_eq!(receipt["coverage"]["limitations"], json!([]));
        assert!(receipt["coverage"]["scope"].as_str().unwrap().contains("not who changed it"));
        let last = world.json(&format!("{episode}/final-project.json"));
        assert_eq!(last["head"], git(&project, &["rev-parse", "HEAD"]));
        assert_eq!(state["initial_project"]["head"], Value::Null);
        assert!(state["initial_project"]["observed_at"].as_str() < last["observed_at"].as_str());
    }
}

#[test]
fn an_unavailable_checkout_refuses_without_a_sitting() {
    let world = World::new();
    let episode = "episodes/moved";
    world.open(episode, "claude", &[]);
    let moved = world.base.join("moved away");
    std::fs::rename(&world.project, &moved).unwrap();
    world.refuses(&world.sending(episode, None, "Begin."), "checkout is unavailable");
    assert!(!world.exists(&format!("{episode}/sittings")));
    std::fs::rename(&moved, &world.project).unwrap();
    assert_eq!(world.send(episode, None, "Begin.")["exit"], 0);
}

#[test]
fn independent_sessions_stay_outside_the_episode() {
    let world = World::new();
    for actor in ACTORS {
        let episode = format!("episodes/{actor}-neighbours");
        world.open(&episode, actor, &[]);
        let bound = world.send(&episode, None, "Begin.")["native_session"].clone();
        let events = world.ls(&format!("{episode}/telemetry/events"));
        let independent = || {
            let mut command = world.command(std::path::Path::new(actor));
            let status = command.current_dir(&world.project).env("FIXTURE_INDEPENDENT", "1").status().unwrap();
            assert!(status.success());
        };
        independent();
        assert_eq!(world.ls(&format!("{episode}/telemetry/events")), events);
        world.send(&episode, Some("next"), "Continue.");
        independent();
        world.cli(&["close", &episode, "--reason", "Others carried on without Berman."]);
        assert_eq!(world.cli(&["status", &episode])["session"], bound);
        for name in world.files(&episode) {
            let member = world.cat(&format!("{episode}/{name}"));
            assert!(!member.windows(16).any(|window| window == b"independent work"), "{name}");
        }
    }
}
