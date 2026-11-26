//! Shared file, digest, lock, identifier and time operations.

use std::ffi::OsString;
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::os::fd::AsRawFd;
use std::os::unix::fs::{DirBuilderExt, MetadataExt, OpenOptionsExt};
use std::path::{Component, Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::Value;
use sha2::{Digest, Sha256};

use crate::error::{At, Error, Result, refuse};
use crate::json;

pub const FILE_LIMIT: u64 = 256 * 1024 * 1024;

/// The current time in UTC to the microsecond, ending in Z.
pub fn now() -> String {
    let elapsed = SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default();
    let (seconds, micros) = (elapsed.as_secs() as i64, elapsed.subsec_micros());
    let (days, rest) = (seconds.div_euclid(86400), seconds.rem_euclid(86400));
    // Civil date from a day count, after Howard Hinnant's days_from_civil inverse.
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = doy - (153 * mp + 2) / 5 + 1;
    let month = if mp < 10 { mp + 3 } else { mp - 9 };
    let year = yoe + era * 400 + i64::from(month <= 2);
    format!("{year:04}-{month:02}-{day:02}T{:02}:{:02}:{:02}.{micros:06}Z", rest / 3600, rest % 3600 / 60, rest % 60)
}

pub fn hex(bytes: &[u8]) -> String {
    const DIGITS: &[u8; 16] = b"0123456789abcdef";
    let mut out = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        out.push(DIGITS[(byte >> 4) as usize] as char);
        out.push(DIGITS[(byte & 15) as usize] as char);
    }
    out
}

pub fn digest(data: &[u8]) -> String {
    hex(&Sha256::digest(data))
}

/// The SHA-256 of a stream, read in bounded pieces.
pub fn digest_reader(mut stream: impl Read) -> std::io::Result<String> {
    let mut hasher = Sha256::new();
    let mut buffer = vec![0u8; 1 << 16];
    loop {
        let count = stream.read(&mut buffer)?;
        if count == 0 {
            return Ok(hex(&hasher.finalize()));
        }
        hasher.update(&buffer[..count]);
    }
}

pub fn random(count: usize) -> Vec<u8> {
    let mut bytes = vec![0u8; count];
    getrandom::fill(&mut bytes).expect("the operating system supplies random bytes");
    bytes
}

/// A random version 4 UUID, without its hyphens.
pub fn uuid_hex() -> String {
    let mut bytes = random(16);
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    hex(&bytes)
}

pub fn uuid() -> String {
    let h = uuid_hex();
    format!("{}-{}-{}-{}-{}", &h[..8], &h[8..12], &h[12..16], &h[16..20], &h[20..])
}

/// Whether text is a UUID in its canonical lowercase spelling.
pub fn is_uuid(text: &str) -> bool {
    text.len() == 36
        && text.bytes().enumerate().all(|(i, b)| match i {
            8 | 13 | 18 | 23 => b == b'-',
            _ => b.is_ascii_digit() || (b'a'..=b'f').contains(&b),
        })
}

pub fn is_sha256(text: &str) -> bool {
    text.len() == 64 && text.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

fn is_symlink(path: &Path) -> bool {
    path.symlink_metadata().is_ok_and(|m| m.file_type().is_symlink())
}

fn symlink_in(path: &Path) -> bool {
    path.ancestors().any(is_symlink)
}

/// An absolute path with `~`, `.` and `..` removed and existing links resolved. The path need not exist.
pub fn resolve(path: &Path) -> PathBuf {
    let mut absolute = if path.is_absolute() {
        PathBuf::new()
    } else {
        std::env::current_dir().unwrap_or_else(|_| PathBuf::from("/"))
    };
    for part in path.components() {
        match part {
            Component::ParentDir => {
                absolute.pop();
            }
            Component::CurDir => {}
            other => absolute.push(other),
        }
    }
    let mut missing: Vec<OsString> = Vec::new();
    let mut existing = absolute.clone();
    loop {
        if let Ok(real) = existing.canonicalize() {
            return missing.iter().rev().fold(real, |path, name| path.join(name));
        }
        match existing.file_name() {
            Some(name) => missing.push(name.to_os_string()),
            None => return absolute,
        }
        existing.pop();
    }
}

/// A path named on the command line: `~` expanded, no final symbolic link, then resolved.
pub fn physical(text: &str) -> Result<PathBuf> {
    let expanded = match (text.strip_prefix("~/"), std::env::var_os("HOME")) {
        (Some(rest), Some(home)) => PathBuf::from(home).join(rest),
        _ if text == "~" => PathBuf::from(std::env::var_os("HOME").unwrap_or_else(|| "~".into())),
        _ => PathBuf::from(text),
    };
    if is_symlink(&expanded) {
        return refuse(format!("Use the physical path: {}", expanded.display()));
    }
    Ok(resolve(&expanded))
}

pub fn exists(path: &Path) -> bool {
    path.symlink_metadata().is_ok()
}

pub fn regular(path: &Path) -> Result<()> {
    if !path.symlink_metadata().at(path)?.file_type().is_file() {
        return refuse(format!("Expected a regular file: {}", path.display()));
    }
    if symlink_in(path) {
        return refuse(format!("Symlink in path: {}", path.display()));
    }
    Ok(())
}

pub fn read_regular(path: &Path) -> Result<Vec<u8>> {
    regular(path)?;
    fs::read(path).at(path)
}

/// What must stay the same about a file while it is read.
pub fn identity(metadata: &fs::Metadata) -> (u64, u64, i64, i64) {
    (metadata.ino(), metadata.len(), metadata.mtime(), metadata.mtime_nsec())
}

/// Read a regular file whole, refusing one that changes during the read.
pub fn stable_bytes(path: &Path, limit: u64) -> Result<Vec<u8>> {
    regular(path)?;
    let before = fs::metadata(path).at(path)?;
    if before.len() > limit {
        return refuse(format!("File exceeds {limit} bytes: {}", path.display()));
    }
    let mut raw = Vec::with_capacity(before.len() as usize + 1);
    File::open(path).at(path)?.take(before.len() + 1).read_to_end(&mut raw).at(path)?;
    let after = fs::metadata(path).at(path)?;
    if identity(&before) != identity(&after) || raw.len() as u64 != after.len() {
        return refuse(format!("Source changed during observation: {}", path.display()));
    }
    Ok(raw)
}

/// Replace a file with complete, synced bytes.
pub fn atomic(path: &Path, data: &[u8]) -> Result<()> {
    if symlink_in(path) {
        return refuse(format!("Symlink in destination: {}", path.display()));
    }
    let parent = path.parent().unwrap_or(Path::new("/"));
    let temporary = parent.join(format!(".write-{}", uuid_hex()));
    let written = (|| {
        let mut stream = OpenOptions::new().write(true).create_new(true).mode(0o600).open(&temporary)?;
        stream.write_all(data)?;
        stream.sync_all()?;
        fs::rename(&temporary, path)
    })();
    if written.is_err() {
        let _ = fs::remove_file(&temporary);
    }
    written.at(path)
}

pub fn read(path: &Path) -> Result<Value> {
    Ok(serde_json::from_slice(&read_regular(path)?)?)
}

pub fn write(path: &Path, value: &Value) -> Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).at(parent)?;
    }
    atomic(path, &json::bytes(value))
}

