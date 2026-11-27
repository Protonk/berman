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
    /// Lines too long to render, by number. Their bytes are in the raw transcript.
    pub unrendered: Vec<u64>,
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

    /// The lines that begin every reading copy for this harness.
    pub fn heading(self) -> Vec<u8> {
        let lines: &[&str] = match self {
            Harness::Codex => &codex::HEADING,
            Harness::Claude => &claude::HEADING,
        };
        lines.iter().flat_map(|line| line.bytes().chain(std::iter::once(b'\n'))).collect()
    }

    /// Add one complete transcript line, numbered from 1, to a reading copy.
    pub fn line(self, number: usize, raw: &[u8], reading: &mut Reading) {
        match self {
            Harness::Codex => codex::line(number, raw, reading),
            Harness::Claude => claude::line(number, raw, reading),
        }
    }

    /// Check a transcript's first line, where the harness names its session there.
    pub fn first_line(self, first: &[u8], session: &str) -> Result<()> {
        match self {
            Harness::Codex => codex::belongs(first, session),
            Harness::Claude => Ok(()),
        }
    }

    /// Check a transcript that has been read whole against the session.
    pub fn whole(self, reading: &Reading, path: &Path, session: &str) -> Result<()> {
        match self {
            Harness::Codex => Ok(()),
            Harness::Claude => claude::belongs(reading, path, session),
        }
    }

    /// The reading copy of complete transcript lines held in memory.
    pub fn render(self, data: &[u8]) -> Reading {
        let mut reading = Reading { text: self.heading(), ..Reading::default() };
        for (index, raw) in lines(data).into_iter().enumerate() {
            self.line(index + 1, raw, &mut reading);
        }
        reading
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
    let source = format!("SOURCE: transcript.jsonl#L{number} {pointer}");
    for part in ["=".repeat(72), source, heading[0].clone(), heading[1].clone()] {
        out.extend_from_slice(part.as_bytes());
        out.push(b'\n');
    }
    // Text goes in as it is, without a second copy of what can be megabytes.
    match content {
        Value::String(text) => out.extend_from_slice(text.as_bytes()),
        other => out.extend_from_slice(crate::json::pretty(other).as_bytes()),
    }
    out.extend_from_slice(b"\n\n");
}

/// A line longer than this stays in the raw transcript and is named in the reading copy without being rendered,
/// so the memory a capture needs does not grow with the longest line.
pub const LONG_LINE: usize = 8 * 1024 * 1024;

/// Note in a reading copy that a line was too long to render.
pub fn unrendered(reading: &mut Reading, number: usize, bytes: u64) {
    reading.unrendered.push(number as u64);
    let note = format!(
        "{}\nSOURCE: transcript.jsonl#L{number}\nNOT RENDERED: this line is {bytes} bytes long. Read it in transcript.jsonl.\n\n",
        "=".repeat(72)
    );
    reading.text.extend_from_slice(note.as_bytes());
}
