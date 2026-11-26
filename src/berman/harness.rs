//! The actor CLIs Berman can launch, and what each adapter must answer.

use std::path::{Path, PathBuf};

use serde_json::Value;

use crate::error::{Result, refuse};
use crate::{claude, codex};

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Harness {
    Codex,
    Claude,
}

pub const NAMES: [&str; 2] = ["codex", "claude"];

/// What a launched sitting passes to the actor's CLI.
pub struct Launch<'a> {
    pub return_directory: &'a Path,
    pub settings: &'a Path,
    pub session: Option<&'a str>,
    pub resuming: bool,
    pub attended: bool,
}

/// What an unattended sitting's native output reports.
pub struct Outcome {
    pub session: Option<String>,
    pub response: Option<String>,
    pub denials: Value,
    pub limitations: Vec<String>,
}

/// A reading copy derived from complete transcript lines, with what its checks need.
#[derive(Default)]
pub struct Reading {
    pub text: Vec<u8>,
    /// SHA-256 of each assistant message's visible text.
    pub assistant: Vec<String>,
    pub malformed: Vec<u64>,
    pub identities: Vec<String>,
}

impl Harness {
    pub fn named(name: &str) -> Result<Harness> {
        match name {
            "codex" => Ok(Harness::Codex),
            "claude" => Ok(Harness::Claude),
            _ => refuse(format!("Unknown actor: {name}")),
        }
    }

    pub fn name(self) -> &'static str {
        match self {
            Harness::Codex => "codex",
            Harness::Claude => "claude",
        }
    }

    pub fn home_variable(self) -> &'static str {
        match self {
            Harness::Codex => codex::HOME_VARIABLE,
            Harness::Claude => claude::HOME_VARIABLE,
        }
    }

    pub fn default_home(self) -> &'static str {
        match self {
            Harness::Codex => codex::DEFAULT_HOME,
            Harness::Claude => claude::DEFAULT_HOME,
        }
    }

    /// Directories of the harness home that hold native transcripts.
    pub fn stores(self) -> &'static [&'static str] {
        match self {
            Harness::Codex => codex::STORES,
            Harness::Claude => claude::STORES,
        }
    }

    /// Callback events and the seconds the harness allows each.
    pub fn events(self) -> &'static [(&'static str, u64)] {
        match self {
            Harness::Codex => codex::EVENTS,
            Harness::Claude => claude::EVENTS,
        }
    }

    /// The native session Berman assigns before the first sitting, if the harness lets it.
    pub fn assigned_session(self, state: &Value) -> Option<String> {
        match self {
            Harness::Codex => None,
            Harness::Claude => state["id"].as_str().map(str::to_string),
        }
    }

    pub fn binds(self, state: &Value, event: &Value, first: Option<&str>) -> bool {
        match self {
            Harness::Codex => codex::binds(state, event, first),
            Harness::Claude => event["session_id"] == state["id"],
        }
    }

    pub fn arguments(self, launch: &Launch) -> Result<Vec<String>> {
        match self {
            Harness::Codex => codex::arguments(launch),
            Harness::Claude => claude::arguments(launch),
        }
    }

    pub fn outcome(self, output: &[u8]) -> Outcome {
        match self {
            Harness::Codex => codex::outcome(output),
            Harness::Claude => claude::outcome(output),
        }
    }

    pub fn candidates(self, home: &Path, session: &str) -> Vec<PathBuf> {
        match self {
            Harness::Codex => codex::candidates(home, session),
            Harness::Claude => claude::candidates(home, session),
        }
    }

    pub fn owns(self, path: &Path, session: &str) -> Result<bool> {
        match self {
            Harness::Codex => codex::owns(path, session),
            Harness::Claude => Ok(true),
        }
    }

    /// Check that complete transcript lines belong to the session and derive their reading copy.
    pub fn transcript(self, raw: &[u8], path: &Path, session: &str) -> Result<Reading> {
        match self {
            Harness::Codex => codex::transcript(raw, session),
            Harness::Claude => claude::transcript(raw, path, session),
        }
    }
}

/// Lines as the transcripts are numbered: ended by a line feed, a carriage return, or both.
pub fn lines(data: &[u8]) -> Vec<&[u8]> {
    let mut out = Vec::new();
    let mut start = 0;
    let mut i = 0;
    while i < data.len() {
        let byte = data[i];
        if byte == b'\n' || byte == b'\r' {
            out.push(&data[start..i]);
            if byte == b'\r' && data.get(i + 1) == Some(&b'\n') {
                i += 1;
            }
            start = i + 1;
        }
        i += 1;
    }
    if start < data.len() {
        out.push(&data[start..]);
    }
    out
}

pub fn blank(line: &[u8]) -> bool {
    line.iter().all(|b| matches!(b, b' ' | b'\t' | b'\n' | b'\r' | 0x0b | 0x0c))
}

/// One block of a reading copy: where it came from, then the content.
pub fn block(out: &mut Vec<u8>, number: usize, pointer: &str, heading: [String; 2], content: &Value) {
    let body = match content {
        Value::String(text) => text.clone(),
        other => crate::json::pretty(other),
    };
    let source = format!("SOURCE: transcript.jsonl#L{number} {pointer}");
    for part in ["=".repeat(72), source, heading[0].clone(), heading[1].clone(), body, String::new()] {
        out.extend_from_slice(part.as_bytes());
        out.push(b'\n');
    }
}
