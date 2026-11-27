//! A scripted harness used to exercise the installed command without a model.
//!
//! Copied to a file named `codex` or `claude`, it answers each message with a numbered reply in
//! its native transcript and runs the episode callbacks. In Claude print mode or codex exec it
//! reports its result on stdout. Environment variables named FIXTURE_* select the failures a
//! test needs.

use std::io::{IsTerminal, Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio, exit};

use berman::json::line;
use serde_json::{Value, json};

fn option(name: &str) -> Option<String> {
    std::env::var(name).ok().filter(|value| !value.is_empty())
}

fn after<'a>(arguments: &'a [String], flag: &str) -> Option<&'a str> {
    arguments.iter().position(|a| a == flag).and_then(|at| arguments.get(at + 1)).map(String::as_str)
}

/// Run the callback a harness would run, giving it the event on standard input.
fn run_hook(command: &str, event: &Value) {
    let mut child = Command::new("sh")
        .args(["-c", command])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .expect("the callback starts");
    child.stdin.take().expect("a pipe").write_all(line(event).as_bytes()).expect("the callback reads its event");
    let output = child.wait_with_output().expect("the callback ends");
    assert!(output.status.success(), "callback failed: {}", String::from_utf8_lossy(&output.stderr));
    assert_eq!(serde_json::from_slice::<Value>(&output.stdout).expect("JSON from the callback"), json!({}));
}

fn hook_command(definition: &Path, event: &str) -> String {
    let hooks: Value = serde_json::from_slice(&std::fs::read(definition).expect("hook settings")).expect("JSON");
    hooks["hooks"][event][0]["hooks"][0]["command"].as_str().expect("a command").to_string()
}

fn append(path: &Path, lines: &[Value]) {
    let mut file = std::fs::OpenOptions::new().create(true).append(true).open(path).expect("the transcript opens");
    for value in lines {
        writeln!(file, "{}", line(value)).expect("the transcript is written");
    }
}

