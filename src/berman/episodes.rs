//! An episode is opened and closed by its producer. Native process exits are sitting facts.
//!
//! The episode's files live in a directory of the producer's library. The actor's return
//! directory is a real directory with the same path at every sitting. A sitting is attended in
//! the producer's terminal, or unattended with the native output kept in the library as it
//! arrives and read for the response the harness reports.

use std::fs::{self, File, OpenOptions};
use std::io::Read;
use std::os::fd::AsRawFd;
use std::os::unix::fs::OpenOptionsExt;
use std::os::unix::process::{CommandExt, ExitStatusExt};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::time::Duration;

use serde_json::{Value, json};

use crate::error::{At, Error, Result, refuse};
use crate::harness::{Harness, Launch, Outcome};
use crate::library::{Episode, Library, ROOT};
use crate::tree::Shelf;
use crate::{capture, files, installation, json, places, process, project, records};

pub const SCHEMAS: [&str; 2] = ["berman.episode/1", "berman.episode/2"];

pub fn install_template(destination: &Path) -> Result<Value> {
    files::new_directory(destination, |stage| {
        for (name, content) in installation::JOB_TEMPLATES {
            files::atomic(&stage.join(name), content.as_bytes())?;
        }
        Ok(())
    })?;
    Ok(json!(files::text(destination)))
}

fn harness_home(harness: Harness) -> PathBuf {
    let home = match std::env::var_os(harness.home_variable()).filter(|home| !home.is_empty()) {
        Some(home) => PathBuf::from(home),
        None => PathBuf::from(std::env::var_os("HOME").unwrap_or_default()).join(harness.default_home()),
    };
    files::resolve(&home)
}

/// The message the actor receives first: the episode, any additional jobs, and the return directory.
fn opening_text(identity: &str, project_path: &Path, jobs: &[String], return_directory: &Path) -> String {
    let mut about = format!(
        "This session is part of a Berman episode its producer opened in {}. \
         Berman keeps a record of the session for the producer.",
        project_path.display()
    );
    if jobs.is_empty() {
        about.push_str(" The producer has given no additional job.");
    } else {
        let assignment = if jobs.len() == 1 { "this additional job" } else { "these additional jobs" };
        let instructions = jobs.join("\n\n---\n\n");
        about.push_str(&format!(
            "\n\nAlongside any other work in the session, carry out {assignment}:\n\n{}",
            instructions.trim_end()
        ));
    }
    format!(
        "Berman episode {identity}\n\n{about}\n\nReturn directory: {}\nKeep ordinary project changes in the checkout.\n",
        return_directory.display()
    )
}

/// The episode a reference names: its ID, or the library path of its directory.
pub fn resolve(library: &Library, reference: &str) -> Result<(Episode, i64)> {
    let found = if files::is_uuid(reference) {
        library.episode(reference)?
    } else {
        match library.find(ROOT, reference)? {
            Some(node) => library.episode_at(node.id)?,
            None => return Err(Error::Missing(format!("No such file or directory in the library: {reference}"))),
        }
    };
    match found {
        Some(Episode { node: Some(node), .. }) => Ok((found.expect("an episode"), node)),
        Some(episode) => refuse(format!("The directory of episode {} was removed from the library", episode.id)),
        None => refuse(format!("No episode is at {reference} in the library")),
    }
}

/// The return directory at the path the opening gave the actor, whatever this command's environment says.
fn return_directory(episode: &Episode) -> Option<PathBuf> {
    episode.return_path.as_ref().map(PathBuf::from)
}

