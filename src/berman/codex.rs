//! What Berman knows about Codex: its home, callbacks, launch, results, session store and transcripts.
//!
//! Codex reads the reviewed callbacks from its home and chooses its own session ID. The first
//! sitting's message, submitted in the checkout, binds that session to the episode. An unattended
//! sitting runs codex exec, whose JSONL events name the session it ran. Raw JSONL is always
//! retained; the reading copy is derived from it and never substitutes for it.

use std::io::{BufRead, BufReader, Read};
use std::path::{Path, PathBuf};

use serde_json::Value;

use crate::error::{At, Result, refuse};
use crate::files;
use crate::harness::{Launch, Outcome, Reading, blank, block, lines};
use crate::json::{display, text, truthy};

pub const HOME_VARIABLE: &str = "CODEX_HOME";
pub const DEFAULT_HOME: &str = ".codex";
pub const STORES: &[&str] = &["sessions", "archived_sessions"];
/// Codex grants callbacks at an interruption or the end of a session a short timeout.
pub const EVENTS: &[(&str, u64)] = &[("UserPromptSubmit", 30), ("Stop", 30), ("Interrupt", 3), ("SessionEnd", 3)];
const FIRST_LINE: u64 = 128 * 1024;

/// The latest opening sitting's first message, submitted in the checkout, binds Codex's session.
pub fn binds(state: &Value, event: &Value, first: Option<&str>) -> bool {
    let submitted = text(event, "hook_event_name") == Some("UserPromptSubmit");
    let Some(cwd) = text(event, "cwd").filter(|cwd| !cwd.is_empty()) else {
        return false;
    };
    submitted
        && first.is_some()
        && text(event, "prompt") == first
        && Some(files::text(&files::resolve(Path::new(cwd))).as_str()) == state["project"].as_str()
}

/// The callbacks come from the Codex home and must see the episode environment.
///
/// The interactive CLI needs --no-daemon for that. codex exec runs its session in its own process.
pub fn arguments(launch: &Launch) -> Result<Vec<String>> {
    let directory = ["--add-dir".to_string(), files::text(launch.return_directory)];
    let mut arguments: Vec<String> = Vec::new();
    let session = match (launch.resuming, launch.session) {
        (true, None) => return refuse("No Codex session is bound to resume"),
        (true, Some(session)) => Some(session.to_string()),
        (false, _) => None,
    };
    if launch.attended {
        arguments.push("--no-daemon".into());
        arguments.extend(session.is_some().then(|| "resume".to_string()));
        arguments.extend(directory);
        arguments.extend(session);
    } else {
        arguments.push("exec".into());
        arguments.extend(directory);
        arguments.push("--json".into());
        if let Some(session) = session {
            arguments.extend(["resume".to_string(), session]);
        }
    }
    Ok(arguments)
}

/// The session and final message in one codex exec event stream.
///
/// Codex tells the actor, not the event stream, when it refuses an operation, so there are no
/// denials to report. Each reconnection attempt is an error event. Those matter only when the
/// turn did not complete; the retained output keeps all of them.
pub fn outcome(output: &[u8]) -> Outcome {
    let (mut sessions, mut messages, mut limitations, mut errors) = (Vec::new(), Vec::new(), Vec::new(), Vec::new());
    let (mut finished, mut malformed, mut events) = (None, 0, 0);
    for line in lines(output) {
        if blank(line) {
            continue;
        }
        let Ok(event) = serde_json::from_slice::<Value>(line) else {
            malformed += 1;
            continue;
        };
        if !event.is_object() {
            continue;
        }
        events += 1;
        let kind = text(&event, "type").unwrap_or_default();
        let item = &event["item"];
        let problem = if kind == "turn.failed" { &event["error"] } else { &event };
        let reason = problem.get("message").filter(|m| truthy(m)).map(display).unwrap_or("no reason given".into());
        match kind {
            "thread.started" if text(&event, "thread_id").is_some() => {
                sessions.push(text(&event, "thread_id").unwrap_or_default().to_string());
            }
            "turn.started" => {
                messages.clear();
                finished = None;
            }
            "item.completed" if text(item, "type") == Some("agent_message") && text(item, "text").is_some() => {
                messages.push(text(item, "text").unwrap_or_default().to_string());
            }
            "turn.completed" => finished = Some("completed"),
            "turn.failed" => {
                finished = Some("failed");
                limitations.push(format!("The native turn failed: {reason}."));
            }
            "error" => errors.push(reason),
            _ => {}
        }
    }
    if malformed > 0 {
        limitations.push(format!("{malformed} native output lines were not JSON."));
    }
    let one = sessions.first().filter(|first| sessions.iter().all(|s| s == *first)).cloned();
    if !sessions.is_empty() && one.is_none() {
        limitations.push("The native output names more than one session.".into());
    }
    if events == 0 {
        limitations.push("The native output holds no events.".into());
    } else if finished.is_none() {
        limitations.push("The native output does not show the turn finishing.".into());
        if let Some(last) = errors.last() {
            limitations.push(format!("Its last reported error: {last}"));
        }
    } else if finished == Some("completed") && messages.is_empty() {
        limitations.push("The completed turn has no final message.".into());
    }
    Outcome {
        session: one,
        response: messages.pop().filter(|_| finished == Some("completed")),
        denials: Value::Null,
        limitations,
    }
}

