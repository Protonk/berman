//! Committed transcript generations for the parent session of one episode, kept as plain files.
//!
//! Only current.json publishes a generation, and every reader and writer holds the episode's
//! capture lock. Superseded prefix bytes remain in the current transcript; non-prefix source
//! revisions and incomplete tails are kept under displaced/ and tails/.

use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};

use serde_json::{Value, json};

use crate::error::{At, Result, refuse};
use crate::{files, json};

const SCHEMA: &str = "berman.capture-current/1";

pub struct Generation {
    pub metadata: Value,
    pub raw: Vec<u8>,
    pub reader: Vec<u8>,
    pub tail: Vec<u8>,
    pub history: Vec<Vec<u8>>,
}

fn identifiers(value: &Value) -> Option<Vec<&str>> {
    value.as_array()?.iter().map(|item| item.as_str().filter(|text| files::is_sha256(text))).collect()
}

pub fn pointer(session: &Path) -> Result<Option<Value>> {
    let path = session.join("current.json");
    if !files::exists(&path) {
        return Ok(None);
    }
    let value = files::read(&path)?;
    let generation = json::text(&value, "generation").filter(|text| files::is_sha256(text));
    let committed = identifiers(&value["committed"]);
    match (generation, committed) {
        (Some(generation), Some(committed))
            if json::text(&value, "schema") == Some(SCHEMA) && committed.contains(&generation) =>
        {
            Ok(Some(value))
        }
        _ => refuse(format!("Invalid capture commit reference: {}", path.display())),
    }
}

type Checked = HashMap<PathBuf, (String, u64)>;

/// Verify a retained byte obligation once per read, including repeated history rows.
fn retained(path: PathBuf, digest: &Value, size: Option<&Value>, checked: &mut Checked) -> Result<()> {
    let Some(digest) = digest.as_str().filter(|text| files::is_sha256(text)) else {
        return refuse("Invalid retained capture digest");
    };
    let size = match size {
        None => None,
        Some(value) => match value.as_u64() {
            Some(size) => Some(size),
            None => return refuse("Invalid retained capture size"),
        },
    };
    if !checked.contains_key(&path) {
        let data = files::read_regular(&path)?;
        checked.insert(path.clone(), (files::digest(&data), data.len() as u64));
    }
    let (observed, length) = &checked[&path];
    if observed != digest || size.is_some_and(|size| size != *length) {
        return refuse(format!("Retained capture bytes differ: {}", path.display()));
    }
    Ok(())
}

fn displaced(session: &Path, row: &Value, checked: &mut Checked) -> Result<()> {
    let name = |key: &str, extension: &str| {
        session.join("displaced").join(format!("{}{extension}", row[key].as_str().unwrap_or_default()))
    };
    retained(name("sha256", ".jsonl"), &row["sha256"], Some(&row["saved_bytes"]), checked)?;
    retained(name("reader_sha256", ".txt"), &row["reader_sha256"], None, checked)
}

/// Check non-prefix predecessors and prior tails without rehashing every growing prefix.
fn obligations(session: &Path, rows: &[Value], tail: &[u8]) -> Result<()> {
    let mut checked = Checked::new();
    for pair in rows.windows(2) {
        match pair[1]["previous_snapshot_is_prefix"].as_bool() {
            None => return refuse("Capture history lacks an explicit prefix relationship"),
            Some(false) => displaced(session, &pair[0], &mut checked)?,
            Some(true) => {}
        }
    }
    let current = (files::digest(tail), tail.len() as u64);
    for row in &rows[..rows.len().saturating_sub(1)] {
        let size = row["omitted_partial_tail_bytes"].as_u64().unwrap_or(0);
        let digest = row["partial_tail_sha256"].as_str().unwrap_or_default();
        if size > 0 && (digest, size) != (current.0.as_str(), current.1) {
            let path = session.join("tails").join(format!("{digest}.bin"));
            retained(path, &row["partial_tail_sha256"], Some(&row["omitted_partial_tail_bytes"]), &mut checked)?;
        }
    }
    Ok(())
}