pub fn open_episode(
    library: &Library,
    destination: &str,
    checkout: &Path,
    actor: &str,
    job_files: &[PathBuf],
) -> Result<Value> {
    let harness = Harness::named(actor)?;
    let project_path = project::top(checkout)?;
    let native_home = harness_home(harness);
    let places = &library.places;
    // The checkout stays clear of everything Berman keeps. A harness home may be one of the dedicated homes.
    let own = [&places.data, &places.returns, &places.homes];
    let overlaps = |path: &Path| own.iter().any(|kept| kept.starts_with(path) || path.starts_with(kept));
    let dedicated = native_home.starts_with(&places.homes) && native_home != places.homes;
    if overlaps(&project_path) || (overlaps(&native_home) && !dedicated) {
        return refuse("Keep the checkout and harness home apart from the library and the return directories");
    }
    let mut jobs = Vec::new();
    for path in job_files {
        let content = String::from_utf8(fs::read(path).at(path)?)
            .map_err(|_| Error::Refused(format!("The additional job is not UTF-8 text: {}", path.display())))?;
        if content.trim().is_empty() {
            return refuse(format!("The additional job needs opening instructions in {}", path.display()));
        }
        jobs.push(content);
    }
    if crate::library::parts(destination)?.is_empty() {
        return refuse("Name a library destination for the episode, such as episodes/cutover");
    }
    library.outside_episodes(destination)?;
    if let Some(existing) = library.find(ROOT, destination)? {
        let Some(Episode { node: Some(node), .. }) = library.episode_at(existing.id)? else {
            return refuse(format!("Destination already exists in the library: {destination}"));
        };
        let state = load(library, node)?;
        let same = state["project"].as_str() == Some(&files::text(&project_path))
            && state["actor"].as_str() == Some(actor)
            && state["jobs"] == json!(jobs)
            && state["home"].as_str() == Some(&files::text(&native_home));
        if !same {
            return refuse("An episode with different instructions or settings already occupies this path");
        }
        return Ok(state);
    }
    let identity = files::uuid();
    let returns = places.returns.join(files::uuid_hex());
    let opening = opening_text(&identity, &project_path, &jobs, &returns);
    let state = json!({
        "schema": SCHEMAS[1],
        "id": identity,
        "opened_at": files::now(),
        "actor": actor,
        "project": files::text(&project_path),
        "home": files::text(&native_home),
        "return": files::text(&returns),
        "jobs": jobs,
        "opening": opening,
        "initial_project": project::git_state(&project_path)?,
    });
    places::unlisted(&places.returns)?;
    files::create_private_directory(&returns)?;
    let opened = (|| {
        places::private(&places.data.join("locks"))?;
        for kind in ["sitting", "capture"] {
            let lock = places.lock(&identity, kind);
            OpenOptions::new().write(true).create_new(true).mode(0o600).open(&lock).at(&lock)?;
        }
        library.transaction(|| {
            let directory = library.make_directory(ROOT, destination)?;
            library.write_json(directory.id, "episode.json", &state)?;
            library.write(directory.id, "opening.md", opening.as_bytes())?;
            library.add_episode(&identity, directory.id, "opened", Some(&files::text(&returns)), None)
        })
    })();
    if let Err(error) = opened {
        let _ = fs::remove_dir(&returns);
        for kind in ["sitting", "capture"] {
            let _ = fs::remove_file(places.lock(&identity, kind));
        }
        return Err(error);
    }
    Ok(state)
}

pub fn load(library: &Library, node: i64) -> Result<Value> {
    let state = library.read_json(node, "episode.json")?;
    if !SCHEMAS.iter().any(|schema| json::text(&state, "schema") == Some(schema)) {
        return refuse("Expected a Berman episode");
    }
    Ok(state)
}

fn harness_of(state: &Value) -> Result<Harness> {
    Harness::named(state["actor"].as_str().unwrap_or_default())
}

/// The native session bound to the episode, or the one Berman assigns before the first sitting.
fn bound_session(library: &Library, id: &str, state: &Value) -> Result<Option<String>> {
    match library.episode(id)?.and_then(|episode| episode.session) {
        Some(session) => Ok(Some(session)),
        None => Ok(harness_of(state)?.assigned_session(state)),
    }
}