fn main() {
    if option("FIXTURE_DROP_LEASE").is_some() {
        // The Codex binary does not keep descriptors it inherits beyond the standard three.
        for descriptor in 3..1024 {
            // SAFETY: closing a descriptor this process does not use has no effect on it.
            unsafe { libc::close(descriptor) };
        }
    }
    let mut all = std::env::args();
    let actor =
        PathBuf::from(all.next().expect("a program name")).file_name().expect("a name").to_string_lossy().into_owned();
    let arguments: Vec<String> = all.collect();
    let project = std::env::current_dir().expect("a working directory");
    let codex = actor == "codex";
    let home = PathBuf::from(option(if codex { "CODEX_HOME" } else { "CLAUDE_CONFIG_DIR" }).expect("a harness home"));
    let episode = option("BERMAN_EPISODE");
    if let Some(log) = option("FIXTURE_LOG") {
        append(Path::new(&log), &[json!({"actor": actor, "args": arguments, "episode": episode})]);
    }
    std::fs::write(project.join("ordinary.txt"), "ordinary project work\n").expect("ordinary work");

    let Some(episode) = episode else {
        if option("FIXTURE_INDEPENDENT").is_some() {
            // Ordinary work in the same checkout and home leaves its own transcript and runs any home callbacks.
            let session = berman::files::uuid();
            let (transcript, first) = if codex {
                (
                    home.join("sessions").join(format!("{session}.jsonl")),
                    json!({"type": "session_meta", "payload": {"id": session}}),
                )
            } else {
                (
                    home.join("projects/example").join(format!("{session}.jsonl")),
                    json!({"sessionId": session, "type": "system"}),
                )
            };
            std::fs::create_dir_all(transcript.parent().expect("a parent")).expect("the session store");
            append(&transcript, &[first]);
            std::fs::OpenOptions::new()
                .append(true)
                .open(&transcript)
                .and_then(|mut f| f.write_all(b"independent work\n"))
                .expect("work");
            let hooks = home.join("hooks.json");
            if hooks.exists() {
                let event = json!({"hook_event_name": "Stop", "session_id": session, "transcript_path": transcript});
                run_hook(&hook_command(&hooks, "Stop"), &event);
            }
        }
        exit(0);
    };

    let out = PathBuf::from(after(&arguments, "--add-dir").expect("a return directory"));
    assert!(out.is_dir(), "the return directory exists");
    let prompt = after(&arguments, "--").unwrap_or_default().to_string();
    let (unattended, session, definition, transcript, first);
    if codex {
        unattended = arguments.first().map(String::as_str) == Some("exec");
        assert!(unattended || arguments.iter().any(|a| a == "--no-daemon"));
        session = "12345678-1234-1234-1234-123456789012".to_string();
        if let Some(at) = arguments.iter().position(|a| a == "resume") {
            assert!(arguments[at..].contains(&session));
        }
        definition = home.join("hooks.json");
        transcript = home.join("sessions").join(format!("{session}.jsonl"));
        first = json!({"type": "session_meta", "payload": {"id": session}});
    } else {
        unattended = arguments.iter().any(|a| a == "--print");
        if unattended {
            assert_eq!(after(&arguments, "--output-format"), Some("json"));
        }
        session = after(&arguments, "--resume").or(after(&arguments, "--session-id")).expect("a session").to_string();
        definition = PathBuf::from(after(&arguments, "--settings").expect("settings"));
        transcript = home.join("projects/example").join(format!("{session}.jsonl"));
        first = json!({"sessionId": session, "type": "system"});
    }
    if unattended {
        let mut input = String::new();
        assert!(!std::io::stdin().is_terminal());
        std::io::stdin().read_to_string(&mut input).expect("standard input");
        assert_eq!(input, "");
    }
    if option("FIXTURE_NO_SESSION").is_some() {
        // The CLI fails before it starts a session, as Codex did when it could not reach its service.
        eprintln!("could not reach the service");
        exit(1);
    }
    let callback = |event: &str, prompt: &str| {
        let payload = json!({
            "hook_event_name": event,
            "session_id": session,
            "cwd": project,
            "prompt": prompt,
            "transcript_path": transcript,
        });
        run_hook(&hook_command(&definition, event), &payload);
    };
    let decides = home.join(format!("fixture-decides-{session}"));
    if !transcript.exists() {
        if option("FIXTURE_EARLY_HOOK").is_some() {
            // Claude Code calls SessionStart before it writes the transcript.
            callback("SessionStart", "");
        }
        assert!(
            prompt.starts_with(&format!("Berman episode {episode}\n")),
            "the first message begins with the opening"
        );
        assert!(prompt.contains(&format!("Return directory: {}\n", out.display())));
        std::fs::create_dir_all(transcript.parent().expect("a parent")).expect("the session store");
        append(&transcript, &[first]);
        if prompt.contains("Record a decision") {
            std::fs::write(&decides, "").expect("a note for later sittings");
        }
    }
    let said = std::fs::read_to_string(&transcript).expect("the transcript");
    let turns = said.matches("\"role\": \"user\"").count() + 1;
    let last = prompt.lines().last().filter(|_| !prompt.is_empty()).unwrap_or("resumed");
    let reply = format!("done {turns}: {last}");
    let turn: Vec<Value> = [("user", prompt.as_str()), ("assistant", reply.as_str())]
        .into_iter()
        .map(|(role, text)| {
            if codex {
                json!({"type": "response_item", "payload": {"type": "message", "role": role, "content": [{"text": text}]}})
            } else {
                json!({"sessionId": session, "message": {"role": role, "content": text}})
            }
        })
        .collect();
    append(&transcript, &turn);
    if decides.exists() {
        std::fs::write(out.join("decision.md"), "We kept the reader because two callers still use it.\n")
            .expect("the return");
    }
    if let Some(seconds) = option("FIXTURE_SLEEP") {
        std::thread::sleep(std::time::Duration::from_secs_f64(seconds.parse().expect("seconds")));
    }
    if option("FIXTURE_WITHOUT_HOOKS").is_none() {
        for event in ["UserPromptSubmit", "Stop", "SessionEnd"] {
            callback(event, &prompt);
        }
    }
    if !unattended {
        exit(0);
    }
    let failed = option("FIXTURE_FAIL").is_some();
    let reported = option("FIXTURE_REPORTED_SESSION").unwrap_or(session.clone());
    if option("FIXTURE_SILENT").is_some() {
        eprintln!("native failure detail");
    } else if !codex {
        let denials =
            json!([{"tool_name": "Write", "tool_use_id": "toolu_1", "tool_input": {"file_path": out.join("x.md")}}]);
        // A failed request can still carry subtype success, with the error text as its result.
        let result = json!({
            "type": "result",
            "subtype": "success",
            "is_error": failed,
            "result": if failed { "API Error: overloaded" } else { reply.as_str() },
            "session_id": reported,
            "permission_denials": if option("FIXTURE_DENY").is_some() { denials } else { json!([]) },
        });
        println!("{}", line(&result));
    } else {
        // Codex reports reconnection attempts as errors whether or not the turn later completes.
        let mut events = vec![
            json!({"type": "thread.started", "thread_id": reported}),
            json!({"type": "turn.started"}),
            json!({"type": "error", "message": "Reconnecting... 1/5 (stream disconnected)"}),
            // A turn can report progress in a message and still fail.
            json!({"type": "item.completed", "item": {"id": "item_1", "type": "agent_message", "text": reply}}),
        ];
        events.push(if failed {
            json!({"type": "turn.failed", "error": {"message": "stream disconnected"}})
        } else {
            json!({"type": "turn.completed", "usage": {}})
        });
        for event in events {
            println!("{}", line(&event));
        }
    }
    if let Some(gate) = option("FIXTURE_DELAY_STDERR") {
        // A descendant keeps the stderr pipe after its parent exits. It then reports its final bytes.
        #[allow(clippy::zombie_processes)]
        let _writer = Command::new("sh").args(["-c", "i=0; while test ! -f \"$1\" && test $i -lt 400; do sleep .1; i=$((i+1)); done; echo delayed-report >&2", "writer", &gate])
            .stdout(Stdio::null()).spawn().unwrap();
    }
    if failed {
        eprintln!("the turn could not finish");
        exit(1);
    }
}
