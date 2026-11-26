//! Disposable homes, an independent Git checkout, a library with a key file, and scripted actors for each test.
#![allow(dead_code)]

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::process::{Command, Output, Stdio};

use serde_json::Value;

pub const ACTORS: [&str; 2] = ["claude", "codex"];

pub struct World {
    _temporary: tempfile::TempDir,
    pub base: PathBuf,
    pub project: PathBuf,
    pub job: PathBuf,
    pub env: BTreeMap<String, String>,
}

pub fn berman() -> PathBuf {
    PathBuf::from(env!("CARGO_BIN_EXE_berman"))
}

/// The scripted actor, built once for each test program. Cargo builds examples only for a full `cargo test`.
fn actor() -> PathBuf {
    static BUILT: std::sync::Once = std::sync::Once::new();
    let target = berman().parent().expect("a target directory").to_path_buf();
    BUILT.call_once(|| {
        let mut build = Command::new(env!("CARGO"));
        build.args(["build", "--quiet", "--example", "scripted-actor"]).current_dir(env!("CARGO_MANIFEST_DIR"));
        if target.file_name().is_some_and(|profile| profile == "release") {
            build.arg("--release");
        }
        assert!(build.status().expect("cargo runs").success(), "the scripted actor builds");
    });
    target.join("examples/scripted-actor")
}

pub fn text(path: &Path) -> String {
    path.to_str().expect("a UTF-8 path").to_string()
}

pub fn git(project: &Path, arguments: &[&str]) -> String {
    let output = Command::new("git")
        .args(["-c", "user.name=Someone", "-c", "user.email=someone@example.invalid", "-C"])
        .arg(project)
        .args(arguments)
        .output()
        .expect("git runs");
    assert!(output.status.success(), "git {arguments:?}: {}", String::from_utf8_lossy(&output.stderr));
    String::from_utf8_lossy(&output.stdout).trim().to_string()
}

/// The names in a directory its owner has made unlistable, read by allowing listing for a moment.
pub fn listed(directory: &Path) -> Vec<String> {
    use std::os::unix::fs::PermissionsExt;
    let mode = std::fs::metadata(directory).expect("the directory").permissions();
    std::fs::set_permissions(directory, std::fs::Permissions::from_mode(0o700)).expect("permission to list");
    let names = std::fs::read_dir(directory)
        .expect("a listing")
        .flatten()
        .map(|e| e.file_name().to_string_lossy().into_owned());
    let names: Vec<String> = names.collect();
    std::fs::set_permissions(directory, mode).expect("permissions restored");
    names
}

impl Drop for World {
    /// Let the temporary directory be removed: the parents of return directories and harness homes are unlistable.
    fn drop(&mut self) {
        use std::os::unix::fs::PermissionsExt;
        let working = if cfg!(target_os = "macos") {
            self.data().join("working.noindex")
        } else {
            self.base.join(".local/state/berman")
        };
        for name in ["returns", "homes"] {
            let _ = std::fs::set_permissions(working.join(name), std::fs::Permissions::from_mode(0o700));
        }
    }
}

impl World {
    /// A world whose library does not exist yet.
    pub fn bare() -> World {
        let temporary = tempfile::tempdir().expect("a temporary directory");
        let base = temporary.path().canonicalize().expect("a real path");
        let project = base.join("normal checkout");
        std::fs::create_dir(&project).expect("a checkout");
        git(&project, &["init", "-q"]);
        let job = base.join("job.md");
        std::fs::write(&job, "Record a decision when one arises.\n").expect("a job");
        let binary = base.join("bin");
        std::fs::create_dir(&binary).expect("a bin directory");
        for name in ACTORS {
            // The actor takes its role from the name it is run under.
            std::os::unix::fs::symlink(actor(), binary.join(name)).expect("a link to the scripted actor");
        }
        let mut env = BTreeMap::new();
        let path = std::env::var("PATH").expect("PATH");
        env.insert("PATH".into(), format!("{}:{path}", binary.display()));
        env.insert("HOME".into(), text(&base));
        env.insert("CODEX_HOME".into(), text(&base.join(".codex")));
        env.insert("CLAUDE_CONFIG_DIR".into(), text(&base.join(".claude")));
        env.insert("FIXTURE_LOG".into(), text(&base.join("launches.jsonl")));
        World { _temporary: temporary, base, project, job, env }
    }

    /// A world with a library whose key is in a file, and reviewed Codex hooks.
    pub fn new() -> World {
        let world = World::bare();
        world.cli(&["library", "init", "--key", "file"]);
        let codex = world.base.join(".codex");
        std::fs::create_dir(&codex).expect("a Codex home");
        let hooks = world.run(&["codex-hooks"]);
        std::fs::write(codex.join("hooks.json"), hooks.stdout).expect("hooks");
        world
    }

    pub fn command(&self, program: &Path) -> Command {
        let mut command = Command::new(program);
        command.current_dir(&self.base).env_clear().envs(&self.env);
        command
    }

    pub fn run(&self, arguments: &[&str]) -> Output {
        self.command(&berman()).args(arguments).stdin(Stdio::null()).output().expect("berman runs")
    }

    /// Run a command that must succeed and print JSON.
    pub fn cli(&self, arguments: &[&str]) -> Value {
        self.cli_code(arguments, 0)
    }

