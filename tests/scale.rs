//! Measurements at realistic sizes: peak memory, database and journal high-water sizes, and how long a
//! callback, a close, a verification and an export take near the 256 MiB file and 1 GiB record limits.
//!
//! These print a table and are not part of the routine suite. Run them from a release build:
//!
//!     cargo test --release --test scale -- --ignored --nocapture --test-threads 1
//!
//! BERMAN_SCALE_MIB sets the largest transcript (default 250). BERMAN_SMALL_DISK names a directory on a
//! filesystem with about 100 MiB free for the disk-full measurement.

mod support;

use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::time::{Duration, Instant};

use serde_json::{Value, json};
use support::{World, berman, text};

const MIB: u64 = 1024 * 1024;

struct Measured {
    seconds: f64,
    memory: u64,
    journal: u64,
    database: u64,
    succeeded: bool,
}

fn size(path: &Path) -> u64 {
    std::fs::metadata(path).map(|metadata| metadata.len()).unwrap_or(0)
}

/// Run one Berman command, sampling the journal while it runs and reading its peak memory when it ends.
#[allow(clippy::zombie_processes, reason = "wait4 reaps the child and returns its resource usage")]
fn measure(world: &World, arguments: &[&str], episode: Option<&str>, input: &str) -> Measured {
    let data = world.data();
    let (stop, high) = (Arc::new(AtomicBool::new(false)), Arc::new(AtomicU64::new(0)));
    let sampler = {
        let (stop, high, journal) = (stop.clone(), high.clone(), data.join("library.db-wal"));
        std::thread::spawn(move || {
            while !stop.load(Ordering::Relaxed) {
                high.fetch_max(size(&journal), Ordering::Relaxed);
                std::thread::sleep(Duration::from_millis(2));
            }
        })
    };
    let mut command = world.command(&berman());
    command.args(arguments).stdin(Stdio::piped()).stdout(Stdio::null()).stderr(Stdio::piped());
    if let Some(episode) = episode {
        command.env("BERMAN_EPISODE", episode);
    }
    let started = Instant::now();
    let mut child = command.spawn().unwrap();
    let _ = child.stdin.take().unwrap().write_all(input.as_bytes());
    let (mut status, mut usage) = (0, unsafe { std::mem::zeroed::<libc::rusage>() });
    // SAFETY: the process is this test's child, and both out-parameters are valid for the call.
    assert_eq!(unsafe { libc::wait4(child.id() as i32, &mut status, 0, &mut usage) }, child.id() as i32);
    let seconds = started.elapsed().as_secs_f64();
    stop.store(true, Ordering::Relaxed);
    sampler.join().unwrap();
    // Linux reports the peak resident set in KiB, macOS in bytes.
    let memory = usage.ru_maxrss as u64 * if cfg!(target_os = "macos") { 1 } else { 1024 };
    Measured {
        seconds,
        memory,
        journal: high.load(Ordering::Relaxed),
        database: size(&data.join("library.db")),
        succeeded: libc::WIFEXITED(status) && libc::WEXITSTATUS(status) == 0,
    }
}

fn row(what: &str, measured: &Measured) {
    println!(
        "{what:<46} {:>7.2} s  {:>6.1} MiB peak memory  {:>7.1} MiB journal  {:>7.1} MiB database{}",
        measured.seconds,
        measured.memory as f64 / MIB as f64,
        measured.journal as f64 / MIB as f64,
        measured.database as f64 / MIB as f64,
        if measured.succeeded { "" } else { "  FAILED" },
    );
}

/// Cheap bytes that no two megabytes share.
struct Noise(u64);

impl Noise {
    fn text(&mut self, length: usize) -> String {
        let mut out = String::with_capacity(length + 16);
        while out.len() < length {
            self.0 ^= self.0 << 13;
            self.0 ^= self.0 >> 7;
            self.0 ^= self.0 << 17;
            out.push_str(&format!("{:016x} ", self.0));
        }
        out
    }
}

/// Grow a Claude Code transcript to a size with lines of a few kilobytes each.
fn grow(transcript: &Path, session: &str, noise: &mut Noise, target: u64) {
    let mut file = std::fs::OpenOptions::new().append(true).open(transcript).unwrap();
    let mut written = size(transcript);
    while written < target {
        let said = noise.text(3000);
        let line = format!("{}\n", json!({"sessionId": session, "message": {"role": "assistant", "content": said}}));
        file.write_all(line.as_bytes()).unwrap();
        written += line.len() as u64;
    }
}

struct Setup {
    world: World,
    id: String,
    transcript: PathBuf,
    returns: PathBuf,
}

fn setup(episode: &str) -> Setup {
    let world = World::new();
    let state = world.open(episode, "claude", &[]);
    let id = state["id"].as_str().unwrap().to_string();
    world.send(episode, None, "Begin.");
    let transcript = world.base.join(".claude/projects/example").join(format!("{id}.jsonl"));
    Setup { world, returns: PathBuf::from(state["return"].as_str().unwrap()), id, transcript }
}

