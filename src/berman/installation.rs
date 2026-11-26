//! The build this executable came from, and the job templates it carries.

use std::fs::File;

use serde_json::{Map, Value, json};

use crate::error::{At, Result};
use crate::files;

pub const JOB_TEMPLATES: [(&str, &str); 3] = [
    ("job.md", include_str!("templates/job.md")),
    ("note-taking.md", include_str!("templates/note-taking.md")),
    ("handover.md", include_str!("templates/handover.md")),
];

/// Identify the installed build: its version, the sources it was built from, and the executable itself.
pub fn verify() -> Result<Value> {
    let executable = std::env::current_exe()?.canonicalize()?;
    let digest = files::digest_reader(File::open(&executable).at(&executable)?).at(&executable)?;
    let mut templates = Map::new();
    for (name, content) in JOB_TEMPLATES {
        templates.insert(format!("templates/{name}"), json!(files::digest(content.as_bytes())));
    }
    // Both systems embed one storage engine, so a library made on either opens on the other.
    let storage = rusqlite::Connection::open_in_memory()?;
    let cipher: String = storage.query_row("PRAGMA cipher_version", [], |row| row.get(0))?;
    let sqlite: String = storage.query_row("SELECT sqlite_version()", [], |row| row.get(0))?;
    Ok(json!({
        "version": env!("CARGO_PKG_VERSION"),
        "source_sha256": env!("BERMAN_SOURCE_SHA256"),
        "commit": option_env!("BERMAN_COMMIT").filter(|commit| !commit.is_empty()),
        "target": env!("BERMAN_TARGET"),
        "storage": format!("SQLCipher {cipher} on SQLite {sqlite}"),
        "executable": files::text(&executable),
        "executable_sha256": digest,
        "templates_sha256": templates,
        "verification": "The source digest and templates were fixed when this executable was built.",
    }))
}