/// Whether a native session for the episode is known: bound, or Claude's assigned one on disk.
fn session_started(library: &Library, id: &str, state: &Value) -> Result<bool> {
    if library.episode(id)?.is_some_and(|episode| episode.session.is_some()) {
        return Ok(true);
    }
    let harness = harness_of(state)?;
    let home = PathBuf::from(state["home"].as_str().unwrap_or_default());
    Ok(harness.assigned_session(state).is_some_and(|session| !harness.candidates(&home, &session).is_empty()))
}

/// Some actor CLIs drop the inherited lease, so check the process a sitting recorded.
fn refuse_while_running(sittings: &[Value]) -> Result<()> {
    for sitting in sittings {
        let (Some(started), Some(pid)) = (sitting["pid_started"].as_str(), sitting["pid"].as_i64()) else {
            continue;
        };
        if sitting["exit"].is_null() && process::started(pid).as_deref() == Some(started) {
            return refuse(format!("The actor from sitting {} is still running as process {pid}", sitting["number"]));
        }
    }
    Ok(())
}

/// What is still in a closed episode's return directory, which collection left for the producer.
fn preserved(directory: Option<&Path>) -> Vec<String> {
    let Some(directory) = directory.filter(|directory| files::exists(directory)) else {
        return Vec::new();
    };
    if files::safe_directory(directory).is_err() {
        // Something other than the directory Berman made is at its path. It is left alone and named.
        return vec![files::text(directory)];
    }
    let mut found = crate::tree::entries(directory).unwrap_or_default();
    found.extend(crate::tree::entries(&records::aside(directory)).unwrap_or_default());
    found.into_iter().map(|(path, _)| files::text(&path)).collect()
}

pub fn status(library: &Library, reference: &str) -> Result<Value> {
    let (episode, node) = resolve(library, reference)?;
    let state = load(library, node)?;
    let path = library.path(node)?;
    // The ending is the library's own record of it, whatever has become of ending.json.
    let ending: Option<Value> = episode.ending.as_deref().map(serde_json::from_str).transpose()?;
    let imported = if episode.origin == "opened" { None } else { library.read_json(node, "ending.json").ok() };
    let closed = episode.closed();
    let returns = return_directory(&episode);
    let mut status = json!({
        "episode": state["id"],
        "actor": state["actor"],
        "project": state["project"],
        "path": path,
        "opening": format!("{path}/opening.md"),
        "return": returns.as_deref().map(files::text),
        "session": episode.session,
        "ending": ending.or(imported),
        "sittings": capture::sittings(library, node)?,
        "record": if library.exists(node, records::MANIFEST)? { json!(path) } else { Value::Null },
    });
    if closed {
        status["preserved"] = json!(preserved(returns.as_deref()));
    }
    Ok(status)
}

extern "C" fn interrupted(_signal: libc::c_int) {}

/// Start the process that carries one output of an unattended actor into the library.
fn relay(library: &Library, episode: &str, name: &str) -> Result<Child> {
    let executable = std::env::current_exe()?;
    let mut command = Command::new(&executable);
    command.args(["relay", episode, name]).env("BERMAN_LIBRARY", &library.places.data);
    command.stdin(Stdio::piped()).stdout(Stdio::null()).stderr(Stdio::null());
    // SAFETY: setsid is async-signal-safe. In its own session the relay is not interrupted with the terminal.
    unsafe {
        command.pre_exec(|| {
            libc::setsid();
            Ok(())
        });
    }
    command.spawn().at(&executable)
}

/// Carry standard input into the library as one output of a sitting, until the writers close it.
pub fn relay_output(library: &Library, episode: &str, name: &str) -> Result<()> {
    let mut input = std::io::stdin().lock();
    let mut buffer = vec![0u8; 1 << 16];
    let (mut kept, mut failed) = (0u64, false);
    loop {
        let count = match input.read(&mut buffer) {
            Ok(0) => break,
            Ok(count) => count,
            Err(error) if error.kind() == std::io::ErrorKind::Interrupted => continue,
            Err(_) => break,
        };
        // Keep what fits the limit for one file. The rest is read and dropped so the actor can finish, and
        // so is everything after a piece the library could not take.
        let room = files::FILE_LIMIT.saturating_sub(kept).min(count as u64) as usize;
        kept += count as u64;
        if room > 0 && !failed {
            failed = library.append_stream(episode, name, &buffer[..room]).is_err();
        }
    }
    library.end_stream(episode, name, failed || kept > files::FILE_LIMIT)
}

