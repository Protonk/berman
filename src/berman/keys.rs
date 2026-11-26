//! The library key: 32 random bytes, generated once and kept where the login's unattended commands can read it.
//!
//! The login keychain on macOS and a Secret Service on Linux are reached through the system's own
//! tools, so the item belongs to them and stays readable after Berman is upgraded. A key file is
//! a user-owned, mode-0600 file beside the library.

use std::fs::{self, OpenOptions};
use std::io::Write;
use std::os::unix::fs::{MetadataExt, OpenOptionsExt};
use std::process::{Command, Stdio};

use crate::error::{At, Error, Result, refuse};
use crate::files;
use crate::places::Places;

const SERVICE: &str = "berman-library";
const SECURITY: &str = "/usr/bin/security";

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Store {
    File,
    Keychain,
    SecretService,
}

pub const NAMES: [&str; 3] = ["file", "keychain", "secret-service"];

impl Store {
    pub fn named(name: &str) -> Result<Store> {
        match name {
            "file" => Ok(Store::File),
            "keychain" => Ok(Store::Keychain),
            "secret-service" => Ok(Store::SecretService),
            _ => refuse(format!("Unknown key store: {name}")),
        }
    }

    pub fn name(self) -> &'static str {
        match self {
            Store::File => "file",
            Store::Keychain => "keychain",
            Store::SecretService => "secret-service",
        }
    }

    /// The system store for this platform.
    pub fn system() -> Store {
        if cfg!(target_os = "macos") { Store::Keychain } else { Store::SecretService }
    }
}

fn is_key(text: &str) -> bool {
    files::is_sha256(text)
}

/// Run a system tool, giving it text on standard input and returning what it prints.
fn tool(program: &str, arguments: &[&str], input: &str) -> Result<String> {
    let missing = |_| Error::Refused(format!("{program} is not available in this login"));
    let mut child = Command::new(program)
        .args(arguments)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(missing)?;
    if let Some(mut stdin) = child.stdin.take() {
        // A tool that needs no input may close the pipe first.
        let _ = stdin.write_all(input.as_bytes());
    }
    let output = child.wait_with_output()?;
    if !output.status.success() {
        let said = String::from_utf8_lossy(&output.stderr);
        let said = said.lines().map(str::trim).rfind(|line| !line.is_empty()).unwrap_or("no reason given");
        return refuse(format!("{program} {}: {said}", arguments.first().copied().unwrap_or_default()));
    }
    Ok(String::from_utf8_lossy(&output.stdout).trim().to_string())
}

fn save(store: Store, places: &Places, library: &str, key: &str) -> Result<()> {
    match store {
        Store::File => {
            let path = places.key_file();
            let mut file = OpenOptions::new().write(true).create_new(true).mode(0o600).open(&path).at(&path)?;
            file.write_all(format!("{key}\n").as_bytes()).at(&path)?;
            file.sync_all().at(&path)
        }
        // Interactive mode takes the command from standard input, which keeps the key out of the process list.
        Store::Keychain => {
            let command =
                format!("add-generic-password -a {library} -s {SERVICE} -l \"Berman library key\" -w {key}\n");
            tool(SECURITY, &["-i"], &command).map(drop)
        }
        Store::SecretService => {
            let arguments = ["store", "--label=Berman library key", "service", SERVICE, "account", library];
            tool("secret-tool", &arguments, key).map(drop)
        }
    }
}

/// Read the key, or say what is missing. Nothing here creates or replaces a key.
pub fn fetch(store: Store, places: &Places, library: &str) -> Result<String> {
    let found = match store {
        Store::File => {
            let path = places.key_file();
            let unchanged = "The library is unchanged; restore the key file from your backup.";
            let metadata = match path.symlink_metadata() {
                Ok(metadata) => metadata,
                Err(_) => return refuse(format!("The library key file is missing: {}. {unchanged}", path.display())),
            };
            // SAFETY: getuid has no preconditions.
            let owner = unsafe { libc::getuid() };
            if !metadata.is_file() || metadata.uid() != owner || metadata.mode() & 0o077 != 0 {
                return refuse(format!(
                    "The library key file must be a regular file that only its owner can read: {}",
                    path.display()
                ));
            }
            fs::read_to_string(&path).at(&path)?.trim().to_string()
        }
        Store::Keychain => tool(SECURITY, &["find-generic-password", "-a", library, "-s", SERVICE, "-w"], "")
            .map_err(|error| unavailable("the login keychain", &error))?,
        Store::SecretService => tool("secret-tool", &["lookup", "service", SERVICE, "account", library], "")
            .map_err(|error| unavailable("the Secret Service", &error))?,
    };
    if !is_key(&found) {
        let place = match store {
            Store::File => "the key file",
            Store::Keychain => "the login keychain",
            Store::SecretService => "the Secret Service",
        };
        return refuse(format!(
            "No library key was found in {place}. The library is unchanged; unlock that store or restore the key."
        ));
    }
    Ok(found)
}

fn unavailable(place: &str, error: &Error) -> Error {
    Error::Refused(format!(
        "The library key could not be read from {place} ({error}). The library is unchanged; \
         unlock that store or restore the key."
    ))
}

/// Generate a key, keep it in the chosen store, and read it back before any library depends on it.
pub fn create(store: Store, places: &Places, library: &str) -> Result<String> {
    let key = files::hex(&files::random(32));
    save(store, places, library, &key)?;
    if fetch(store, places, library)? != key {
        return refuse("The key store returned a different key from the one just kept");
    }
    Ok(key)
}

/// Remove a key from its store. Used when setup fails after the key was kept.
pub fn discard(store: Store, places: &Places, library: &str) {
    let _ = match store {
        Store::File => fs::remove_file(places.key_file()).map_err(Error::from),
        Store::Keychain => tool(SECURITY, &["delete-generic-password", "-a", library, "-s", SERVICE], "").map(drop),
        Store::SecretService => tool("secret-tool", &["clear", "service", SERVICE, "account", library], "").map(drop),
    };
}
