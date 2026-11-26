//! Attribute callbacks and transcript bytes to the actor in one open episode.
//!
//! Installed callbacks run `berman capture ACTOR`, so that command is part of every reviewed
//! Codex hooks.json.

use std::io::Read;
use std::path::{Path, PathBuf};

use serde_json::{Map, Value, json};

use crate::error::{At, Error, Result, refuse};
use crate::harness::Harness;
use crate::{files, json, snapshots};

const CALLBACK_LIMIT: u64 = 4 * 1024 * 1024;

/// Whether text can name a native session: a letter or digit, then up to 127 letters, digits, `_` or `-`.
pub fn identity(text: &str) -> bool {
    let mut bytes = text.bytes();
    bytes.next().is_some_and(|b| b.is_ascii_alphanumeric())
        && text.len() <= 128
        && bytes.all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
}

/// An opening sitting delivers the opening, followed by the producer's message when there is one.
pub fn first_message(state: &Value, prompt: Option<&str>) -> String {
    let opening = state["opening"].as_str().unwrap_or_default();
    match prompt {
        None => opening.to_string(),
        Some(prompt) => format!("{opening}\nMessage from the producer:\n\n{prompt}"),
    }
}

/// Whether a sitting record says it delivered the opening.
pub fn delivers_opening(sitting: &Value) -> bool {
    sitting.get("opening").and_then(Value::as_bool).unwrap_or(sitting["request"].is_null())
}

pub fn sittings(folder: &Path) -> Result<Vec<Value>> {
    let Ok(entries) = std::fs::read_dir(folder.join("sittings")) else {
        return Ok(Vec::new());
    };
    let mut paths: Vec<PathBuf> = entries.flatten().map(|entry| entry.path()).collect();
    paths.retain(|path| path.extension().is_some_and(|extension| extension == "json"));
    paths.sort();
    paths.iter().map(|path| files::read(path)).collect()
}

/// What the latest sitting delivered first, if it delivered the opening. Sittings run one at a time.
fn opening_message(folder: &Path, state: &Value) -> Result<Option<String>> {
    let recorded = sittings(folder)?;
    Ok(recorded.last().filter(|sitting| delivers_opening(sitting)).map(|s| first_message(state, s["prompt"].as_str())))
}

/// Closeout fallback for absent or relocated callback paths, bounded by native parent identity.
fn find_parent(home: &Path, harness: Harness, session: &str) -> Result<PathBuf> {
    let mut found = None;
    for (count, path) in harness.candidates(home, session).into_iter().enumerate() {
        if count >= 50000 {
            return refuse("Parent source discovery exceeds candidate limit");
        }
        if !harness.owns(&path, session)? {
            continue;
        }
        if found.is_some() {
            return refuse("Multiple native sources claim the bound parent identity");
        }
        found = Some(path);
    }
    found.ok_or_else(|| Error::Refused("No native transcript found for the bound parent".into()))
}

/// Publish a verified transcript generation for the episode's bound parent.
pub fn observe(folder: &Path, state: &Value, session: &str, event: &Value) -> Result<Value> {
    let actor = state["actor"].as_str().unwrap_or_default();
    let harness = Harness::named(actor)?;
    let home = PathBuf::from(state["home"].as_str().unwrap_or_default());
    let previous = snapshots::read(&folder.join("session"))?;
    let old = previous.as_ref().map(|generation| &generation.metadata);
    let named = json::text(event, "transcript_path").filter(|path| !path.is_empty());
    let known = old.and_then(|metadata| json::text(metadata, "source_transcript"));
    let mut path = PathBuf::from(named.or(known).unwrap_or_default());
    let name = json::text(event, "hook_event_name").unwrap_or_default();
    if name == "closeout" && (path.as_os_str().is_empty() || !path.exists()) {
        path = find_parent(&home, harness, session)?;
    }
    let climbs = path.components().any(|part| part == std::path::Component::ParentDir);
    if climbs || !harness.stores().iter().any(|store| path.starts_with(home.join(store))) {
        return refuse("No transcript path in the assigned native session store");
    }
    let data = files::stable_bytes(&path, files::FILE_LIMIT)?;
    let end = data.iter().rposition(|byte| *byte == b'\n').map_or(0, |last| last + 1);
    let (raw, tail) = data.split_at(end);
    if raw.is_empty() {
        return refuse("No complete transcript record");
    }
    let reading = harness.transcript(raw, &path, session)?;
    let expected = match json::text(event, "last_assistant_message").filter(|said| !said.is_empty()) {
        Some(said) => Value::String(files::digest(said.as_bytes())),
        None => old.map(|metadata| metadata["expected_last_assistant_sha256"].clone()).unwrap_or(Value::Null),
    };
    let present = match expected.as_str() {
        Some(digest) => json!(reading.assistant.iter().any(|said| said == digest)),
        None => Value::Null,
    };
    let tail_digest = if tail.is_empty() { Value::Null } else { json!(files::digest(tail)) };
    let tail_file = tail_digest.as_str().map_or(Value::Null, |digest| json!(format!("partial-tail-{digest}.bin")));
    let record = json!({
        "schema": "berman.session-capture/2",
        "harness": actor,
        "format": format!("{actor}-jsonl/1"),
        "episode": state["id"],
        "session_id": session,
        "source_transcript": files::text(&path),
        "captured_at_utc": files::now(),
        "event": name,
        "turn_id": event.get("turn_id").cloned().unwrap_or(Value::Null),
        "source_size_at_read": data.len(),
        "saved_bytes": raw.len(),
        "sha256": files::digest(raw),
        "reader_sha256": files::digest(&reading.text),
        "previous_snapshot_is_prefix": previous.as_ref().map(|generation| raw.starts_with(&generation.raw)),
        "partial_tail_file": tail_file,
        "partial_tail_sha256": tail_digest,
        "omitted_partial_tail_bytes": tail.len(),
        "unparsed_jsonl_lines": reading.malformed,
        "expected_last_assistant_sha256": expected,
        "last_assistant_message_present": present,
        "source_identity": "Native session identity verified against the bound parent.",
    });
    snapshots::publish(&folder.join("session"), &record, raw, &reading.text, tail, &folder.join("scratch"))?;
    Ok(record)
}