/// Start or retry a sitting. An opening sitting delivers the opening; others resume the session.
pub fn launch(
    library: &Library,
    reference: &str,
    request: Option<&str>,
    prompt: Option<&str>,
    attended: bool,
    opening: bool,
) -> Result<Value> {
    let (episode, node) = resolve(library, reference)?;
    let id = episode.id.as_str();
    // Whether the episode is closed is the library's own knowledge. Renaming ending.json cannot reopen it.
    let closed = |episode: Option<Episode>| match episode {
        Some(episode) if !episode.closed() => Ok(()),
        _ => refuse("The producer has closed this episode"),
    };
    closed(Some(episode.clone()))?;
    let lease = library.lease(id, "sitting", false)?;
    let state = load(library, node)?;
    closed(library.episode(id)?)?;
    let actor = state["actor"].as_str().unwrap_or_default();
    let harness = harness_of(&state)?;
    let recent = capture::sittings(library, node)?;
    refuse_while_running(&recent)?;
    for mut sitting in recent.iter().cloned() {
        if sitting["request"].as_str() != request {
            continue;
        }
        let recorded_attended = sitting.get("attended").and_then(Value::as_bool).unwrap_or(true);
        let same = sitting["prompt"].as_str() == prompt
            && recorded_attended == attended
            && capture::delivers_opening(&sitting) == opening;
        if !same {
            return refuse("This request already started a sitting with different instructions");
        }
        let lost = !attended && json::truthy(&sitting["pid"]) && sitting["exit"].is_null();
        if lost && sitting.get("response").is_none() {
            // The launcher stopped before the exit, and no recorded process still writes this output.
            let mut account = account(library, id, node, &state, harness, &sitting)?;
            if let Some(limitations) = account["limitations"].as_array_mut() {
                limitations.insert(0, json!("The launcher did not observe the process exit."));
            }
            merge(&mut sitting, account);
            library.write_json(node, &sitting_path(&sitting), &sitting)?;
        }
        return Ok(sitting);
    }
    if !opening && recent.is_empty() {
        return refuse("Start the first sitting before resuming");
    }
    if opening && !recent.is_empty() && session_started(library, id, &state)? {
        return refuse("The actor’s session has already started. Continue it with resume.");
    }
    if !opening && !session_started(library, id, &state)? {
        return refuse(
            "No native session is known for this episode. If the first sitting ended before the actor’s CLI \
             started one, start again with a new --request. For Codex, also check that the episode hook ran.",
        );
    }
    if library.read(node, "opening.md")? != state["opening"].as_str().unwrap_or_default().as_bytes() {
        return refuse("opening.md differs from the opening recorded in episode.json");
    }
    let checkout = PathBuf::from(state["project"].as_str().unwrap_or_default());
    if !checkout.is_dir() {
        return refuse(format!("The checkout is unavailable at {}", checkout.display()));
    }
    let Some(executable) = files::which(actor) else {
        return refuse(format!("Install {actor} in this login before launching"));
    };
    let session = bound_session(library, id, &state)?;
    let Some(returns) = return_directory(&episode) else {
        return refuse("This episode has no return directory");
    };
    files::safe_directory(&returns)?;
    let settings = library.places.claude_settings();
    if harness == Harness::Claude {
        files::write(&settings, &capture::hook_settings(harness)?)?;
    }
    let mut arguments = harness.arguments(&Launch {
        return_directory: &returns,
        settings: &settings,
        session: session.as_deref(),
        resuming: !opening,
        attended,
    })?;
    let message = if opening { Some(capture::first_message(&state, prompt)) } else { prompt.map(str::to_string) };
    if let Some(message) = message.filter(|message| !message.is_empty()) {
        arguments.extend(["--".to_string(), message]);
    }
    let number = recent.len() + 1;
    let mut sitting = json!({
        "number": number,
        "request": request,
        "prompt": prompt,
        "opening": opening,
        "attended": attended,
        "started_at": files::now(),
        "executable": files::text(&executable),
        "exit": null,
    });
    if !attended {
        sitting["stdout"] = json!(format!("sittings/{number:06}.stdout"));
        sitting["stderr"] = json!(format!("sittings/{number:06}.stderr"));
    }
    let path = sitting_path(&sitting);
    library.write_json(node, &path, &sitting)?;
    let mut command = Command::new(&executable);
    // Only the launched sitting learns which episode it belongs to.
    command
        .args(&arguments)
        .current_dir(&checkout)
        .env("BERMAN_EPISODE", id)
        .env("BERMAN_LIBRARY", &library.places.data);
    command.env(harness.home_variable(), state["home"].as_str().unwrap_or_default());
    let mut relays = Vec::new();
    if !attended {
        // An unattended actor has no terminal. A relay for each output puts it into the library as it
        // arrives, and outlives the launcher so that a retry can still read what the actor reported.
        command.stdin(Stdio::null());
        for name in ["stdout", "stderr"] {
            let mut relay = relay(library, id, sitting[name].as_str().unwrap_or_default())?;
            let pipe = relay.stdin.take().ok_or_else(|| Error::Refused("The output relay has no input".into()))?;
            match name {
                "stdout" => command.stdout(pipe),
                _ => command.stderr(pipe),
            };
            relays.push(relay);
        }
    }
    keep_lease(&mut command, &lease);
    // SAFETY: the handler does nothing, so any thread may be interrupted by it.
    unsafe { libc::signal(libc::SIGINT, interrupted as extern "C" fn(libc::c_int) as libc::sighandler_t) };
    let spawned = command.spawn().at(&executable);
    // The actor now holds the only writing ends of its output. Dropping ours lets each relay see the end.
    drop(command);
    let mut child = match spawned {
        Ok(child) => child,
        Err(error) => {
            sitting["error"] = json!(error.to_string());
            library.write_json(node, &path, &sitting)?;
            return Err(error);
        }
    };
    sitting["pid"] = json!(child.id());
    sitting["pid_started"] = json!(process::started(i64::from(child.id())));
    library.write_json(node, &path, &sitting)?;
    // The actor process receives terminal interrupts directly. The launcher waits for its exit.
    let ended = child.wait()?;
    // Each relay ends when the actor and its descendants have closed the output it carries.
    let patience = std::time::Instant::now() + Duration::from_secs(5);
    while relays.iter_mut().any(|relay| matches!(relay.try_wait(), Ok(None))) && std::time::Instant::now() < patience {
        std::thread::sleep(Duration::from_millis(20));
    }
    let code = ended.code().or_else(|| ended.signal().map(|signal| -signal));
    sitting["exit"] = json!(code);
    sitting["exited_at"] = json!(files::now());
    if !attended {
        let account = account(library, id, node, &state, harness, &sitting)?;
        merge(&mut sitting, account);
    }
    sitting["transcript"] = transcript_at_end(library, id, node, &state)?;
    library.write_json(node, &path, &sitting)?;
    Ok(sitting)
}

