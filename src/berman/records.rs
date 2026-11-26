//! Publish a portable episode record and prepare a reading folder from selected records.

use std::fs;
use std::path::{Path, PathBuf};

use serde_json::{Map, Value, json};

use crate::error::{At, Result, refuse};
use crate::{files, json, project, snapshots};

const MANIFEST: &str = "manifest.json";
const SCHEMA: &str = "berman.episode-record/1";
const RECORD_FILES: usize = 4096;
const RECORD_BYTES: u64 = 1024 * 1024 * 1024;

/// Every entry below a directory that is not itself a directory, with the reason it cannot be read as a file.
fn entries(root: &Path) -> Result<Vec<(PathBuf, Option<String>)>> {
    files::safe_directory(root)?;
    let mut found = Vec::new();
    let mut unreadable = Vec::new();
    let mut pending = vec![root.to_path_buf()];
    while let Some(directory) = pending.pop() {
        let listing = match fs::read_dir(&directory) {
            Ok(listing) => listing,
            Err(error) => {
                unreadable.push((directory, Some(error.to_string())));
                continue;
            }
        };
        let mut paths: Vec<PathBuf> = listing.flatten().map(|entry| entry.path()).collect();
        paths.sort();
        let mut below = Vec::new();
        for path in paths {
            match path.symlink_metadata() {
                Ok(metadata) if metadata.is_dir() => below.push(path),
                Ok(metadata) if metadata.is_file() => found.push((path, None)),
                Ok(_) => found.push((path, Some("symbolic link or special file".to_string()))),
                Err(error) => found.push((path, Some(error.to_string()))),
            }
        }
        pending.extend(below.into_iter().rev());
    }
    found.extend(unreadable);
    Ok(found)
}

fn relative(path: &Path, root: &Path) -> String {
    files::text(path.strip_prefix(root).unwrap_or(path))
}

fn hashes(folder: &Path) -> Result<Map<String, Value>> {
    let mut members = Map::new();
    for (path, problem) in entries(folder)? {
        let name = relative(&path, folder);
        if let Some(problem) = problem {
            return refuse(format!("Cannot verify {name}: {problem}"));
        }
        if name != MANIFEST {
            files::regular(&path)?;
            let digest = files::digest_reader(fs::File::open(&path).at(&path)?).at(&path)?;
            members.insert(name, json!(digest));
        }
    }
    Ok(members)
}

pub fn verify(record: &Path, expected: Option<&str>) -> Result<Value> {
    let raw = files::read_regular(&record.join(MANIFEST))?;
    let fingerprint = files::digest(&raw);
    if expected.is_some_and(|expected| expected != fingerprint) {
        return refuse("The supplied record digest differs");
    }
    let manifest: Value = serde_json::from_slice(&raw)?;
    let episode = json::text(&manifest, "episode").filter(|identity| files::is_uuid(identity));
    let Some(episode) = episode.filter(|_| json::text(&manifest, "schema") == Some(SCHEMA)) else {
        return refuse("Expected an episode record");
    };
    let actual = hashes(record)?;
    let required = ["episode.json", "opening.md", "ending.json", "coverage.json"];
    if manifest["members"].as_object() != Some(&actual) || !required.iter().all(|name| actual.contains_key(*name)) {
        return refuse("Record members differ from the manifest");
    }
    let state = files::read(&record.join("episode.json"))?;
    let opening = fs::read(record.join("opening.md")).at(record)?;
    if state["id"].as_str() != Some(episode)
        || json::text(&state, "schema") != Some("berman.episode/1")
        || state["opening"].as_str().map(str::as_bytes) != Some(&opening)
    {
        return refuse("The recorded episode differs from its opening or manifest");
    }
    Ok(json!({
        "episode": episode,
        "record": files::text(record),
        "sha256": fingerprint,
        "coverage": files::read(&record.join("coverage.json"))?,
    }))
}

