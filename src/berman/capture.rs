//! Attribute callbacks and transcript bytes to the actor in one open episode.
//!
//! Installed callbacks run `berman capture ACTOR`, so that command is part of every reviewed
//! Codex hooks.json. It names no episode: only a sitting Berman launched carries one.
//!
//! Each attributed callback publishes a generation of the parent transcript under
//! `telemetry/parent/` in one transaction. Bytes a later generation extends are shared with it.
//! A generation the next one does not extend is kept under `telemetry/displaced/`, and an
//! incomplete final line under `telemetry/tails/`.

use std::collections::HashMap;
use std::io::{Read, Write};
use std::path::{Path, PathBuf};

use serde_json::{Map, Value, json};

use crate::error::{At, Error, Result, refuse};
use crate::harness::{self, Harness, Reading};
use crate::library::{Episode, Library, Stored};
use crate::{files, json};

const CALLBACK_LIMIT: u64 = 4 * 1024 * 1024;
const PARENT: &str = "telemetry/parent";

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

/// The sittings recorded for an episode, in order.
pub fn sittings(library: &Library, node: i64) -> Result<Vec<Value>> {
    let Some(directory) = library.find(node, "sittings")? else {
        return Ok(Vec::new());
    };
    let records = library.children(directory.id)?.into_iter().filter(|entry| entry.name.ends_with(".json"));
    records.map(|entry| library.read_json(entry.id, "")).collect()
}