/// Read the session's transcript as the sitting ends, so that closing need not read it later.
///
/// Whatever is said in the same native session after this, outside a sitting, is then not
/// collected. The value says when the transcript was read, is null when the session has none,
/// or gives the reason it could not be read.
fn transcript_at_end(library: &Library, id: &str, node: i64, state: &Value) -> Result<Value> {
    let _capture = library.lease(id, "capture", true)?;
    let Some(session) = bound_session(library, id, state)? else {
        return Ok(Value::Null);
    };
    Ok(match capture::observe(library, node, state, &session, &json!({"hook_event_name": "sitting"})) {
        Ok(read) => json!({"read_at": read["captured_at_utc"], "saved_bytes": read["saved_bytes"]}),
        Err(Error::Missing(_)) => Value::Null,
        Err(error) => json!({"limitation": error.to_string()}),
    })
}

/// Keep the lease in the child as well, in case the launcher is interrupted.
fn keep_lease(command: &mut Command, lease: &File) {
    let descriptor = lease.as_raw_fd();
    // SAFETY: fcntl is async-signal-safe, and the descriptor stays open until after the spawn.
    unsafe {
        command.pre_exec(move || {
            if libc::fcntl(descriptor, libc::F_SETFD, 0) == -1 {
                return Err(std::io::Error::last_os_error());
            }
            Ok(())
        });
    }
}

