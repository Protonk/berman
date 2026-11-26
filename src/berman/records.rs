//! Publish an episode's record, verify records wherever they are kept, and prepare reviews from selected records.
//!
//! A record has the same members and manifest in the library and in a directory on disk, so a
//! copy made on one system verifies on another.

use std::collections::BTreeSet;
use std::fs;
use std::path::{Path, PathBuf};

use serde_json::{Map, Value, json};

use crate::error::{At, Result, refuse};
use crate::library::{Library, ROOT, parts};
use crate::tree::{Directory, Shelf, Tree, entries, relative};
use crate::{capture, episodes, files, json, project};

pub const MANIFEST: &str = "manifest.json";
const SCHEMA: &str = "berman.episode-record/1";
const RECORD_FILES: usize = 4096;
const RECORD_BYTES: u64 = 1024 * 1024 * 1024;
const REQUIRED: [&str; 4] = ["episode.json", "opening.md", "ending.json", "coverage.json"];

fn hashes(tree: &dyn Tree) -> Result<Map<String, Value>> {
    let mut members = Map::new();
    for member in tree.members()? {
        if let Some(problem) = member.problem {
            return refuse(format!("Cannot verify {}: {problem}", member.name));
        }
        if member.name != MANIFEST {
            let digest = tree.measure(&member.name)?.0;
            members.insert(member.name, json!(digest));
        }
    }
    Ok(members)
}

/// Name the members that differ, so a producer can see what was removed, added or changed.
fn differences(expected: Option<&Map<String, Value>>, actual: &Map<String, Value>) -> String {
    let Some(expected) = expected else {
        return "the manifest lists no members".into();
    };
    let names: BTreeSet<&String> = expected.keys().chain(actual.keys()).collect();
    let mut found = Vec::new();
    for name in names {
        match (expected.get(name), actual.get(name)) {
            (Some(_), None) => found.push(format!("missing {name}")),
            (None, Some(_)) => found.push(format!("unlisted {name}")),
            (Some(listed), Some(present)) if listed != present => found.push(format!("changed {name}")),
            _ => {}
        }
    }
    let shown = found.len().min(12);
    let more = if found.len() > shown { format!(" and {} more", found.len() - shown) } else { String::new() };
    format!("{}{more}", found[..shown].join(", "))
}

/// Check a record against its manifest, and against a separately held manifest digest when one is given.
pub fn verify(tree: &dyn Tree, expected: Option<&str>) -> Result<Value> {
    let raw = tree.read(MANIFEST)?;
    let fingerprint = files::digest(&raw);
    if expected.is_some_and(|expected| expected != fingerprint) {
        return refuse("The supplied record digest differs");
    }
    let manifest: Value = serde_json::from_slice(&raw)?;
    let episode = json::text(&manifest, "episode").filter(|identity| files::is_uuid(identity));
    let Some(episode) = episode.filter(|_| json::text(&manifest, "schema") == Some(SCHEMA)) else {
        return refuse("Expected an episode record");
    };
    let actual = hashes(tree)?;
    let listed = manifest["members"].as_object();
    if listed != Some(&actual) || !REQUIRED.iter().all(|name| actual.contains_key(*name)) {
        return refuse(format!("Record members differ from the manifest: {}", differences(listed, &actual)));
    }
    let state: Value = serde_json::from_slice(&tree.read("episode.json")?)?;
    if state["id"].as_str() != Some(episode)
        || !episodes::SCHEMAS.iter().any(|schema| json::text(&state, "schema") == Some(schema))
        || state["opening"].as_str().map(str::as_bytes) != Some(&tree.read("opening.md")?)
    {
        return refuse("The recorded episode differs from its opening or manifest");
    }
    Ok(json!({
        "episode": episode,
        "record": tree.label(),
        "sha256": fingerprint,
        "coverage": serde_json::from_slice::<Value>(&tree.read("coverage.json")?)?,
    }))
}