    pub fn cli_code(&self, arguments: &[&str], code: i32) -> Value {
        let output = self.run(arguments);
        let said = String::from_utf8_lossy(&output.stderr);
        assert_eq!(output.status.code(), Some(code), "{arguments:?}: {said}");
        serde_json::from_slice(&output.stdout).unwrap_or_else(|_| panic!("{arguments:?} printed no JSON: {said}"))
    }

    /// Run a command that must fail, saying the given words.
    pub fn refuses(&self, arguments: &[&str], words: &str) {
        let output = self.run(arguments);
        let said = String::from_utf8_lossy(&output.stderr);
        assert!(!output.status.success(), "{arguments:?} succeeded");
        assert!(said.contains(words), "{arguments:?} said {said:?}, not {words:?}");
    }

    /// The exact bytes of a library file.
    pub fn cat(&self, path: &str) -> Vec<u8> {
        let output = self.run(&["library", "cat", path]);
        assert!(output.status.success(), "cat {path}: {}", String::from_utf8_lossy(&output.stderr));
        output.stdout
    }

    pub fn read(&self, path: &str) -> String {
        String::from_utf8(self.cat(path)).expect("UTF-8 text")
    }

    pub fn json(&self, path: &str) -> Value {
        serde_json::from_slice(&self.cat(path)).expect("JSON")
    }

    pub fn exists(&self, path: &str) -> bool {
        self.run(&["library", "ls", path]).status.success()
    }

    /// The names in a library directory. Directories end in a slash.
    pub fn ls(&self, path: &str) -> Vec<String> {
        let output = self.run(&["library", "ls", path]);
        assert!(output.status.success(), "ls {path}: {}", String::from_utf8_lossy(&output.stderr));
        String::from_utf8_lossy(&output.stdout).lines().map(str::to_string).collect()
    }

    /// Every file below a library directory, by relative path.
    pub fn files(&self, path: &str) -> Vec<String> {
        let mut found = Vec::new();
        let mut pending = vec![String::new()];
        while let Some(prefix) = pending.pop() {
            for name in self.ls(&format!("{path}/{prefix}")) {
                match name.strip_suffix('/') {
                    Some(directory) => pending.push(format!("{prefix}{directory}/")),
                    None => found.push(format!("{prefix}{name}")),
                }
            }
        }
        found.sort();
        found
    }

    pub fn open(&self, episode: &str, actor: &str, jobs: &[&Path]) -> Value {
        self.cli(&self.opening(episode, actor, jobs).iter().map(String::as_str).collect::<Vec<_>>())
    }

    pub fn opening(&self, episode: &str, actor: &str, jobs: &[&Path]) -> Vec<String> {
        let mut arguments: Vec<String> =
            ["open", episode, "--actor", actor, "--project"].into_iter().map(String::from).collect();
        arguments.push(text(&self.project));
        for job in jobs {
            arguments.extend(["--job".to_string(), text(job)]);
        }
        arguments
    }

    /// Send a message for an unattended sitting: the opening when no request is named.
    pub fn send(&self, episode: &str, request: Option<&str>, prompt: &str) -> Value {
        self.cli(&self.sending(episode, request, prompt))
    }

    pub fn sending<'a>(&self, episode: &'a str, request: Option<&'a str>, prompt: &'a str) -> Vec<&'a str> {
        match request {
            None => vec!["start", episode, "--unattended", "--prompt", prompt],
            Some(request) => vec!["resume", episode, "--request", request, "--unattended", "--prompt", prompt],
        }
    }

    pub fn launches(&self) -> Vec<Value> {
        let log = std::fs::read_to_string(&self.env["FIXTURE_LOG"]).unwrap_or_default();
        log.lines().map(|line| serde_json::from_str(line).expect("JSON")).collect()
    }

    /// Run the callback command directly, as a harness would, for an episode or for none.
    pub fn callback(&self, actor: &str, episode: Option<&str>, input: &str) -> Output {
        use std::io::Write;
        let mut command = self.command(&berman());
        command.args(["capture", actor]).stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::piped());
        if let Some(episode) = episode {
            command.env("BERMAN_EPISODE", episode);
        }
        let mut child = command.spawn().expect("the callback starts");
        // Without an episode the callback returns before it reads its input.
        let _ = child.stdin.take().expect("a pipe").write_all(input.as_bytes());
        child.wait_with_output().expect("the callback ends")
    }

    /// Where this login's library keeps its files.
    pub fn data(&self) -> PathBuf {
        if cfg!(target_os = "macos") {
            self.base.join("Library/Application Support/Berman")
        } else {
            self.base.join(".local/share/berman")
        }
    }

    /// The names the reading guide documents as members of a record.
    pub fn assert_documented(&self, record: &str) {
        let guide = Path::new(env!("CARGO_MANIFEST_DIR")).join("docs/records.md");
        let guide = std::fs::read_to_string(guide).expect("the reading guide");
        let mut documented = Vec::new();
        for row in guide.lines().filter(|line| line.starts_with("| `")) {
            let cell = row.split('|').nth(1).unwrap_or_default();
            documented.extend(cell.split('`').skip(1).step_by(2).map(str::to_string));
        }
        for name in self.files(record) {
            let listed =
                documented.iter().any(|known| *known == name || (known.ends_with('/') && name.starts_with(known)));
            assert!(listed, "{name} is not in the reading guide");
        }
    }
}