fn sitting_path(sitting: &Value) -> String {
    format!("sittings/{:06}.json", sitting["number"].as_u64().unwrap_or_default())
}

fn merge(sitting: &mut Value, more: Value) {
    if let (Some(target), Value::Object(more)) = (sitting.as_object_mut(), more) {
        target.extend(more);
    }
}

/// Report what an unattended sitting's native output says, without supplying what it lacks.
fn account(library: &Library, id: &str, node: i64, state: &Value, harness: Harness, sitting: &Value) -> Result<Value> {
    let mut cut = Vec::new();
    let mut output = |name: &str| -> Result<Vec<u8>> {
        let path = sitting[name].as_str().unwrap_or_default();
        if library.settle_stream(id, node, path)? {
            cut.push(format!(
                "The native {name} was not kept in full: it passed the limit for one file or could not be stored."
            ));
        }
        library.read(node, path)
    };
    let (stdout, stderr) = (output("stdout"), output("stderr"));
    let outcome = match stdout {
        Ok(bytes) => harness.outcome(&bytes),
        Err(error) => Outcome {
            session: None,
            response: None,
            denials: Value::Null,
            limitations: vec![format!("Native output unread: {error}")],
        },
    };
    let mut limitations = outcome.limitations;
    limitations.extend(cut);
    if let Some(reported) = &outcome.session {
        let _capture = library.lease(id, "capture", true)?;
        let bound = bound_session(library, id, state)?;
        if bound.is_none() && capture::identity(reported) {
            // No callback bound the first sitting. The process Berman launched named its own session.
            library.bind_session(id, reported)?;
        } else if bound.as_ref() != Some(reported) {
            let bound = bound.unwrap_or_else(|| "None".into());
            limitations.push(format!("The native output names session {reported}, not the bound session {bound}."));
        }
    }
    if let Some(code) = sitting["exit"].as_i64().filter(|code| *code != 0) {
        let mut ending = if code < 0 {
            format!("The actor process ended on signal {}.", -code)
        } else {
            format!("The actor exited with status {code}.")
        };
        let errors = stderr.map(|bytes| String::from_utf8_lossy(&bytes).into_owned()).unwrap_or_default();
        if let Some(last) = errors.lines().rev().map(str::trim).find(|line| !line.is_empty()) {
            ending.push_str(&format!(" Its error output ends: {}", last.chars().take(300).collect::<String>()));
        }
        limitations.push(ending);
    }
    Ok(json!({
        "native_session": outcome.session,
        "response": outcome.response,
        "denials": outcome.denials,
        "limitations": limitations,
    }))
}