/// Commit the correspondence, then publish the record with the inventory cleanup works from in one transaction.
///
/// Return files are read as stable regular files without following links. A file that changes
/// while it is read, cannot be read, or would pass a limit stays where it is and is named in
/// the coverage.
pub fn publish(
    library: &Library,
    id: &str,
    node: i64,
    state: &Value,
    mut limitations: Vec<String>,
    returns: Option<&Path>,
) -> Result<()> {
    // An interrupted close may have committed part of the return. Collection starts again from the directory.
    if let Some(partial) = library.find(node, "return")? {
        library.remove(&partial)?;
    }
    let (mut retained, mut count) = (0u64, 0usize);
    let mut inventory = Vec::new();
    {
        match returns.filter(|directory| files::exists(directory)) {
            None => limitations.push("The actor return directory was missing.".into()),
            Some(directory) if files::safe_directory(directory).is_err() => {
                limitations.push("return was unavailable as a directory.".into());
            }
            Some(directory) => {
                for (path, problem) in entries(directory)? {
                    let label = format!("return/{}", relative(&path, directory));
                    let collected = match (problem, path.to_str()) {
                        (Some(problem), _) => Err(problem),
                        (None, None) => Err("its name is not UTF-8 text".to_string()),
                        (None, Some(_)) => (|| {
                            let size = fs::metadata(&path).at(&path)?.len();
                            if count >= RECORD_FILES || retained + size > RECORD_BYTES {
                                return refuse("the return's size or file limit was reached");
                            }
                            // Each file is its own transaction, so the journal never holds more than one file.
                            library.transaction(|| {
                                let stored = library.store_file(&path, Some(files::FILE_LIMIT))?;
                                library.place(node, &label, &stored, false)?;
                                Ok(stored)
                            })
                        })()
                        .map_err(|error| error.to_string()),
                    };
                    match collected {
                        Ok(stored) => {
                            retained += stored.size;
                            count += 1;
                            let name = relative(&path, directory);
                            inventory.push(json!({"path": name, "size": stored.size, "sha256": stored.sha256}));
                        }
                        Err(problem) => limitations.push(format!("Omitted {label}: {problem}")),
                    }
                }
            }
        }
    }
    library.transaction(|| {
        match project::git_state(Path::new(state["project"].as_str().unwrap_or_default())) {
            Ok(observed) => drop(library.write_json(node, "final-project.json", &observed)?),
            Err(error) => limitations.push(format!("Final checkout observation unavailable: {error}")),
        }
        // Every stored byte is read and digested once here: for the capture's own checks and for the manifest.
        let shelf = Shelf::new(library, node);
        match capture::check_with(library, node, &mut |kept| shelf.measure_content(kept.content)) {
            Ok(Some(metadata)) => {
                if json::truthy(&metadata["unparsed_jsonl_lines"]) {
                    let lines = json::line(&metadata["unparsed_jsonl_lines"]);
                    limitations.push(format!("Unparsed parent JSONL lines: {lines}"));
                }
                if json::truthy(&metadata["omitted_partial_tail_bytes"]) {
                    limitations.push("An incomplete final JSONL line was retained separately.".into());
                }
                if metadata["last_assistant_message_present"] == json!(false) {
                    limitations
                        .push("The expected final assistant message was absent from the captured transcript.".into());
                }
            }
            Ok(None) => limitations.push("No verified parent transcript was collected.".into()),
            Err(error) => {
                limitations.push(format!("Could not verify the staged parent transcript: {error}"));
                limitations.push("No verified parent transcript was collected. Its members are kept as found.".into());
            }
        }
        if !library.exists(node, "telemetry/events")? {
            limitations.push("No session callbacks were observed.".into());
        }
        if !library.exists(node, "sittings")? {
            limitations.push("No sitting was launched.".into());
        }
        // Sittings and telemetry are already in the episode and stay whole. The limits bound what closing brings in.
        for prefix in ["sittings", "telemetry/events"] {
            let Some(directory) = library.find(node, prefix)? else {
                continue;
            };
            for (name, member) in library.files(directory.id)?.into_iter().filter(|(name, _)| name.ends_with(".json")) {
                let label = format!("{prefix}/{name}");
                let row = library.read_json(member.id, "")?;
                if prefix == "sittings" {
                    if row["exit"].is_null() {
                        limitations.push(format!("{label}: process exit unobserved."));
                    }
                    for item in row["limitations"].as_array().into_iter().flatten() {
                        limitations.push(format!("{label}: {}", json::display(item)));
                    }
                } else if json::truthy(&row["limitation"]) {
                    limitations.push(format!("{label}: {}", json::display(&row["limitation"])));
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
        library.write_json(node, "coverage.json", &coverage)?;
        let members = hashes(&shelf)?;
        library.write_json(node, MANIFEST, &json!({"schema": SCHEMA, "episode": state["id"], "members": members}))?;
        library.set_cleanup(id, Some(&json!(inventory)))
    })
}

/// Remove the return files the record holds, where each still has the bytes that were collected.
///
/// A file goes only while the library still holds it under `return/` with the collected digest.
/// Anything else stays: a file that changed, arrived later, was never collected, or whose copy
/// the producer has since removed from the record. Directories left empty are removed.
pub fn retire(library: &Library, node: i64, directory: &Path, inventory: &Value) -> Result<()> {
    for item in inventory.as_array().into_iter().flatten() {
        let (Some(name), Some(digest)) = (item["path"].as_str(), item["sha256"].as_str()) else {
            continue;
        };
        let held = library.find(node, &format!("return/{name}"))?.and_then(|member| member.sha256);
        let path = directory.join(name);
        let unchanged = held.as_deref() == Some(digest)
            && files::regular(&path).is_ok()
            && fs::File::open(&path).ok().and_then(|file| files::digest_reader(file).ok()).as_deref() == Some(digest);
        if unchanged {
            let _ = fs::remove_file(&path);
        }
    }
    let mut directories = vec![directory.to_path_buf()];
    let mut next = 0;
    while next < directories.len() {
        let listing = fs::read_dir(&directories[next]).map(|entries| entries.flatten().collect::<Vec<_>>());
        for entry in listing.unwrap_or_default() {
            if entry.file_type().is_ok_and(|kind| kind.is_dir()) {
                directories.push(entry.path());
            }
        }
        next += 1;
    }
    // Deepest first. A directory that still holds something refuses and stays.
    for emptied in directories.iter().skip(1).rev() {
        let _ = fs::remove_dir(emptied);
    }
    Ok(())
}

/// A record a producer selected: in the library, or a directory on disk.
pub enum Source {
    Library(String),
    Directory(PathBuf),
}

impl Source {
    /// A reference that begins with `/`, `./`, `../` or `~` names a directory on disk. Anything else is in the library.
    pub fn named(text: &str) -> Result<Source> {
        let local = text.starts_with('/') || text.starts_with("./") || text.starts_with("../") || text.starts_with('~');
        if local || text == "." || text == ".." {
            return Ok(Source::Directory(files::physical(text)?));
        }
        Ok(Source::Library(text.to_string()))
    }
}

/// The library directory a reference names: an episode by ID, or any directory by path.
fn shelf<'a>(library: &'a Library, reference: &str) -> Result<Shelf<'a>> {
    if files::is_uuid(reference) {
        return Ok(Shelf::new(library, episodes::resolve(library, reference)?.1));
    }
    let found = library.expect(ROOT, reference)?;
    if !found.directory {
        return refuse(format!("Expected a record directory in the library: {reference}"));
    }
    Ok(Shelf::new(library, found.id))
}

pub fn verify_source(
    library: impl FnOnce() -> Result<Library>,
    source: &Source,
    expected: Option<&str>,
) -> Result<Value> {
    match source {
        Source::Directory(path) => verify(&Directory(path.clone()), expected),
        Source::Library(reference) => {
            let library = library()?;
            verify(&shelf(&library, reference)?, expected)
        }
    }
}

/// Prepare a review in the library: the question, verified copies of the selected records, and an answer file.
pub fn review(library: &Library, sources: &[Source], question: &str, output: &str) -> Result<Value> {
    if question.trim().is_empty() {
        return refuse("Supply the question for the selected records");
    }
    let target = parts(output)?;
    if target.is_empty() {
        return refuse("Name a library destination for the review, such as reviews/cutover-question");
    }
    library.transaction(|| {
        for source in sources {
            if let Source::Library(reference) = source {
                let from = library.path(shelf(library, reference)?.node)?;
                let from = parts(&from)?;
                if target.starts_with(&from) || from.starts_with(&target) {
                    return refuse("Use a review destination outside each source record");
                }
            }
        }
        let directory = library.make_directory(ROOT, output)?;
        let mut selected: Vec<Value> = Vec::new();
        for source in sources {
            let verified = match source {
                Source::Directory(path) => verify(&Directory(path.clone()), None)?,
                Source::Library(reference) => verify(&shelf(library, reference)?, None)?,
            };
            let identity = verified["episode"].as_str().unwrap_or_default();
            if selected.iter().any(|known| known["episode"].as_str() == Some(identity)) {
                return refuse(format!("Episode selected twice: {identity}"));
            }
            let relative = format!("sources/{identity}");
            let destination = format!("{output}/{relative}");
            let copy = match source {
                Source::Directory(path) => library.put(path, &destination, false)?,
                Source::Library(reference) => {
                    let from = library.path(shelf(library, reference)?.node)?;
                    library.directories(directory.id, &["sources"])?;
                    library.copy(&from, &destination)?
                }
            };
            verify(&Shelf::new(library, copy.id), verified["sha256"].as_str())?;
            selected.push(json!({"episode": identity, "path": relative, "sha256": verified["sha256"]}));
        }
        library.write_json(directory.id, "sources.json", &json!(selected))?;
        library.write(directory.id, "question.md", format!("{}\n", question.trim_end()).as_bytes())?;
        library.write(
            directory.id,
            "answer.md",
            b"<!-- Write your answer here. Cite the relative source path and passage \
              for each conclusion, and identify gaps that affect your answer. -->\n",
        )?;
        Ok(json!({
            "output": output,
            "question": format!("{output}/question.md"),
            "answer": format!("{output}/answer.md"),
            "sources": selected,
        }))
    })
}

/// The sources a review directory lists: for each, its path inside the review and its manifest digest.
fn listed_sources(directory: &Path) -> Option<Vec<(String, String)>> {
    let listed = files::read(&directory.join("sources.json")).ok()?;
    let entries = listed.as_array().filter(|entries| !entries.is_empty())?;
    entries
        .iter()
        .map(|entry| {
            let path = parts(entry["path"].as_str()?).ok().filter(|names| !names.is_empty())?.join("/");
            Some((path, entry["sha256"].as_str().filter(|digest| files::is_sha256(digest))?.to_string()))
        })
        .collect()
}

/// What kind of Berman directory this is on disk, if any. A file that only shares a name does not count.
fn kind(directory: &Path) -> Option<&'static str> {
    let schema = |name: &str| {
        files::read(&directory.join(name)).ok().and_then(|value| json::text(&value, "schema").map(str::to_string))
    };
    if schema(MANIFEST).as_deref() == Some(SCHEMA) {
        Some("record")
    } else if listed_sources(directory).is_some() {
        Some("review")
    } else if schema("episode.json").is_some_and(|found| episodes::SCHEMAS.contains(&found.as_str())) {
        Some("episode")
    } else {
        None
    }
}

