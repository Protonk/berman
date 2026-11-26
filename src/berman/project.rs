//! Observe the checkout at the boundaries of an episode.
//!
//! An observation shows the checkout at one time. Anyone may change it between observations.

use std::path::Path;
use std::process::{Command, Output};

use serde_json::{Value, json};

use crate::error::{Result, refuse};
use crate::files;

fn git(project: &Path, arguments: &[&str]) -> Result<Output> {
    Ok(Command::new("git").arg("-C").arg(project).args(arguments).env("GIT_OPTIONAL_LOCKS", "0").output()?)
}

fn failed(arguments: &[&str], output: &Output) -> String {
    format!("git {} failed: {}", arguments.join(" "), String::from_utf8_lossy(&output.stderr).trim())
}

/// The top of the Git checkout that contains a directory.
pub fn top(checkout: &Path) -> Result<std::path::PathBuf> {
    let arguments = ["rev-parse", "--show-toplevel"];
    let output = git(checkout, &arguments)?;
    if !output.status.success() {
        return refuse(failed(&arguments, &output));
    }
    Ok(files::resolve(Path::new(String::from_utf8_lossy(&output.stdout).trim())))
}

pub fn git_state(project: &Path) -> Result<Value> {
    let arguments = ["status", "--porcelain=v1", "--untracked-files=normal"];
    let status = git(project, &arguments)?;
    if !status.status.success() {
        return refuse(failed(&arguments, &status));
    }
    let arguments = ["rev-parse", "--verify", "--quiet", "HEAD"];
    let head = git(project, &arguments)?;
    if !matches!(head.status.code(), Some(0 | 1)) {
        return refuse(failed(&arguments, &head));
    }
    let head = String::from_utf8_lossy(&head.stdout).trim().to_string();
    Ok(json!({
        "observed_at": files::now(),
        "head": if head.is_empty() { Value::Null } else { Value::String(head) },
        "status": String::from_utf8_lossy(&status.stdout),
    }))
}