fn callback(setup: &Setup) -> Measured {
    let event = json!({"hook_event_name": "Stop", "session_id": setup.id, "transcript_path": setup.transcript});
    measure(&setup.world, &["capture", "claude"], Some(&setup.id), &event.to_string())
}

#[test]
#[ignore = "a measurement, not a check"]
fn callbacks_as_a_transcript_grows_and_is_rewritten() {
    let largest: u64 = std::env::var("BERMAN_SCALE_MIB").ok().and_then(|text| text.parse().ok()).unwrap_or(250);
    let episode = "episodes/growing";
    let setup = setup(episode);
    let mut noise = Noise(0x9e37_79b9_7f4a_7c15);
    println!("\nCallbacks on a growing transcript (Codex allows 3 s at an interruption or the end of a session)");
    for target in [1, 16, 64, 128, largest].into_iter().filter(|target| *target <= largest) {
        grow(&setup.transcript, &setup.id, &mut noise, target * MIB);
        row(&format!("append to {target} MiB"), &callback(&setup));
        row(&format!("observe the same {target} MiB again"), &callback(&setup));
    }
    grow(&setup.transcript, &setup.id, &mut noise, largest * MIB + 64 * 1024);
    row(&format!("append 64 KiB to {largest} MiB"), &callback(&setup));
    // A rewrite: a longer first line moves every later byte, so nothing captured before is a prefix or a
    // shared chunk. The copy is streamed: on Linux a child starts with its parent's peak memory.
    let rewritten = setup.transcript.with_extension("rewritten");
    {
        use std::io::{BufRead, BufReader};
        let mut source = BufReader::new(std::fs::File::open(&setup.transcript).unwrap());
        let mut first = Vec::new();
        source.read_until(b'\n', &mut first).unwrap();
        let mut out = std::io::BufWriter::new(std::fs::File::create(&rewritten).unwrap());
        writeln!(out, "{}", json!({"sessionId": setup.id, "type": "summary", "summary": "rewritten"})).unwrap();
        std::io::copy(&mut source, &mut out).unwrap();
    }
    std::fs::rename(&rewritten, &setup.transcript).unwrap();
    row(&format!("rewrite at {largest} MiB"), &callback(&setup));
    let capture = setup.world.json(&format!("{episode}/telemetry/parent/capture.json"));
    assert_eq!(capture["previous_snapshot_is_prefix"], false);
    row("close", &measure(&setup.world, &["close", episode, "--reason", "Measured."], None, ""));
    row("verify", &measure(&setup.world, &["verify", episode], None, ""));
    let limitations = setup.world.json(&format!("{episode}/coverage.json"))["limitations"].clone();
    println!("coverage: {limitations}");
}

#[test]
#[ignore = "a measurement, not a check"]
fn a_callback_on_one_very_long_line() {
    let episode = "episodes/long-line";
    let setup = setup(episode);
    println!("\nCallbacks on a transcript with single lines of 6 MiB (rendered) and 32 MiB (kept raw)");
    for megabytes in [6usize, 32] {
        let said = "0123456789abcdef".repeat(megabytes * 65536);
        let line = format!("{}\n", json!({"sessionId": setup.id, "message": {"role": "user", "content": said}}));
        std::fs::OpenOptions::new().append(true).open(&setup.transcript).unwrap().write_all(line.as_bytes()).unwrap();
        row(&format!("after one {megabytes} MiB line"), &callback(&setup));
    }
    let capture = setup.world.json(&format!("{episode}/telemetry/parent/capture.json"));
    println!("unrendered lines: {}", capture["unrendered_long_lines"]);
}