/// What the latest sitting delivered first, if it delivered the opening. Sittings run one at a time.
fn opening_message(library: &Library, node: i64, state: &Value) -> Result<Option<String>> {
    let recorded = sittings(library, node)?;
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

/// The complete lines of a transcript and its reading copy, stored as they were read, and any incomplete last line.
struct Observed {
    raw: Stored,
    reader: Stored,
    tail: Vec<u8>,
    reading: Reading,
    source_size: u64,
    /// The digest of the first bytes, as many as the previous generation kept.
    prefix: Option<String>,
}

/// Read a transcript once, in bounded pieces: store its complete lines, derive the reading copy, and note
/// the digest at the length the previous generation had.
fn read_source(
    library: &Library,
    harness: Harness,
    path: &Path,
    session: &str,
    previous: Option<u64>,
) -> Result<Observed> {
    files::regular(path)?;
    let before = std::fs::metadata(path).at(path)?;
    if before.len() > files::FILE_LIMIT {
        return refuse(format!("File exceeds {} bytes: {}", files::FILE_LIMIT, path.display()));
    }
    let mut source = std::fs::File::open(path).at(path)?.take(before.len() + 1);
    let mut raw = library.writer()?;
    let mut reader = library.writer()?;
    let mut reading = Reading { text: harness.heading(), ..Reading::default() };
    let (mut pending, mut buffer) = (Vec::new(), vec![0u8; 1 << 16]);
    let (mut total, mut number, mut prefix) = (0u64, 0usize, None);
    loop {
        let count = source.read(&mut buffer).at(path)?;
        if count == 0 {
            break;
        }
        total += count as u64;
        pending.extend_from_slice(&buffer[..count]);
        let Some(last) = pending.iter().rposition(|byte| *byte == b'\n') else {
            continue;
        };
        let complete: Vec<u8> = pending.drain(..=last).collect();
        // Take the digest exactly where the previous generation ended, if these bytes reach that far.
        let split = previous.filter(|size| (raw.size()..raw.size() + complete.len() as u64).contains(size));
        let cut = split.map_or(0, |size| (size - raw.size()) as usize);
        raw.write_all(&complete[..cut])?;
        if split.is_some() {
            prefix = Some(raw.digest_so_far());
        }
        raw.write_all(&complete[cut..])?;
        for line in harness::lines(&complete) {
            number += 1;
            if number == 1 {
                harness.first_line(line, session)?;
            }
            harness.line(number, line, &mut reading);
            reader.write_all(&reading.text)?;
            reading.text.clear();
        }
    }
    if previous == Some(raw.size()) {
        prefix = Some(raw.digest_so_far());
    }
    let after = std::fs::metadata(path).at(path)?;
    if files::identity(&before) != files::identity(&after) || total != after.len() {
        return refuse(format!("Source changed during observation: {}", path.display()));
    }
    if raw.size() == 0 {
        return refuse("No complete transcript record");
    }
    harness.whole(&reading, path, session)?;
    Ok(Observed { raw: raw.finish()?, reader: reader.finish()?, tail: pending, reading, source_size: total, prefix })
}

/// Keep bytes under a name derived from their digest, or confirm an earlier copy.
fn retain(library: &Library, node: i64, path: &str, stored: &Stored) -> Result<()> {
    match library.find(node, path)? {
        None => library.place(node, path, stored, false).map(drop),
        Some(existing) if existing.sha256.as_deref() == Some(&stored.sha256) => Ok(()),
        Some(_) => refuse(format!("Existing immutable capture member differs: {path}")),
    }
}

fn stored(library: &Library, node: i64, path: &str) -> Result<Stored> {
    let found = library.expect(node, path)?;
    match (found.content, found.sha256) {
        (Some(content), Some(sha256)) => Ok(Stored { content, size: found.size, sha256 }),
        _ => refuse(format!("Expected a file in the library: {path}")),
    }
}

fn tail_name(metadata: &Value) -> Result<Option<&str>> {
    match &metadata["partial_tail_file"] {
        Value::Null => Ok(None),
        Value::String(name) if !name.is_empty() && !name.contains('/') && name != "." && name != ".." => Ok(Some(name)),
        _ => refuse("Invalid capture tail path"),
    }
}

/// Publish a verified transcript generation for the episode's bound parent.
pub fn observe(library: &Library, node: i64, state: &Value, session: &str, event: &Value) -> Result<Value> {
    let actor = state["actor"].as_str().unwrap_or_default();
    let harness = Harness::named(actor)?;
    let home = PathBuf::from(state["home"].as_str().unwrap_or_default());
    library.transaction(|| {
        let capture = format!("{PARENT}/capture.json");
        let old = if library.exists(node, &capture)? { Some(library.read_json(node, &capture)?) } else { None };
        let named = json::text(event, "transcript_path").filter(|path| !path.is_empty());
        let known = old.as_ref().and_then(|metadata| json::text(metadata, "source_transcript"));
        let mut path = PathBuf::from(named.or(known).unwrap_or_default());
        let name = json::text(event, "hook_event_name").unwrap_or_default();
        if name == "closeout" && (path.as_os_str().is_empty() || !path.exists()) {
            path = find_parent(&home, harness, session)?;
        }
        let climbs = path.components().any(|part| part == std::path::Component::ParentDir);
        if climbs || !harness.stores().iter().any(|store| path.starts_with(home.join(store))) {
            return refuse("No transcript path in the assigned native session store");
        }
        if let Some(metadata) = &old {
            // The stored generation must still be the one its description names before anything is built on it.
            let agrees = |name: &str, key: &str| -> Result<bool> {
                Ok(metadata[key].as_str() == Some(&stored(library, node, &format!("{PARENT}/{name}"))?.sha256))
            };
            let tail = match tail_name(metadata)? {
                Some(name) => agrees(name, "partial_tail_sha256")?,
                None => true,
            };
            if !agrees("transcript.jsonl", "sha256")? || !agrees("conversation.txt", "reader_sha256")? || !tail {
                return refuse("Capture bytes, reader, metadata or history disagree");
            }
        }
        let previous = old.as_ref().and_then(|metadata| metadata["saved_bytes"].as_u64());
        let seen = read_source(library, harness, &path, session, previous)?;
        let extends = old.as_ref().map(|metadata| seen.prefix.as_deref() == metadata["sha256"].as_str());
        if let Some(metadata) = &old {
            if extends == Some(false) {
                let kept = |key: &str, extension: &str| {
                    format!("telemetry/displaced/{}{extension}", metadata[key].as_str().unwrap_or_default())
                };
                retain(
                    library,
                    node,
                    &kept("sha256", ".jsonl"),
                    &stored(library, node, &format!("{PARENT}/transcript.jsonl"))?,
                )?;
                retain(
                    library,
                    node,
                    &kept("reader_sha256", ".txt"),
                    &stored(library, node, &format!("{PARENT}/conversation.txt"))?,
                )?;
            }
            if let Some(tail) = tail_name(metadata)? {
                let from = format!("{PARENT}/{tail}");
                let digest = metadata["partial_tail_sha256"].as_str().unwrap_or_default();
                retain(library, node, &format!("telemetry/tails/{digest}.bin"), &stored(library, node, &from)?)?;
                library.remove(&library.expect(node, &from)?)?;
            }
        }
        let expected = match json::text(event, "last_assistant_message").filter(|said| !said.is_empty()) {
            Some(said) => Value::String(files::digest(said.as_bytes())),
            None => old.as_ref().map_or(Value::Null, |metadata| metadata["expected_last_assistant_sha256"].clone()),
        };
        let present = match expected.as_str() {
            Some(digest) => json!(seen.reading.assistant.iter().any(|said| said == digest)),
            None => Value::Null,
        };
        let tail_digest = if seen.tail.is_empty() { Value::Null } else { json!(files::digest(&seen.tail)) };
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
            "source_size_at_read": seen.source_size,
            "saved_bytes": seen.raw.size,
            "sha256": seen.raw.sha256,
            "reader_sha256": seen.reader.sha256,
            "previous_snapshot_is_prefix": extends,
            "partial_tail_file": tail_file,
            "partial_tail_sha256": tail_digest,
            "omitted_partial_tail_bytes": seen.tail.len(),
            "unparsed_jsonl_lines": seen.reading.malformed,
            "expected_last_assistant_sha256": expected,
            "last_assistant_message_present": present,
            "source_identity": "Native session identity verified against the bound parent.",
        });
        let listing = format!("{PARENT}/captures.jsonl");
        let mut history = if old.is_some() { library.read(node, &listing)? } else { Vec::new() };
        history.extend_from_slice(json::line(&record).as_bytes());
        history.push(b'\n');
        library.place(node, &format!("{PARENT}/transcript.jsonl"), &seen.raw, true)?;
        library.place(node, &format!("{PARENT}/conversation.txt"), &seen.reader, true)?;
        library.write(node, &listing, &history)?;
        library.write_json(node, &capture, &record)?;
        if let Some(name) = tail_file.as_str() {
            library.write(node, &format!("{PARENT}/{name}"), &seen.tail)?;
        }
        Ok(record)
    })
}