/// Whether a transcript generation was ever published. An unreadable pointer counts as published.
fn captured(session: &Path) -> bool {
    !matches!(snapshots::pointer(session), Ok(None))
}

/// The command a harness runs for each callback. It names no episode.
pub fn hook_command(harness: Harness) -> Result<String> {
    let executable = std::env::current_exe()?.canonicalize()?;
    Ok(files::shell_join(&[files::text(&executable), "capture".into(), harness.name().into()]))
}

pub fn hook_settings(harness: Harness) -> Result<Value> {
    let command = hook_command(harness)?;
    let mut hooks = Map::new();
    for (event, timeout) in harness.events() {
        let handler = json!({"type": "command", "command": command, "timeout": timeout});
        hooks.insert((*event).to_string(), json!([{"hooks": [handler]}]));
    }
    Ok(json!({"hooks": hooks}))
}

/// Only a launched episode can bind its opening. A different or closed session writes nothing.
pub fn record(folder: &Path, harness: Harness, event: &Value) -> Result<()> {
    if !event.is_object() {
        return refuse("Expected a hook event");
    }
    let name = json::text(event, "hook_event_name").unwrap_or_default();
    let Some(session) = json::text(event, "session_id").filter(|session| identity(session)) else {
        return Ok(());
    };
    if !harness.events().iter().any(|(known, _)| *known == name) {
        return Ok(());
    }
    files::safe_directory(folder)?;
    let _lease = files::lock(&folder.join(".capture.lock"), true)?;
    let state = files::read(&folder.join("episode.json"))?;
    let closed = files::exists(&folder.join("ending.json"));
    if state["actor"].as_str() != Some(harness.name()) || closed || !folder.join("sittings").is_dir() {
        return Ok(());
    }
    let bound = folder.join("session-id.json");
    if files::exists(&bound) {
        if files::read(&bound)?.as_str() != Some(session) {
            return Ok(());
        }
    } else if !harness.binds(&state, event, opening_message(folder, &state)?.as_deref()) {
        return Ok(());
    }
    files::keep(&bound, &json::bytes(&json!(session)))?;
    let area = folder.join("telemetry");
    let mut row = json!({"observed_at": files::now(), "event": event});
    let path = area.join("events").join(format!("{}.json", files::uuid_hex()));
    files::write(&path, &row)?;
    match observe(&area, &state, session, event) {
        Ok(result) => row["generation"] = json!(json::digest(&result)),
        // Claude Code calls its first hooks before it writes the transcript. Later absence is a gap.
        Err(Error::Missing(_)) if !captured(&area.join("session")) => {
            row["observation"] = json!("The native transcript did not exist yet.");
        }
        Err(error) => row["limitation"] = json!(error.to_string()),
    }
    files::write(&path, &row)
}

/// The command behind each callback. The ordinary harness path has no episode and returns at once.
pub fn main(name: &str) -> Result<()> {
    let harness = Harness::named(name)?;
    if let Some(folder) = std::env::var_os("BERMAN_EPISODE").filter(|folder| !folder.is_empty()) {
        let mut raw = Vec::new();
        std::io::stdin().take(CALLBACK_LIMIT + 1).read_to_end(&mut raw).at(Path::new("standard input"))?;
        if raw.len() as u64 > CALLBACK_LIMIT {
            return refuse("Callback exceeds 4 MiB");
        }
        record(Path::new(&folder), harness, &serde_json::from_slice(&raw)?)?;
    }
    println!("{{}}");
    Ok(())
}