#[test]
#[ignore = "a measurement, not a check"]
fn a_record_near_the_shared_limit() {
    let episode = "episodes/large-return";
    let setup = setup(episode);
    let mut noise = Noise(0x2545_f491_4f6c_dd1d);
    // Four files of 250 MiB and one of 60 MiB: the last would pass the 1 GiB shared limit.
    for (name, megabytes) in [("a.bin", 250), ("b.bin", 250), ("c.bin", 250), ("d.bin", 250), ("e.bin", 60)] {
        let mut file = std::fs::File::create(setup.returns.join(name)).unwrap();
        for _ in 0..megabytes {
            file.write_all(&noise.text(MIB as usize).as_bytes()[..MIB as usize]).unwrap();
        }
    }
    std::fs::write(setup.returns.join("too-large.bin"), vec![0u8; 0]).unwrap();
    println!("\nA return of 1,060 MiB against the 1 GiB record limit");
    let closed = measure(&setup.world, &["close", episode, "--reason", "Measured."], None, "");
    row("close and collect", &closed);
    assert!(closed.succeeded);
    let status = setup.world.cli(&["status", episode]);
    println!("preserved: {}", status["preserved"]);
    let coverage = setup.world.json(&format!("{episode}/coverage.json"));
    println!("coverage: {}", coverage["limitations"]);
    assert_eq!(status["preserved"].as_array().unwrap().len(), 1);
    row("verify", &measure(&setup.world, &["verify", episode], None, ""));
    let out = setup.world.base.join("exported");
    row("export", &measure(&setup.world, &["library", "export", episode, &text(&out)], None, ""));
    row("verify the export", &measure(&setup.world, &["verify", &text(&out)], None, ""));
    row(
        "review (copies share bytes)",
        &measure(&setup.world, &["review", episode, "--question", "Q?", "--output", "reviews/q"], None, ""),
    );
    // The file left over the limit keeps the return directory. The producer deals with it, then closes again.
    std::fs::remove_file(setup.returns.join("e.bin")).unwrap();
    row(
        "close again after the leftover is gone",
        &measure(&setup.world, &["close", episode, "--reason", "Measured."], None, ""),
    );
    row("remove the episode", &measure(&setup.world, &["library", "rm", "--recursive", episode], None, ""));
    setup.world.cli(&["verify", &format!("reviews/q/sources/{}", setup.id)]);
}

#[test]
#[ignore = "a measurement, not a check"]
fn a_full_disk_leaves_the_last_generation_verifiable() {
    let Some(small) = std::env::var_os("BERMAN_SMALL_DISK").map(PathBuf::from) else {
        println!("\nSet BERMAN_SMALL_DISK to a directory on a filesystem with about 100 MiB free.");
        return;
    };
    let mut world = World::new();
    let library = small.join(format!("library-{}", std::process::id()));
    world.env.insert("BERMAN_LIBRARY".into(), text(&library));
    world.cli(&["library", "init", "--key", "file"]);
    let episode = "episodes/full";
    let state = world.open(episode, "claude", &[]);
    let id = state["id"].as_str().unwrap().to_string();
    world.send(episode, None, "Begin.");
    let transcript = world.base.join(".claude/projects/example").join(format!("{id}.jsonl"));
    let mut noise = Noise(0x1234_5678_9abc_def1);
    grow(&transcript, &id, &mut noise, 8 * MIB);
    let event = json!({"hook_event_name": "Stop", "session_id": id, "transcript_path": transcript}).to_string();
    let run = |world: &World| {
        let mut command = world.command(&berman());
        command.args(["capture", "claude"]).env("BERMAN_EPISODE", &id).stdin(Stdio::piped());
        let mut child = command.stdout(Stdio::null()).stderr(Stdio::piped()).spawn().unwrap();
        let _ = child.stdin.take().unwrap().write_all(event.as_bytes());
        let output = child.wait_with_output().unwrap();
        (output.status.success(), String::from_utf8_lossy(&output.stderr).trim().to_string())
    };
    assert!(run(&world).0);
    let committed = world.json(&format!("{episode}/telemetry/parent/capture.json"));
    println!("\nA transcript grows past the space left for the library");
    grow(&transcript, &id, &mut noise, 200 * MIB);
    let (succeeded, said) = run(&world);
    println!("callback at 200 MiB on the small disk: succeeded={succeeded} {said}");
    let after = world.run(&["library", "cat", &format!("{episode}/telemetry/parent/capture.json")]);
    let after: Value = serde_json::from_slice(&after.stdout).expect("the last generation is readable");
    assert_eq!(after, committed, "the last committed generation is unchanged");
    let events = world.ls(&format!("{episode}/telemetry/events"));
    let rows: Vec<Value> =
        events.iter().map(|name| world.json(&format!("{episode}/telemetry/events/{name}"))).collect();
    let noted: Vec<&Value> = rows.iter().filter(|row| row.get("limitation").is_some()).collect();
    println!(
        "events: {} recorded, {} with a limitation: {}",
        rows.len(),
        noted.len(),
        noted.first().map(|row| row["limitation"].to_string()).unwrap_or_default()
    );
    // With room again, the next capture succeeds from the source.
    std::fs::OpenOptions::new().write(true).open(&transcript).unwrap().set_len(8 * MIB).unwrap();
    let content = std::fs::read(&transcript).unwrap();
    let end = content.iter().rposition(|byte| *byte == b'\n').unwrap() + 1;
    std::fs::write(&transcript, &content[..end]).unwrap();
    println!("callback after the transcript is small again: succeeded={}", run(&world).0);
    let receipt = world.run(&["close", episode, "--reason", "Measured."]);
    println!("close: {}", if receipt.status.success() { "record published" } else { "refused" });
    println!("{}", String::from_utf8_lossy(&receipt.stderr).trim());
    println!("verify: {}", world.run(&["verify", episode]).status.success());
    let _ = std::fs::remove_dir_all(&library);
}