/// Bring a closed record or a review from a directory on disk into a fresh library destination.
///
/// The directory is verified first and left intact. The library copy keeps the original manifest
/// bytes and is verified against their digest.
pub fn import(library: &Library, directory: &Path, destination: &str) -> Result<Value> {
    match kind(directory) {
        Some("record") => {
            let verified = verify(&Directory(directory.to_path_buf()), None)?;
            let identity = verified["episode"].as_str().unwrap_or_default();
            if let Some(known) = library.episode(identity)? {
                let place = match known.node {
                    Some(node) => format!("at {}", library.path(node)?),
                    None => "and its directory was removed".to_string(),
                };
                return refuse(format!("Episode {identity} is already known to the library {place}"));
            }
            library.transaction(|| {
                let copy = library.put(directory, destination, false)?;
                verify(&Shelf::new(library, copy.id), verified["sha256"].as_str())?;
                library.add_episode(identity, copy.id, "imported", None)?;
                Ok(json!({
                    "imported": "record",
                    "episode": identity,
                    "path": library.path(copy.id)?,
                    "sha256": verified["sha256"],
                    "from": files::text(directory),
                    "coverage": verified["coverage"],
                }))
            })
        }
        Some("review") => {
            let sources = listed_sources(directory).unwrap_or_default();
            for (path, digest) in &sources {
                verify(&Directory(directory.join(path)), Some(digest))?;
            }
            // A source the review does not list would be carried without having been verified.
            if let Ok(present) = fs::read_dir(directory.join("sources")) {
                for entry in present.flatten() {
                    let name = format!("sources/{}", entry.file_name().to_string_lossy());
                    if !sources.iter().any(|(path, _)| *path == name || path.starts_with(&format!("{name}/"))) {
                        return refuse(format!("{name} is not listed in sources.json"));
                    }
                }
            }
            library.transaction(|| {
                let copy = library.put(directory, destination, false)?;
                for (path, digest) in &sources {
                    verify(&Shelf::new(library, library.expect(copy.id, path)?.id), Some(digest))?;
                }
                let listed: Vec<Value> =
                    sources.iter().map(|(path, digest)| json!({"path": path, "sha256": digest})).collect();
                Ok(json!({
                    "imported": "review",
                    "path": library.path(copy.id)?,
                    "sources": listed,
                    "from": files::text(directory),
                }))
            })
        }
        Some(_) => refuse(
            "This is an episode directory from an earlier release, not its record. Close the episode with the \
             release that opened it, then import its record directory.",
        ),
        None => refuse(format!(
            "Expected a record or review directory: {}. Use berman library put for other files.",
            directory.display()
        )),
    }
}

