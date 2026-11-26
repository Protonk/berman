//! An episode is opened and closed by its producer. Native process exits are sitting facts.
//!
//! A sitting is attended in the producer's terminal, or unattended with the native output kept
//! beside its record and read for the response the harness reports.

use std::fs::{self, File, OpenOptions};
use std::os::fd::AsRawFd;
use std::os::unix::fs::OpenOptionsExt;
use std::os::unix::process::{CommandExt, ExitStatusExt};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

use serde_json::{Value, json};

use crate::error::{At, Error, Result, refuse};
use crate::harness::{Harness, Launch};
use crate::{capture, files, installation, json, process, project, records};

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

pub fn open_episode(folder: &Path, checkout: &Path, actor: &str, job_files: &[PathBuf]) -> Result<Value> {
    let harness = Harness::named(actor)?;
    let project_path = project::top(checkout)?;
    let native_home = harness_home(harness);
    for path in [&project_path, &native_home] {
        if path.starts_with(folder) || folder.starts_with(path) {
            return refuse("Keep the episode, checkout and harness home in separate directories");
        }
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
    if files::exists(folder) {
        let state = load(folder)?;
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
    let opening = opening_text(&identity, &project_path, &jobs, &folder.join("return"));
    let state = json!({
        "schema": "berman.episode/1",
        "id": identity,
        "opened_at": files::now(),
        "directory": files::text(folder),
        "actor": actor,
        "project": files::text(&project_path),
        "home": files::text(&native_home),
        "jobs": jobs,
        "opening": opening,
        "initial_project": project::git_state(&project_path)?,
    });
    files::new_directory(folder, |stage| {
        fs::create_dir(stage.join("return")).at(stage)?;
        for name in [".sitting.lock", ".capture.lock"] {
            OpenOptions::new().write(true).create_new(true).mode(0o600).open(stage.join(name)).at(stage)?;
        }
        files::write(&stage.join("episode.json"), &state)?;
        files::atomic(&stage.join("opening.md"), opening.as_bytes())
    })?;
    Ok(state)
}

pub fn load(folder: &Path) -> Result<Value> {
    let state = files::read(&folder.join("episode.json"))?;
    if json::text(&state, "schema") != Some("berman.episode/1") {
        return refuse("Expected a Berman episode directory");
    }
    if state["directory"].as_str() != Some(&files::text(folder)) {
        return refuse("Operate this episode at its opened path. Its record can be read from any copy.");
    }
    Ok(state)
}

fn harness_of(state: &Value) -> Result<Harness> {
    Harness::named(state["actor"].as_str().unwrap_or_default())
}

/// The native session bound to the episode, or the one Berman assigns before the first sitting.
fn bound_session(folder: &Path, state: &Value) -> Result<Option<String>> {
    let binding = folder.join("session-id.json");
    if files::exists(&binding) {
        return Ok(files::read(&binding)?.as_str().map(str::to_string));
    }
    Ok(harness_of(state)?.assigned_session(state))
}

/// Whether a native session for the episode is known: bound, or Claude's assigned one on disk.
fn session_started(folder: &Path, state: &Value) -> Result<bool> {
    if files::exists(&folder.join("session-id.json")) {
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

pub fn status(folder: &Path) -> Result<Value> {
    let state = load(folder)?;
    let ending = folder.join("ending.json");
    let record = folder.join("record");
    Ok(json!({
        "episode": state["id"],
        "actor": state["actor"],
        "project": state["project"],
        "opening": files::text(&folder.join("opening.md")),
        "return": files::text(&folder.join("return")),
        "ending": if files::exists(&ending) { files::read(&ending)? } else { Value::Null },
        "sittings": capture::sittings(folder)?,
        "record": if record.is_dir() { json!(files::text(&record)) } else { Value::Null },
    }))
}

extern "C" fn interrupted(_signal: libc::c_int) {}

/// Start or retry a sitting. An opening sitting delivers the opening; others resume the session.
pub fn launch(
    folder: &Path,
    request: Option<&str>,
    prompt: Option<&str>,
    attended: bool,
    opening: bool,
) -> Result<Value> {
    files::safe_directory(folder)?;
    let lease = files::lock(&folder.join(".sitting.lock"), false)?;
    let state = load(folder)?;
    if files::exists(&folder.join("ending.json")) {
        return refuse("The producer has closed this episode");
    }
    let actor = state["actor"].as_str().unwrap_or_default();
    let harness = harness_of(&state)?;
    let sittings = folder.join("sittings");
    let recent = capture::sittings(folder)?;
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
            let mut account = account(folder, &state, harness, &sitting)?;
            if let Some(limitations) = account["limitations"].as_array_mut() {
                limitations.insert(0, json!("The launcher did not observe the process exit."));
            }
            merge(&mut sitting, account);
            files::write(&sitting_path(&sittings, &sitting), &sitting)?;
        }
        return Ok(sitting);
    }
    if !opening && recent.is_empty() {
        return refuse("Start the first sitting before resuming");
    }
    if opening && !recent.is_empty() && session_started(folder, &state)? {
        return refuse("The actor’s session has already started. Continue it with resume.");
    }
    if !opening && !session_started(folder, &state)? {
        return refuse(
            "No native session is known for this episode. If the first sitting ended before the actor’s CLI \
             started one, start again with a new --request. For Codex, also check that the episode hook ran.",
        );
    }
    let opening_file = folder.join("opening.md");
    if fs::read(&opening_file).at(&opening_file)? != state["opening"].as_str().unwrap_or_default().as_bytes() {
        return refuse("opening.md differs from the opening recorded in episode.json");
    }
    let checkout = PathBuf::from(state["project"].as_str().unwrap_or_default());
    if !checkout.is_dir() {
        return refuse(format!("The checkout is unavailable at {}", checkout.display()));
    }
    let Some(executable) = files::which(actor) else {
        return refuse(format!("Install {actor} in this login before launching"));
    };
    let session = bound_session(folder, &state)?;
    let return_directory = folder.join("return");
    files::safe_directory(&return_directory)?;
    let settings = folder.join("claude-hooks.json");
    if harness == Harness::Claude {
        files::write(&settings, &capture::hook_settings(harness)?)?;
    }
    let mut arguments = harness.arguments(&Launch {
        return_directory: &return_directory,
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
    let path = sitting_path(&sittings, &sitting);
    files::write(&path, &sitting)?;
    let mut command = Command::new(&executable);
    command.args(&arguments).current_dir(&checkout).env("BERMAN_EPISODE", folder);
    command.env(harness.home_variable(), state["home"].as_str().unwrap_or_default());
    let prepared = (|| {
        if !attended {
            // An unattended actor has no terminal. Its output stays beside the sitting record.
            let create = |name: &Value| {
                let output = folder.join(name.as_str().unwrap_or_default());
                OpenOptions::new().write(true).create_new(true).mode(0o600).open(&output).at(&output)
            };
            command.stdin(Stdio::null()).stdout(create(&sitting["stdout"])?).stderr(create(&sitting["stderr"])?);
        }
        keep_lease(&mut command, &lease);
        // SAFETY: the handler does nothing, so any thread may be interrupted by it.
        unsafe { libc::signal(libc::SIGINT, interrupted as extern "C" fn(libc::c_int) as libc::sighandler_t) };
        command.spawn().at(&executable)
    })();
    let mut child = match prepared {
        Ok(child) => child,
        Err(error) => {
            sitting["error"] = json!(error.to_string());
            files::write(&path, &sitting)?;
            return Err(error);
        }
    };
    sitting["pid"] = json!(child.id());
    sitting["pid_started"] = json!(process::started(i64::from(child.id())));
    files::write(&path, &sitting)?;
    // The actor process receives terminal interrupts directly. The launcher waits for its exit.
    let ended = child.wait()?;
    let code = ended.code().or_else(|| ended.signal().map(|signal| -signal));
    sitting["exit"] = json!(code);
    sitting["exited_at"] = json!(files::now());
    if !attended {
        let account = account(folder, &state, harness, &sitting)?;
        merge(&mut sitting, account);
    }
    files::write(&path, &sitting)?;
    Ok(sitting)
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

fn sitting_path(sittings: &Path, sitting: &Value) -> PathBuf {
    sittings.join(format!("{:06}.json", sitting["number"].as_u64().unwrap_or_default()))
}

fn merge(sitting: &mut Value, more: Value) {
    if let (Some(target), Value::Object(more)) = (sitting.as_object_mut(), more) {
        target.extend(more);
    }
}

/// Report what an unattended sitting's native output says, without supplying what it lacks.
fn account(folder: &Path, state: &Value, harness: Harness, sitting: &Value) -> Result<Value> {
    let output =
        |name: &str| files::stable_bytes(&folder.join(sitting[name].as_str().unwrap_or_default()), files::FILE_LIMIT);
    let outcome = match output("stdout") {
        Ok(bytes) => harness.outcome(&bytes),
        Err(error) => crate::harness::Outcome {
            session: None,
            response: None,
            denials: Value::Null,
            limitations: vec![format!("Native output unread: {error}")],
        },
    };
    let mut limitations = outcome.limitations;
    if let Some(reported) = &outcome.session {
        let _capture = files::lock(&folder.join(".capture.lock"), true)?;
        let bound = bound_session(folder, state)?;
        if bound.is_none() && capture::identity(reported) {
            // No callback bound the first sitting. The process Berman launched named its own session.
            files::keep(&folder.join("session-id.json"), &json::bytes(&json!(reported)))?;
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
        let errors = output("stderr").map(|bytes| String::from_utf8_lossy(&bytes).into_owned()).unwrap_or_default();
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

pub fn close(folder: &Path, reason: &str) -> Result<Value> {
    if reason.trim().is_empty() {
        return refuse("The producer must supply an ending reason");
    }
    files::safe_directory(folder)?;
    let _sitting = files::lock(&folder.join(".sitting.lock"), false)?;
    let _capture = files::lock(&folder.join(".capture.lock"), true)?;
    let state = load(folder)?;
    refuse_while_running(&capture::sittings(folder)?)?;
    let ending = folder.join("ending.json");
    if files::exists(&ending) {
        if files::read(&ending)?["reason"].as_str() != Some(reason) {
            return refuse("A different ending is already recorded");
        }
    } else {
        files::write(&ending, &json!({"reason": reason, "at": files::now()}))?;
    }
    if files::exists(&folder.join("record")) {
        return records::verify(&folder.join("record"), None);
    }
    let mut limitations = Vec::new();
    if let (Some(session), true) = (bound_session(folder, &state)?, folder.join("sittings").is_dir()) {
        let closeout = json!({"hook_event_name": "closeout"});
        if let Err(error) = capture::observe(&folder.join("telemetry"), &state, &session, &closeout) {
            limitations.push(format!("Final parent transcript observation: {error}"));
        }
    }
    records::seal(folder, &state, limitations)
}