/// Check the published generation against the bytes stored for it and the bytes its history still requires.
///
/// Returns the generation's description, or None when no transcript was ever captured.
pub fn check(library: &Library, node: i64) -> Result<Option<Value>> {
    let capture = format!("{PARENT}/capture.json");
    if !library.exists(node, &capture)? {
        if library.exists(node, PARENT)? {
            return refuse("The captured transcript has lost its description, capture.json");
        }
        return Ok(None);
    }
    let metadata = library.read_json(node, &capture)?;
    let mut measured: HashMap<String, (String, u64)> = HashMap::new();
    let mut measure = |path: String| -> Result<(String, u64)> {
        if !measured.contains_key(&path) {
            let content = stored(library, node, &path)?.content;
            measured.insert(path.clone(), library.measure(content)?);
        }
        Ok(measured[&path].clone())
    };
    let agrees = |found: &(String, u64), digest: &Value, size: Option<&Value>| {
        digest.as_str() == Some(&found.0) && size.is_none_or(|size| size.as_u64() == Some(found.1))
    };
    let raw = measure(format!("{PARENT}/transcript.jsonl"))?;
    let reader = measure(format!("{PARENT}/conversation.txt"))?;
    let listing = library.read(node, &format!("{PARENT}/captures.jsonl"))?;
    let rows: Vec<Value> =
        harness::lines(&listing).into_iter().map(|line| Ok(serde_json::from_slice(line)?)).collect::<Result<_>>()?;
    if !agrees(&raw, &metadata["sha256"], Some(&metadata["saved_bytes"]))
        || !agrees(&reader, &metadata["reader_sha256"], None)
        || rows.last() != Some(&metadata)
    {
        return refuse("Capture bytes, reader, metadata or history disagree");
    }
    let mut current_tail = (files::digest(b""), 0u64);
    if let Some(name) = tail_name(&metadata)? {
        current_tail = measure(format!("{PARENT}/{name}"))?;
        if !agrees(&current_tail, &metadata["partial_tail_sha256"], Some(&metadata["omitted_partial_tail_bytes"])) {
            return refuse("Capture partial tail differs");
        }
    }
    let mut retained = |path: String, digest: &Value, size: Option<&Value>| -> Result<()> {
        if !digest.as_str().is_some_and(files::is_sha256) {
            return refuse("Invalid retained capture digest");
        }
        if !agrees(&measure(path.clone())?, digest, size) {
            return refuse(format!("Retained capture bytes differ: {path}"));
        }
        Ok(())
    };
    for pair in rows.windows(2) {
        let name = |key: &str, extension: &str| {
            format!("telemetry/displaced/{}{extension}", pair[0][key].as_str().unwrap_or_default())
        };
        match pair[1]["previous_snapshot_is_prefix"].as_bool() {
            None => return refuse("Capture history lacks an explicit prefix relationship"),
            Some(true) => {}
            Some(false) => {
                retained(name("sha256", ".jsonl"), &pair[0]["sha256"], Some(&pair[0]["saved_bytes"]))?;
                retained(name("reader_sha256", ".txt"), &pair[0]["reader_sha256"], None)?;
            }
        }
    }
    for row in &rows[..rows.len().saturating_sub(1)] {
        let size = row["omitted_partial_tail_bytes"].as_u64().unwrap_or(0);
        let digest = row["partial_tail_sha256"].as_str().unwrap_or_default();
        if size > 0 && (digest, size) != (current_tail.0.as_str(), current_tail.1) {
            let path = format!("telemetry/tails/{digest}.bin");
            retained(path, &row["partial_tail_sha256"], Some(&row["omitted_partial_tail_bytes"]))?;
        }
    }
    Ok(Some(metadata))
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
pub fn record(library: &Library, episode: &str, harness: Harness, event: &Value) -> Result<()> {
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
    let Some(Episode { node: Some(node), .. }) = library.episode(episode)? else {
        return Ok(());
    };
    if library.exists(node, "ending.json")? {
        return Ok(());
    }
    let _lease = files::lock(&library.places.lock(episode, "capture"), true)?;
    let bound = library.episode(episode)?.and_then(|episode| episode.session);
    let state = library.read_json(node, "episode.json")?;
    let closed = library.exists(node, "ending.json")?;
    if state["actor"].as_str() != Some(harness.name()) || closed || !library.exists(node, "sittings")? {
        return Ok(());
    }
    match bound {
        Some(bound) if bound != session => return Ok(()),
        Some(_) => {}
        None if !harness.binds(&state, event, opening_message(library, node, &state)?.as_deref()) => return Ok(()),
        None => library.bind_session(episode, session)?,
    }
    let mut row = json!({"observed_at": files::now(), "event": event});
    let path = format!("telemetry/events/{}.json", files::uuid_hex());
    library.write_json(node, &path, &row)?;
    match observe(library, node, &state, session, event) {
        Ok(result) => row["generation"] = json!(json::digest(&result)),
        // Claude Code calls its first hooks before it writes the transcript. Later absence is a gap.
        Err(Error::Missing(_)) if !library.exists(node, PARENT)? => {
            row["observation"] = json!("The native transcript did not exist yet.");
        }
        Err(error) => row["limitation"] = json!(error.to_string()),
    }
    library.write_json(node, &path, &row).map(drop)
}

/// The command behind each callback. The ordinary harness path has no episode and returns at once.
pub fn main(name: &str) -> Result<()> {
    let harness = Harness::named(name)?;
    if let Some(episode) = std::env::var("BERMAN_EPISODE").ok().filter(|episode| !episode.is_empty()) {
        let mut raw = Vec::new();
        std::io::stdin().take(CALLBACK_LIMIT + 1).read_to_end(&mut raw).at(Path::new("standard input"))?;
        if raw.len() as u64 > CALLBACK_LIMIT {
            return refuse("Callback exceeds 4 MiB");
        }
        let event = serde_json::from_slice(&raw)?;
        if !files::is_uuid(&episode) {
            return refuse("BERMAN_EPISODE does not name an episode");
        }
        record(&Library::open()?, &episode, harness, &event)?;
    }
    println!("{{}}");
    Ok(())
}