fn tail_name(metadata: &Value) -> Result<Option<&str>> {
    match &metadata["partial_tail_file"] {
        Value::Null => Ok(None),
        Value::String(name) if !name.is_empty() && !name.contains('/') && name != "." && name != ".." => Ok(Some(name)),
        _ => refuse("Invalid capture tail path"),
    }
}

fn read_generation(folder: &Path) -> Result<Generation> {
    let metadata = files::read(&folder.join("capture.json"))?;
    let raw = files::read_regular(&folder.join("transcript.jsonl"))?;
    let reader = files::read_regular(&folder.join("conversation.txt"))?;
    let listing = files::read_regular(&folder.join("captures.jsonl"))?;
    let history: Vec<Vec<u8>> = crate::harness::lines(&listing).into_iter().map(<[u8]>::to_vec).collect();
    let last: Option<Value> = history.last().and_then(|line| serde_json::from_slice(line).ok());
    if metadata["sha256"].as_str() != Some(files::digest(&raw).as_str())
        || metadata["saved_bytes"].as_u64() != Some(raw.len() as u64)
        || metadata["reader_sha256"].as_str() != Some(files::digest(&reader).as_str())
        || last.as_ref() != Some(&metadata)
    {
        return refuse("Capture bytes, reader, metadata or history disagree");
    }
    let mut tail = Vec::new();
    if let Some(name) = tail_name(&metadata)? {
        tail = files::read_regular(&folder.join(name))?;
        if metadata["partial_tail_sha256"].as_str() != Some(files::digest(&tail).as_str())
            || metadata["omitted_partial_tail_bytes"].as_u64() != Some(tail.len() as u64)
        {
            return refuse("Capture partial tail differs");
        }
    }
    Ok(Generation { metadata, raw, reader, tail, history })
}

fn rows(generation: &Generation) -> Result<Vec<Value>> {
    generation.history.iter().map(|line| Ok(serde_json::from_slice(line)?)).collect()
}

/// Read the published generation and check the bytes its history still requires.
pub fn read(session: &Path) -> Result<Option<Generation>> {
    let Some(reference) = pointer(session)? else {
        return Ok(None);
    };
    let name = reference["generation"].as_str().unwrap_or_default();
    let current = read_generation(&session.join("snapshots").join(name))?;
    let rows = rows(&current)?;
    let committed: Vec<String> = rows.iter().map(json::digest).collect();
    if json::digest(&current.metadata) != name || json!(committed) != reference["committed"] {
        return refuse("Capture commit reference and retained history disagree");
    }
    obligations(session, &rows, &current.tail)?;
    Ok(Some(current))
}

/// Keep bytes under a name derived from their digest, or confirm an earlier copy.
fn immutable(path: &Path, data: &[u8]) -> Result<()> {
    if files::exists(path) {
        if files::read_regular(path)? != data {
            return refuse(format!("Existing immutable capture member differs: {}", path.display()));
        }
        return Ok(());
    }
    files::keep(path, data)
}