/// List the plaintext episode, record and review directories that earlier releases left below a directory.
pub fn legacy(library: Option<&Library>, top: &Path) -> Result<Value> {
    files::safe_directory(top)?;
    let mut found = Vec::new();
    let mut pending = vec![top.to_path_buf()];
    while let Some(directory) = pending.pop() {
        let Some(kind) = kind(&directory) else {
            let mut below: Vec<PathBuf> = match fs::read_dir(&directory) {
                Ok(listing) => listing.flatten().map(|entry| entry.path()).collect(),
                Err(_) => continue,
            };
            below.retain(|path| path.symlink_metadata().is_ok_and(|metadata| metadata.is_dir()));
            below.sort();
            pending.extend(below.into_iter().rev());
            continue;
        };
        let mut row = json!({"kind": kind, "path": files::text(&directory)});
        let identity = match kind {
            "record" => files::read(&directory.join(MANIFEST)).ok().map(|manifest| manifest["episode"].clone()),
            "episode" => files::read(&directory.join("episode.json")).ok().map(|state| state["id"].clone()),
            _ => None,
        };
        if kind == "episode" {
            row["closed"] = json!(files::exists(&directory.join("ending.json")));
            // An earlier release kept the record inside the episode directory.
            row["record"] = match kind_of_record(&directory.join("record")) {
                true => json!(files::text(&directory.join("record"))),
                false => Value::Null,
            };
        }
        if let Some(identity) = identity.as_ref().and_then(Value::as_str) {
            row["episode"] = json!(identity);
            if let Some(library) = library {
                let known = library.episode(identity)?.and_then(|episode| episode.node);
                row["in_library"] = match known {
                    Some(node) => json!(library.path(node)?),
                    None => Value::Null,
                };
            }
        }
        found.push(row);
    }
    Ok(json!(found))
}

fn kind_of_record(directory: &Path) -> bool {
    directory.is_dir() && kind(directory) == Some("record")
}
