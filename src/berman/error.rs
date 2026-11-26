//! One error for refusals and failed operations. A missing file is kept apart because capture treats it differently.

use std::fmt;
use std::io;
use std::path::Path;

#[derive(Debug)]
pub enum Error {
    Missing(String),
    Refused(String),
}

pub type Result<T> = std::result::Result<T, Error>;

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Error::Missing(text) | Error::Refused(text) => f.write_str(text),
        }
    }
}

impl From<io::Error> for Error {
    fn from(error: io::Error) -> Self {
        match error.kind() {
            io::ErrorKind::NotFound => Error::Missing(error.to_string()),
            _ => Error::Refused(error.to_string()),
        }
    }
}

impl From<serde_json::Error> for Error {
    fn from(error: serde_json::Error) -> Self {
        Error::Refused(format!("Invalid JSON: {error}"))
    }
}

impl From<rusqlite::Error> for Error {
    fn from(error: rusqlite::Error) -> Self {
        match error.sqlite_error_code() {
            Some(rusqlite::ErrorCode::NotADatabase) => {
                Error::Refused("The library key does not open the library. The library is unchanged.".into())
            }
            Some(rusqlite::ErrorCode::DatabaseBusy | rusqlite::ErrorCode::DatabaseLocked) => {
                Error::Refused("The library is busy with another Berman command".into())
            }
            _ => Error::Refused(format!("Library storage: {error}")),
        }
    }
}

pub fn refuse<T>(text: impl Into<String>) -> Result<T> {
    Err(Error::Refused(text.into()))
}

/// Name the path an operating-system error concerns.
pub trait At<T> {
    fn at(self, path: &Path) -> Result<T>;
}

impl<T> At<T> for io::Result<T> {
    fn at(self, path: &Path) -> Result<T> {
        self.map_err(|error| {
            let text = format!("{error}: {}", path.display());
            match error.kind() {
                io::ErrorKind::NotFound => Error::Missing(text),
                _ => Error::Refused(text),
            }
        })
    }
}