/// Remove every generation directory except the current one.
///
/// A superseded generation goes only after its non-prefix bytes are confirmed under displaced/.
/// A generation without a commit was never published; the next capture reads the source again.
fn recover(session: &Path) -> Result<()> {
    let current = pointer(session)?;
    let captured = read(session)?;
    let known: HashMap<String, Value> = match &captured {
        Some(generation) => rows(generation)?.into_iter().map(|row| (json::digest(&row), row)).collect(),
        None => HashMap::new(),
    };
    let name = current.as_ref().and_then(|value| value["generation"].as_str()).unwrap_or_default();
    let folders: Vec<PathBuf> = match fs::read_dir(session.join("snapshots")) {
        Ok(entries) => entries.flatten().map(|entry| entry.path()).collect(),
        Err(_) => Vec::new(),
    };
    let mut checked = Checked::new();
    // Check every retirement before changing any generation.
    for folder in &folders {
        let label = folder.file_name().unwrap_or_default().to_string_lossy().into_owned();
        if !files::is_sha256(&label) {
            return refuse(format!("Unknown capture generation member: {}", folder.display()));
        }
        files::safe_directory(folder)?;
        if let (Some(previous), Some(now), true) = (known.get(&label), &captured, label != name) {
            let size = previous["saved_bytes"].as_u64().unwrap_or(u64::MAX) as usize;
            let prefix = now.raw.get(..size).map(files::digest);
            if prefix.as_deref() != previous["sha256"].as_str() {
                let path =
                    session.join("displaced").join(format!("{}.jsonl", previous["sha256"].as_str().unwrap_or("")));
                retained(path, &previous["sha256"], Some(&previous["saved_bytes"]), &mut checked)?;
            }
        }
    }
    for folder in folders {
        if folder.file_name().is_none_or(|label| label.to_string_lossy() != name) {
            fs::remove_dir_all(&folder).at(&folder)?;
        }
    }
    Ok(())
}

pub fn publish(session: &Path, metadata: &Value, raw: &[u8], reader: &[u8], tail: &[u8], staging: &Path) -> Result<()> {
    for folder in [session, staging] {
        fs::create_dir_all(folder).at(folder)?;
        files::safe_directory(folder)?;
    }
    recover(session)?;
    let previous = read(session)?;
    let current = pointer(session)?;
    let expected = previous.as_ref().map(|generation| raw.starts_with(&generation.raw));
    if metadata["previous_snapshot_is_prefix"].as_bool() != expected {
        return refuse("Capture prefix relationship differs from the observed previous bytes");
    }
    if let Some(generation) = &previous {
        if expected == Some(false) {
            let kept = session.join("displaced");
            immutable(&kept.join(format!("{}.jsonl", files::digest(&generation.raw))), &generation.raw)?;
            immutable(&kept.join(format!("{}.txt", files::digest(&generation.reader))), &generation.reader)?;
        }
        if !generation.tail.is_empty() {
            let name = format!("{}.bin", files::digest(&generation.tail));
            immutable(&session.join("tails").join(name), &generation.tail)?;
        }
    }
    let identity = json::digest(metadata);
    if current.as_ref().is_some_and(|value| value["generation"].as_str() == Some(&identity)) {
        return Ok(());
    }
    let mut history = previous.map(|generation| generation.history).unwrap_or_default();
    history.push(json::line(metadata).into_bytes());
    let mut listing = history.join(&b'\n');
    listing.push(b'\n');
    let stage = staging.join(format!("generation-{}", files::uuid_hex()));
    files::create_private_directory(&stage)?;
    files::atomic(&stage.join("capture.json"), &json::bytes(metadata))?;
    files::atomic(&stage.join("transcript.jsonl"), raw)?;
    files::atomic(&stage.join("conversation.txt"), reader)?;
    files::atomic(&stage.join("captures.jsonl"), &listing)?;
    match tail_name(metadata)? {
        Some(name) if !tail.is_empty() => files::atomic(&stage.join(name), tail)?,
        None if !tail.is_empty() => return refuse("Invalid capture tail path"),
        _ => {}
    }
    read_generation(&stage)?; // Check staged bytes before publishing their pointer.
    let target = session.join("snapshots").join(&identity);
    fs::create_dir_all(session.join("snapshots")).at(session)?;
    if files::exists(&target) {
        return refuse("Capture generation identity already exists without publication");
    }
    fs::rename(&stage, &target).at(&target)?;
    let mut committed: Vec<Value> =
        current.and_then(|value| value["committed"].as_array().cloned()).unwrap_or_default();
    committed.push(json!(identity));
    // A single replace changes visibility of raw bytes, reader, metadata and history together.
    files::write(
        &session.join("current.json"),
        &json!({
            "schema": SCHEMA,
            "generation": identity,
            "committed": committed,
            "retention": "Superseded prefix bytes remain in the current transcript; non-prefix revisions and tails are retained.",
        }),
    )?;
    recover(session)
}