/// Create a deterministic artifact, or verify an interrupted operation's copy.
pub fn keep(path: &Path, data: &[u8]) -> Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).at(parent)?;
    }
    if exists(path) {
        if read_regular(path)? != data {
            return refuse(format!("Existing artifact differs; retained for inspection: {}", path.display()));
        }
        return Ok(());
    }
    atomic(path, data)
}

pub fn safe_directory(path: &Path) -> Result<()> {
    if !path.is_dir() || symlink_in(path) {
        return refuse(format!("Expected a directory without symlinks: {}", path.display()));
    }
    Ok(())
}

fn flock(file: &File, blocking: bool) -> Result<()> {
    let operation = libc::LOCK_EX | if blocking { 0 } else { libc::LOCK_NB };
    loop {
        // SAFETY: the descriptor belongs to an open file for the duration of the call.
        if unsafe { libc::flock(file.as_raw_fd(), operation) } == 0 {
            return Ok(());
        }
        let error = std::io::Error::last_os_error();
        match error.kind() {
            std::io::ErrorKind::Interrupted => continue,
            std::io::ErrorKind::WouldBlock => return refuse("A sitting or close is in progress"),
            _ => return Err(Error::from(error)),
        }
    }
}

/// Hold an exclusive lock on an existing lock file until the returned file is dropped.
pub fn lock(path: &Path, blocking: bool) -> Result<File> {
    regular(path)?;
    let file = File::open(path).at(path)?;
    flock(&file, blocking)?;
    Ok(file)
}

pub fn create_private_directory(path: &Path) -> Result<()> {
    fs::DirBuilder::new().recursive(true).mode(0o700).create(path).at(path)
}

/// Publish a complete new directory. Concurrent publishers serialize the rename.
pub fn new_directory<T>(destination: &Path, fill: impl FnOnce(&Path) -> Result<T>) -> Result<T> {
    let occupied = || refuse(format!("Destination already exists: {}", destination.display()));
    if exists(destination) {
        return occupied();
    }
    let parent = destination.parent().unwrap_or(Path::new("/"));
    fs::create_dir_all(parent).at(parent)?;
    safe_directory(parent)?;
    // The staging directory is named for its destination and is not hidden, so one left by an interrupted
    // command can be seen and removed.
    let name = destination.file_name().unwrap_or_default().to_string_lossy();
    let temporary = parent.join(format!("{name}.berman-partial-{}", uuid_hex()));
    let tree = temporary.join("new");
    create_private_directory(&tree)?;
    let result = (|| {
        let value = fill(&tree)?;
        let directory = File::open(parent).at(parent)?;
        flock(&directory, true)?;
        if exists(destination) {
            return occupied();
        }
        fs::rename(&tree, destination).at(destination)?;
        directory.sync_all().at(parent)?;
        Ok(value)
    })();
    let _ = fs::remove_dir_all(&temporary);
    result
}

/// The first executable file of that name on PATH.
pub fn which(name: &str) -> Option<PathBuf> {
    use std::os::unix::fs::PermissionsExt;
    std::env::split_paths(&std::env::var_os("PATH")?)
        .map(|directory| directory.join(name))
        .find(|path| path.metadata().is_ok_and(|m| m.is_file() && m.permissions().mode() & 0o111 != 0))
}

/// Quote arguments into one line a POSIX shell splits back into them.
pub fn shell_join(arguments: &[String]) -> String {
    let safe = |c: char| c.is_ascii_alphanumeric() || "@%+=:,./-_".contains(c);
    let quoted: Vec<String> =
        arguments
            .iter()
            .map(|a| {
                if !a.is_empty() && a.chars().all(safe) {
                    a.clone()
                } else {
                    format!("'{}'", a.replace('\'', "'\"'\"'"))
                }
            })
            .collect();
    quoted.join(" ")
}

/// A path as text. Berman records paths as UTF-8.
pub fn text(path: &Path) -> String {
    path.to_string_lossy().into_owned()
}