pub fn seal(folder: &Path, state: &Value, mut limitations: Vec<String>) -> Result<Value> {
    let record = folder.join("record");
    files::new_directory(&record, |stage| {
        for name in ["episode.json", "ending.json"] {
            files::write(&stage.join(name), &files::read(&folder.join(name))?)?;
        }
        files::atomic(&stage.join("opening.md"), state["opening"].as_str().unwrap_or_default().as_bytes())?;
        match project::git_state(Path::new(state["project"].as_str().unwrap_or_default())) {
            Ok(observed) => files::write(&stage.join("final-project.json"), &observed)?,
            Err(error) => limitations.push(format!("Final checkout observation unavailable: {error}")),
        }
        let mut roots = vec![
            ("return".to_string(), folder.join("return")),
            ("sittings".to_string(), folder.join("sittings")),
            ("telemetry/events".to_string(), folder.join("telemetry/events")),
        ];
        let session = folder.join("telemetry/session");
        let captured = snapshots::read(&session).unwrap_or_else(|error| {
            limitations.push(format!("Could not verify the staged parent transcript: {error}"));
            None
        });
        if let Some(generation) = captured {
            let metadata = &generation.metadata;
            if json::truthy(&metadata["unparsed_jsonl_lines"]) {
                limitations
                    .push(format!("Unparsed parent JSONL lines: {}", json::line(&metadata["unparsed_jsonl_lines"])));
            }
            if json::truthy(&metadata["omitted_partial_tail_bytes"]) {
                limitations.push("An incomplete final JSONL line was retained separately.".into());
            }
            if metadata["last_assistant_message_present"] == json!(false) {
                limitations
                    .push("The expected final assistant message was absent from the captured transcript.".into());
            }
            let current = json::digest(metadata);
            roots.push(("telemetry/parent".into(), session.join("snapshots").join(current)));
            for name in ["displaced", "tails"] {
                roots.push((format!("telemetry/{name}"), session.join(name)));
            }
        } else {
            limitations.push("No verified parent transcript was collected.".into());
        }
        if !files::exists(&folder.join("telemetry/events")) {
            limitations.push("No session callbacks were observed.".into());
        }
        if !files::exists(&folder.join("sittings")) {
            limitations.push("No sitting was launched.".into());
        }
        let (mut retained, mut count) = (0u64, 0usize);
        for (prefix, root) in roots {
            if !files::exists(&root) {
                if prefix == "return" {
                    limitations.push("The actor return directory was missing.".into());
                }
                continue;
            }
            if files::safe_directory(&root).is_err() {
                limitations.push(format!("{prefix} was unavailable as a directory."));
                continue;
            }
            for (path, problem) in entries(&root)? {
                let label = format!("{prefix}/{}", relative(&path, &root));
                let read = match problem {
                    Some(problem) => Err(problem),
                    None => collect(&path, count, retained).map_err(|error| error.to_string()),
                };
                let raw = match read {
                    Ok(raw) => raw,
                    Err(problem) => {
                        limitations.push(format!("Omitted {label}: {problem}"));
                        continue;
                    }
                };
                files::keep(&stage.join(&label), &raw)?;
                retained += raw.len() as u64;
                count += 1;
                if prefix == "sittings" && label.ends_with(".json") {
                    let sitting: Value = serde_json::from_slice(&raw)?;
                    if sitting["exit"].is_null() {
                        limitations.push(format!("{label}: process exit unobserved."));
                    }
                    for item in sitting["limitations"].as_array().into_iter().flatten() {
                        limitations.push(format!("{label}: {}", json::display(item)));
                    }
                }
                if prefix == "telemetry/events" {
                    let row: Value = serde_json::from_slice(&raw)?;
                    if json::truthy(&row["limitation"]) {
                        limitations.push(format!("{label}: {}", json::display(&row["limitation"])));
                    }
                }
            }
        }
        let coverage = json!({
            "limitations": limitations,
            "scope": "The actor return, the sittings Berman launched with any native output they left, \
                      the bound parent session, and the checkout as observed at the opening and at closing. \
                      Those observations show the checkout at two times, not who changed it. \
                      Other sessions and activity outside the bound session are outside capture.",
        });
        files::write(&stage.join("coverage.json"), &coverage)?;
        let manifest = json!({"schema": SCHEMA, "episode": state["id"], "members": hashes(stage)?});
        files::write(&stage.join(MANIFEST), &manifest)?;
        verify(stage, None)
    })?;
    verify(&record, None)
}

/// Read one member for the record, within the limits shared by everything the record copies.
fn collect(path: &Path, count: usize, retained: u64) -> Result<Vec<u8>> {
    let size = fs::metadata(path).at(path)?.len();
    if count >= RECORD_FILES || retained + size > RECORD_BYTES {
        return refuse("episode record size or file limit reached");
    }
    files::stable_bytes(path, files::FILE_LIMIT)
}

fn copy_tree(source: &Path, destination: &Path) -> Result<()> {
    fs::create_dir_all(destination).at(destination)?;
    for (path, problem) in entries(source)? {
        let name = relative(&path, source);
        if let Some(problem) = problem {
            return refuse(format!("Cannot copy {name}: {problem}"));
        }
        let target = destination.join(&name);
        if let Some(parent) = target.parent() {
            fs::create_dir_all(parent).at(parent)?;
        }
        fs::copy(&path, &target).at(&target)?;
    }
    Ok(())
}

pub fn review(records: &[PathBuf], question: &str, output: &Path) -> Result<Value> {
    if question.trim().is_empty() {
        return refuse("Supply the question for the selected records");
    }
    for record in records {
        if output.starts_with(record) || record.starts_with(output) {
            return refuse("Use a review destination outside each source record");
        }
    }
    let sources = files::new_directory(output, |stage| {
        let mut sources: Vec<Value> = Vec::new();
        for record in records {
            let source = verify(record, None)?;
            let identity = source["episode"].as_str().unwrap_or_default();
            if sources.iter().any(|known| known["episode"].as_str() == Some(identity)) {
                return refuse(format!("Episode selected twice: {identity}"));
            }
            let relative = format!("sources/{identity}");
            let destination = stage.join(&relative);
            copy_tree(record, &destination)?;
            verify(&destination, source["sha256"].as_str())?;
            sources.push(json!({"episode": identity, "path": relative, "sha256": source["sha256"]}));
        }
        files::write(&stage.join("sources.json"), &json!(sources))?;
        files::atomic(&stage.join("question.md"), format!("{}\n", question.trim_end()).as_bytes())?;
        files::atomic(
            &stage.join("answer.md"),
            b"<!-- Write your answer here. Cite the relative source path and passage \
              for each conclusion, and identify gaps that affect your answer. -->\n",
        )?;
        Ok(sources)
    })?;
    Ok(json!({
        "output": files::text(output),
        "question": files::text(&output.join("question.md")),
        "answer": files::text(&output.join("answer.md")),
        "sources": sources,
    }))
}
