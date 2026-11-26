//! Where Berman keeps the producer's library and the working directories of open episodes.
//!
//! The library is one encrypted database in the login's application data. Return directories
//! and dedicated harness homes are real directories under parents the login can enter but not
//! list, so an ordinary recursive walk does not enumerate them.

use std::fs;
use std::os::unix::fs::{DirBuilderExt, PermissionsExt};
use std::path::{Path, PathBuf};

use crate::error::{At, Result, refuse};
use crate::files;

#[derive(Clone, Debug)]
pub struct Places {
    /// The library database, its description, any key file, and the episode locks.
    pub data: PathBuf,
    /// Parent of the return directory of each open episode.
    pub returns: PathBuf,
    /// Parent of each dedicated harness home.
    pub homes: PathBuf,
}

fn home() -> Result<PathBuf> {
    match std::env::var_os("HOME").filter(|home| !home.is_empty()) {
        Some(home) => Ok(files::resolve(Path::new(&home))),
        None => refuse("HOME names no directory for the library"),
    }
}

fn base(variable: &str, fallback: &str) -> Result<PathBuf> {
    match std::env::var_os(variable).map(PathBuf::from).filter(|path| path.is_absolute()) {
        Some(path) => Ok(files::resolve(&path)),
        None => Ok(home()?.join(fallback)),
    }
}

impl Places {
    /// The places for this login. BERMAN_LIBRARY names another data directory.
    pub fn find() -> Result<Places> {
        let chosen = std::env::var_os("BERMAN_LIBRARY").map(PathBuf::from).filter(|path| path.is_absolute());
        if cfg!(target_os = "macos") {
            let data = match chosen {
                Some(path) => files::resolve(&path),
                None => home()?.join("Library/Application Support/Berman"),
            };
            // Spotlight skips a directory whose name ends in .noindex.
            let working = data.join("working.noindex");
            Ok(Places { returns: working.join("returns"), homes: working.join("homes"), data })
        } else {
            let (data, working) = match chosen {
                Some(path) => (files::resolve(&path), files::resolve(&path).join("working")),
                None => (
                    base("XDG_DATA_HOME", ".local/share")?.join("berman"),
                    base("XDG_STATE_HOME", ".local/state")?.join("berman"),
                ),
            };
            Ok(Places { returns: working.join("returns"), homes: working.join("homes"), data })
        }
    }

    pub fn database(&self) -> PathBuf {
        self.data.join("library.db")
    }

    pub fn description(&self) -> PathBuf {
        self.data.join("library.json")
    }

    pub fn key_file(&self) -> PathBuf {
        self.data.join("library.key")
    }

    pub fn lock(&self, episode: &str, kind: &str) -> PathBuf {
        self.data.join("locks").join(format!("{episode}.{kind}"))
    }

    /// Settings passed to each Claude Code sitting. They name the callback command and no episode.
    pub fn claude_settings(&self) -> PathBuf {
        self.data.join("claude-hooks.json")
    }
}

/// A private directory for the owning login alone.
pub fn private(path: &Path) -> Result<()> {
    fs::DirBuilder::new().recursive(true).mode(0o700).create(path).at(path)?;
    files::safe_directory(path)
}

/// A parent the owning login can enter and write in but not list.
pub fn unlisted(path: &Path) -> Result<()> {
    if let Some(parent) = path.parent() {
        private(parent)?;
    }
    if !files::exists(path) {
        fs::DirBuilder::new().mode(0o700).create(path).at(path)?;
    }
    files::safe_directory(path)?;
    fs::set_permissions(path, fs::Permissions::from_mode(0o300)).at(path)
}