/// The session ID recorded in a transcript's first line, or None.
fn meta_session(line: &[u8]) -> Option<String> {
    let value: Value = serde_json::from_slice(line).ok()?;
    if text(&value, "type") != Some("session_meta") {
        return None;
    }
    let meta = value.get("payload").filter(|payload| payload.is_object())?;
    let identity = meta.get("id").filter(|id| truthy(id)).or_else(|| meta.get("session_id"))?;
    identity.as_str().map(str::to_string)
}

fn walk(directory: &Path, session: &str, found: &mut Vec<PathBuf>) {
    let Ok(entries) = std::fs::read_dir(directory) else {
        return;
    };
    for entry in entries.flatten() {
        let Ok(kind) = entry.file_type() else {
            continue;
        };
        let name = entry.file_name();
        let name = name.to_string_lossy();
        if kind.is_dir() {
            walk(&entry.path(), session, found);
        } else if name.contains(session) && name.ends_with(".jsonl") {
            found.push(entry.path());
        }
    }
}

pub fn candidates(home: &Path, session: &str) -> Vec<PathBuf> {
    let mut found = Vec::new();
    for store in STORES {
        walk(&home.join(store), session, &mut found);
    }
    found
}

pub fn owns(path: &Path, session: &str) -> Result<bool> {
    files::regular(path)?;
    let mut line = Vec::new();
    let stream = std::fs::File::open(path).at(path)?;
    BufReader::new(stream.take(FIRST_LINE + 1)).read_until(b'\n', &mut line).at(path)?;
    Ok(line.len() as u64 <= FIRST_LINE && meta_session(&line).as_deref() == Some(session))
}

/// Whether a transcript's first line names the session.
pub fn belongs(first: &[u8], session: &str) -> Result<()> {
    if meta_session(first).as_deref() != Some(session) {
        return refuse("Codex transcript metadata differs from the assigned parent");
    }
    Ok(())
}

pub const HEADING: [&str; 6] = [
    "CODEX EPISODE SESSION: DERIVED READING COPY",
    "",
    "Source: transcript.jsonl. Each block cites its raw JSONL line and JSON pointer.",
    "Visible user/assistant messages, tool calls/results, and harness task errors.",
    "Reasoning, developer context, transport metadata and duplicate message events stay raw.",
    "",
];

/// Add one transcript line to the reading copy: visible response items and harness task errors.
pub fn line(number: usize, raw: &[u8], reading: &mut Reading) {
    if blank(raw) {
        return;
    }
    let Ok(event) = serde_json::from_slice::<Value>(raw) else {
        reading.malformed.push(number as u64);
        return;
    };
    if !event.is_object() {
        return;
    }
    let nothing = Value::Object(Default::default());
    let item = event.get("payload").unwrap_or(&nothing);
    if !item.is_object() {
        return;
    }
    let empty = Value::String(String::new());
    let mut kind = display(&item["type"]);
    let mut role = item.get("role").map(display).unwrap_or_default();
    let mut blocks: Vec<(String, &Value)> = Vec::new();
    if text(&event, "type") == Some("event_msg") && kind == "task_complete" && truthy(&item["error"]) {
        (role, kind) = ("harness".into(), "task_error".into());
        blocks.push(("/payload/error".into(), &item["error"]));
    } else if text(&event, "type") != Some("response_item") {
        return;
    } else if kind == "message" && matches!(text(item, "role"), Some("user" | "assistant")) {
        let mut texts = String::new();
        for (i, part) in item["content"].as_array().into_iter().flatten().enumerate() {
            if let Some(said) = text(part, "text").filter(|_| part.is_object()) {
                texts.push_str(said);
                blocks.push((format!("/payload/content/{i}/text"), &part["text"]));
            }
        }
        if role == "assistant" {
            reading.assistant.push(files::digest(texts.as_bytes()));
        }
    } else if kind == "function_call" || kind == "custom_tool_call" {
        let field = if kind == "function_call" { "arguments" } else { "input" };
        blocks.push((format!("/payload/{field}"), item.get(field).unwrap_or(&empty)));
    } else if kind == "function_call_output" || kind == "custom_tool_call_output" {
        blocks.push(("/payload/output".into(), item.get("output").unwrap_or(&empty)));
    }
    let time = event.get("timestamp").map(display).unwrap_or_else(|| "not recorded".into());
    let name = item.get("name").map(display).unwrap_or_default();
    let call = item.get("call_id").map(display).unwrap_or_default();
    for (pointer, content) in blocks {
        let heading =
            [format!("TIME: {time} | ROLE: {role} | KIND: {kind}"), format!("NAME: {name} | CALL ID: {call}")];
        block(&mut reading.text, number, &pointer, heading, content);
    }
}
