//! What Berman knows about Claude Code: its home, callbacks, launch, results, session store and transcripts.
//!
//! Berman launches Claude Code with the episode ID as its session ID and passes the callbacks as
//! per-launch settings. An unattended sitting runs in print mode and reports one JSON result. Raw
//! JSONL is always retained; the reading copy is derived from it and never substitutes for it.

use std::path::{Path, PathBuf};

use serde_json::{Value, json};

use crate::error::{Result, refuse};
use crate::files;
use crate::harness::{Launch, Outcome, Reading, blank, block};
use crate::json::{display, text, truthy};

pub const HOME_VARIABLE: &str = "CLAUDE_CONFIG_DIR";
pub const DEFAULT_HOME: &str = ".claude";
pub const STORES: &[&str] = &["projects"];
pub const EVENTS: &[(&str, u64)] = &[
    ("SessionStart", 30),
    ("UserPromptSubmit", 30),
    ("Stop", 30),
    ("StopFailure", 30),
    ("PreCompact", 30),
    ("PostCompact", 30),
    ("SessionEnd", 30),
];

pub fn arguments(launch: &Launch) -> Result<Vec<String>> {
    let Some(session) = launch.session else {
        return refuse("Claude Code needs the session Berman assigned");
    };
    let mut arguments: Vec<String> = Vec::new();
    if !launch.attended {
        arguments.extend(["--print", "--output-format", "json"].map(String::from));
    }
    arguments.extend([
        "--settings".into(),
        files::text(launch.settings),
        if launch.resuming { "--resume" } else { "--session-id" }.into(),
        session.into(),
        "--add-dir".into(),
        files::text(launch.return_directory),
    ]);
    Ok(arguments)
}

/// The session, final response and permission denials that a print-mode result reports.
pub fn outcome(output: &[u8]) -> Outcome {
    let mut value: Value = serde_json::from_slice(output).unwrap_or(Value::Null);
    if let Value::Array(items) = &value {
        // A verbose setting turns the result into the last item of a message list.
        value = items.iter().rev().find(|item| text(item, "type") == Some("result")).cloned().unwrap_or(Value::Null);
    }
    if !value.is_object() || text(&value, "type") != Some("result") {
        return Outcome {
            session: None,
            response: None,
            denials: Value::Null,
            limitations: vec!["The native output holds no result.".into()],
        };
    }
    let denials: Vec<Value> = value["permission_denials"]
        .as_array()
        .map(|items| {
            let denied = items.iter().filter(|item| item.is_object());
            denied.map(|item| json!({"tool": item["tool_name"], "input": item["tool_input"]})).collect()
        })
        .unwrap_or_default();
    let result = text(&value, "result");
    let (response, limitations) = match result {
        Some(reply) if text(&value, "subtype") == Some("success") && !truthy(&value["is_error"]) => {
            (Some(reply.to_string()), Vec::new())
        }
        _ => {
            let subtype = Some(&value["subtype"]).filter(|v| v.as_str() != Some("success"));
            let ending = Some(&value["terminal_reason"]).filter(|v| v.as_str() != Some("completed"));
            let mut parts: Vec<String> =
                [subtype, ending].into_iter().flatten().filter(|v| truthy(v)).map(display).collect();
            parts.extend(result.filter(|reply| !reply.is_empty()).map(str::to_string));
            if let Some(errors) = value["errors"].as_array() {
                parts.extend(errors.iter().filter_map(Value::as_str).filter(|e| !e.is_empty()).map(str::to_string));
            }
            let detail = if parts.is_empty() { "no detail".to_string() } else { parts.join("; ") };
            (None, vec![format!("The native result reports an error: {detail}")])
        }
    };
    Outcome {
        session: text(&value, "session_id").map(str::to_string),
        response,
        denials: Value::Array(denials),
        limitations,
    }
}

pub fn candidates(home: &Path, session: &str) -> Vec<PathBuf> {
    let Ok(projects) = std::fs::read_dir(home.join("projects")) else {
        return Vec::new();
    };
    let name = format!("{session}.jsonl");
    projects.flatten().map(|entry| entry.path().join(&name)).filter(|path| files::exists(path)).collect()
}

/// Whether the transcript read belongs to the session: named for it, and naming no other.
pub fn belongs(reading: &Reading, path: &Path, session: &str) -> Result<()> {
    let named = path.file_name().is_some_and(|name| name.to_string_lossy() == format!("{session}.jsonl"));
    if !named || reading.identities != [session] {
        return refuse("Claude transcript identity differs from the assigned parent");
    }
    Ok(())
}

pub const HEADING: [&str; 5] = [
    "CLAUDE EPISODE SESSION: DERIVED READING COPY",
    "",
    "Source: transcript.jsonl. Blocks cite raw JSONL lines and JSON pointers.",
    "Visible messages and tool calls/results. Other content remains in the raw source.",
    "",
];

/// Add one transcript line to the reading copy.
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
    if let Some(identity) = text(&event, "sessionId")
        && !reading.identities.iter().any(|known| known == identity)
    {
        reading.identities.push(identity.to_string());
    }
    let Some(message) = event.get("message").filter(|m| m.is_object()) else {
        return;
    };
    let role = message.get("role").unwrap_or(&event["type"]);
    let whole = message.get("content").and_then(Value::as_str);
    let content = match (whole, message.get("content")) {
        (Some(said), _) => vec![json!({"type": "text", "text": said})],
        (None, Some(Value::Array(items))) => items.clone(),
        (None, None) => Vec::new(),
        _ => return,
    };
    let mut texts = String::new();
    for (i, part) in content.iter().enumerate() {
        let Some(kind) = text(part, "type").filter(|_| part.is_object()) else {
            continue;
        };
        let field = match kind {
            "text" => "text",
            "tool_use" => "input",
            "tool_result" => "content",
            _ => continue,
        };
        let empty = Value::String(String::new());
        let value = part.get(field).unwrap_or(&empty);
        if let (true, Some(said)) = (kind == "text", value.as_str()) {
            texts.push_str(said);
        }
        let pointer =
            if whole.is_some() { "/message/content".to_string() } else { format!("/message/content/{i}/{field}") };
        let time = event.get("timestamp").map(display).unwrap_or_else(|| "not recorded".into());
        let name = part.get("name").map(display).unwrap_or_default();
        let call = part.get("id").or_else(|| part.get("tool_use_id")).map(display).unwrap_or_default();
        let heading = [
            format!("TIME: {time} | ROLE: {} | KIND: {kind}", display(role)),
            format!("NAME: {name} | CALL ID: {call}"),
        ];
        block(&mut reading.text, number, &pointer, heading, value);
    }
    if role.as_str() == Some("assistant") {
        reading.assistant.push(files::digest(texts.as_bytes()));
    }
}