/// Record the producer's ending, collect the correspondence into the record, and retire what was collected.
///
/// Nothing leaves the return directory until the committed record has been read back and
/// verified, and the library remembers that it was. Until then a repeated close collects and
/// publishes again. After it, a repeated close takes cleanup up from the committed inventory.
pub fn close(library: &Library, reference: &str, reason: &str) -> Result<Value> {
    if reason.trim().is_empty() {
        return refuse("The producer must supply an ending reason");
    }
    let (episode, node) = resolve(library, reference)?;
    let identity = episode.id.clone();
    let id = identity.as_str();
    if episode.origin != "opened" {
        return refuse("This episode was closed where its record was made");
    }
    let same_reason = |episode: &Episode| -> Result<Option<Value>> {
        let ending: Option<Value> = episode.ending.as_deref().map(serde_json::from_str).transpose()?;
        match &ending {
            Some(recorded) if recorded["reason"].as_str() != Some(reason) => {
                refuse("A different ending is already recorded")
            }
            _ => Ok(ending),
        }
    };
    if episode.return_path.is_none() {
        // Collection and cleanup are complete. Only the ending can still disagree.
        same_reason(&episode)?;
        let mut receipt = library.snapshot(|| records::verify(&Shelf::new(library, node), None))?;
        receipt["return"] = Value::Null;
        receipt["preserved"] = json!([]);
        return Ok(receipt);
    }
    let _sitting = library.lease(id, "sitting", false)?;
    let _capture = library.lease(id, "capture", true)?;
    let episode = library.episode(id)?.unwrap_or(episode);
    let state = load(library, node)?;
    let sittings = capture::sittings(library, node)?;
    refuse_while_running(&sittings)?;
    let ending = match same_reason(&episode)? {
        Some(ending) => ending,
        None => {
            // From here no sitting starts and callbacks are no longer taken in.
            let ending = json!({"reason": reason, "at": files::now()});
            library.set_ending(id, &ending)?;
            ending
        }
    };
    let returns = return_directory(&episode);
    let mut receipt = None;
    if episode.record.is_none() {
        // The record is the library's account of the ending, even if a file of that name was moved or changed.
        library.write_json(node, "ending.json", &ending)?;
        let mut limitations = Vec::new();
        // Each sitting read the transcript as it ended. It is read here only if the last sitting's end went
        // unobserved, and then anything said in that session since is in it.
        let unobserved = sittings.last().filter(|sitting| sitting["exit"].is_null());
        if let (Some(sitting), Some(session)) = (unobserved, bound_session(library, id, &state)?) {
            let closeout = json!({"hook_event_name": "closeout"});
            match capture::observe(library, node, &state, &session, &closeout) {
                Ok(_) => limitations.push(format!(
                    "The parent transcript was read at closing, because the end of sitting {} was not observed. \
                     It can hold what was said in that session afterwards.",
                    sitting["number"]
                )),
                Err(error) => limitations.push(format!("Final parent transcript observation: {error}")),
            }
        }
        // Every output a sitting names becomes a file, including one a relay carried after its launcher stopped.
        for sitting in sittings.iter().filter(|sitting| sitting["attended"] == json!(false)) {
            for name in ["stdout", "stderr"].iter().filter_map(|name| sitting[*name].as_str()) {
                library.settle_stream(id, node, name)?;
            }
        }
        library.durable()?;
        records::publish(library, &episode, node, &state, limitations, returns.as_deref())?;
        library.clear_streams(id)?;
        // Read the committed record back before anything outside the library is retired.
        let verified = library.snapshot(|| records::verify(&Shelf::new(library, node), None))?;
        library.set_record(id, verified["sha256"].as_str())?;
        receipt = Some(verified);
    }
    let cleanup = library.episode(id)?.and_then(|episode| episode.cleanup);
    if let Some(directory) = &returns {
        let inventory: Value = match &cleanup {
            Some(text) => serde_json::from_str(text)?,
            None => json!([]),
        };
        records::retire(library, node, directory, &inventory)?;
        let gone = |path: &Path| fs::remove_dir(path).is_ok() || !files::exists(path);
        if gone(&records::aside(directory)) && gone(directory) {
            library.forget_return(id)?;
            for kind in ["sitting", "capture"] {
                let _ = fs::remove_file(library.places.lock(id, kind));
            }
        }
    }
    // A record the producer has changed since it was verified no longer verifies. Cleanup above does not
    // depend on that: it removes a file only while the record still holds its copy.
    let mut receipt = match receipt {
        Some(receipt) => receipt,
        None => library.snapshot(|| records::verify(&Shelf::new(library, node), None))?,
    };
    let left = library.episode(id)?.and_then(|episode| return_directory(&episode));
    receipt["return"] = json!(left.as_deref().map(files::text));
    receipt["preserved"] = json!(preserved(left.as_deref()));
    Ok(receipt)
}
