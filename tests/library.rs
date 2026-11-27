//! The producer's library: directory operations, the key, exports, and records brought in from earlier releases.

mod support;

use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};

use berman::files;
use serde_json::{Value, json};
use support::{World, text};

fn golden() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/golden/python-0.26")
}

/// Every file below a directory with its digest, to show a directory was left intact.
fn inventory(directory: &Path) -> Vec<(String, String)> {
    let mut found = Vec::new();
    let mut pending = vec![directory.to_path_buf()];
    while let Some(next) = pending.pop() {
        for entry in std::fs::read_dir(&next).unwrap().flatten() {
            let path = entry.path();
            if path.is_dir() {
                pending.push(path);
            } else {
                found
                    .push((text(path.strip_prefix(directory).unwrap()), files::digest(&std::fs::read(&path).unwrap())));
            }
        }
    }
    found.sort();
    found
}

fn closed_episode(world: &World, name: &str) -> Value {
    world.open(name, "claude", &[&world.job]);
    world.cli(&["start", name]);
    world.cli(&["close", name, "--reason", "Done."])
}

#[test]
fn help_shows_how_to_browse_and_its_examples_work() {
    let world = World::new();
    let help = String::from_utf8(world.run(&["help", "library"]).stdout).unwrap();
    for example in [
        "berman library ls\n",
        "berman library ls episodes\n",
        "berman library cat episodes/NAME/return/HANDOVER.md",
        "berman library mkdir",
        "berman library put",
        "berman library export",
        "berman library cp",
        "berman library mv",
        "berman library rm",
        "unattended terminal",
    ] {
        assert!(help.contains(example), "help library lacks {example:?}");
    }
    let notes = world.base.join("notes.md");
    let bytes: Vec<u8> = (0..=255u8).cycle().take(5 * 1024 * 1024 + 17).collect();
    std::fs::write(&notes, &bytes).unwrap();
    assert!(world.ls("").is_empty());
    world.cli(&["library", "mkdir", "reviews/archive"]);
    world.refuses(&["library", "mkdir", "reviews/archive"], "already exists");
    world.cli(&["library", "put", &text(&notes), "reviews/NAME/notes.md"]);
    world.refuses(&["library", "put", &text(&notes), "reviews/NAME/notes.md"], "already exists");
    assert_eq!(world.ls(""), ["reviews/"]);
    assert_eq!(world.ls("reviews"), ["NAME/", "archive/"]);
    assert_eq!(world.cat("reviews/NAME/notes.md"), bytes);
    let long = String::from_utf8(world.run(&["library", "ls", "--long", "reviews/NAME"]).stdout).unwrap();
    assert!(long.contains(&bytes.len().to_string()) && long.trim_end().ends_with("notes.md"));
    world.cli(&["library", "cp", "reviews/NAME", "reviews/NAME-copy"]);
    world.cli(&["library", "mv", "reviews/NAME", "reviews/archive/NAME"]);
    world.refuses(&["library", "cat", "reviews/NAME/notes.md"], "No such file");
    world.refuses(&["library", "cat", "reviews/archive"], "is a directory");
    world.refuses(&["library", "mv", "reviews/archive", "reviews/archive/NAME/inside"], "into itself");
    world.refuses(&["library", "ls", "reviews/../reviews"], "names each directory in full");
    let out = world.base.join("NAME");
    world.cli(&["library", "export", "reviews/archive/NAME", &text(&out)]);
    assert_eq!(std::fs::read(out.join("notes.md")).unwrap(), bytes);
    world.refuses(&["library", "export", "reviews/archive/NAME", &text(&out)], "already exists");
    let single = world.base.join("one.md");
    world.cli(&["library", "export", "reviews/NAME-copy/notes.md", &text(&single)]);
    assert_eq!(std::fs::read(&single).unwrap(), bytes);
    world.refuses(&["library", "rm", "reviews/archive"], "not empty");
    world.cli(&["library", "rm", "reviews/archive/NAME/notes.md"]);
    world.cli(&["library", "rm", "--recursive", "reviews/archive"]);
    assert_eq!(world.ls("reviews"), ["NAME-copy/"]);
    // The copy keeps its bytes after its source is gone.
    assert_eq!(world.cat("reviews/NAME-copy/notes.md"), bytes);
}

#[test]
fn copies_share_stored_bytes() {
    let world = World::new();
    let large = world.base.join("large.bin");
    std::fs::write(&large, files::random(4 * 1024 * 1024)).unwrap();
    world.cli(&["library", "put", &text(&large), "reviews/large.bin"]);
    let size = || std::fs::metadata(world.data().join("library.db")).unwrap().len();
    let once = size();
    for copy in 0..5 {
        world.cli(&["library", "cp", "reviews/large.bin", &format!("reviews/copy-{copy}.bin")]);
    }
    world.cli(&["library", "put", &text(&large), "reviews/again.bin"]);
    assert!(size() - once < 128 * 1024, "six more copies grew the library by {} bytes", size() - once);
    for name in world.ls("reviews") {
        world.cli(&["library", "rm", &format!("reviews/{name}")]);
    }
    // With the last reference gone the bytes are released for reuse.
    world.cli(&["library", "put", &text(&large), "reviews/large.bin"]);
    assert!(size() <= once + 128 * 1024);
}

#[test]
fn a_missing_key_is_reported_and_the_library_is_left_alone() {
    let world = World::new();
    world.cli(&["library", "mkdir", "episodes"]);
    let (key, held) = (world.data().join("library.key"), world.base.join("held.key"));
    assert_eq!(std::fs::metadata(&key).unwrap().permissions().mode() & 0o777, 0o600);
    assert_eq!(std::fs::metadata(world.data().join("library.db")).unwrap().permissions().mode() & 0o777, 0o600);
    let before = inventory(&world.data());
    std::fs::rename(&key, &held).unwrap();
    for command in [vec!["library", "ls"], vec!["status", "episodes/any"], vec!["library", "mkdir", "x"]] {
        world.refuses(&command, "key file is missing");
        world.refuses(&command, "library is unchanged");
    }
    world.refuses(&["library", "init", "--key", "file"], "already exists here and is unchanged");
    std::fs::write(&key, format!("{}\n", files::hex(&files::random(32)))).unwrap();
    std::fs::set_permissions(&key, std::fs::Permissions::from_mode(0o600)).unwrap();
    world.refuses(&["library", "ls"], "does not open the library");
    std::fs::set_permissions(&key, std::fs::Permissions::from_mode(0o644)).unwrap();
    world.refuses(&["library", "ls"], "only its owner can read");
    std::fs::rename(&held, &key).unwrap();
    assert_eq!(world.ls(""), ["episodes/"]);
    assert_eq!(inventory(&world.data()), before);
    world.refuses(&["library", "init"], "already exists");
}

#[test]
fn a_backup_of_the_database_and_key_restores_in_another_home() {
    let world = World::new();
    let receipt = closed_episode(&world, "episodes/kept");
    let place = world.cli(&["library", "where"]);
    assert!(place["backup"].as_str().unwrap().contains("library.json"));
    let restored = World::bare();
    std::fs::create_dir_all(restored.data()).unwrap();
    for name in ["library.db", "library.json", "library.key"] {
        std::fs::copy(world.data().join(name), restored.data().join(name)).unwrap();
    }
    assert_eq!(restored.cli(&["verify", "episodes/kept"])["sha256"], receipt["sha256"]);
    assert_eq!(restored.cat("episodes/kept/return/decision.md"), world.cat("episodes/kept/return/decision.md"));
    let backup = place["backup"].as_str().unwrap();
    assert!(backup.contains("return directory") && backup.contains(".retiring"));
    // The restored library is a working one: a new episode opens, sits and closes in it.
    restored.open("episodes/new", "claude", &[&restored.job]);
    assert_eq!(restored.cli(&["start", "episodes/new"])["exit"], 0);
    assert_eq!(restored.cli(&["close", "episodes/new", "--reason", "Done."])["coverage"]["limitations"], json!([]));
}

#[test]
fn an_export_holds_one_state_of_each_file_while_the_library_changes() {
    let world = World::new();
    let (old, new) = (world.base.join("old.bin"), world.base.join("new.bin"));
    std::fs::write(&old, vec![b'a'; 3 * 1024 * 1024]).unwrap();
    std::fs::write(&new, vec![b'b'; 2 * 1024 * 1024]).unwrap();
    for name in ["first.bin", "second.bin", "third.bin"] {
        world.cli(&["library", "put", &text(&old), &format!("reviews/busy/{name}")]);
    }
    let stop = std::sync::atomic::AtomicBool::new(false);
    std::thread::scope(|scope| {
        scope.spawn(|| {
            // Another command keeps replacing the members while the exports run.
            while !stop.load(std::sync::atomic::Ordering::Relaxed) {
                for (name, from) in
                    [("second.bin", &new), ("third.bin", &new), ("second.bin", &old), ("third.bin", &old)]
                {
                    world.cli(&["library", "put", "--replace", &text(from), &format!("reviews/busy/{name}")]);
                }
            }
        });
        for round in 0..12 {
            let out = world.base.join(format!("exported-{round}"));
            world.cli(&["library", "export", "reviews/busy", &text(&out)]);
            for name in ["first.bin", "second.bin", "third.bin"] {
                let bytes = std::fs::read(out.join(name)).unwrap();
                let whole = bytes == vec![b'a'; 3 * 1024 * 1024] || bytes == vec![b'b'; 2 * 1024 * 1024];
                assert!(whole, "{name} was exported with {} bytes in round {round}", bytes.len());
            }
        }
        stop.store(true, std::sync::atomic::Ordering::Relaxed);
    });
}

#[test]
fn a_changed_or_moved_record_says_so_when_verified() {
    let world = World::new();
    let receipt = closed_episode(&world, "episodes/kept");
    let id = receipt["episode"].as_str().unwrap();
    let manifest = world.cat("episodes/kept/manifest.json");
    world.cli(&["library", "cp", "episodes/kept", "reviews/untouched"]);
    world.cli(&["library", "mv", "episodes/kept", "episodes/archive/kept"]);
    assert_eq!(world.cli(&["verify", "episodes/archive/kept"])["sha256"], receipt["sha256"]);
    assert_eq!(world.cli(&["verify", id])["record"], "episodes/archive/kept");
    world.cli(&["library", "rm", "episodes/archive/kept/return/decision.md"]);
    world.refuses(&["verify", id], "missing return/decision.md");
    let note = world.base.join("note.md");
    std::fs::write(&note, "added later").unwrap();
    world.cli(&["library", "put", &text(&note), "episodes/archive/kept/note.md"]);
    world.cli(&["library", "put", "--replace", &text(&note), "episodes/archive/kept/opening.md"]);
    world.refuses(&["verify", id], "unlisted note.md");
    world.refuses(&["verify", id], "changed opening.md");
    // The original manifest still describes the bytes collected at closing, and the copy made earlier has them.
    assert_eq!(world.cat("episodes/archive/kept/manifest.json"), manifest);
    assert_eq!(
        world.cli(&["verify", "reviews/untouched", "--sha256", receipt["sha256"].as_str().unwrap()])["episode"],
        id
    );
}

#[test]
fn an_export_reports_names_the_filesystem_cannot_hold_apart() {
    let world = World::new();
    let file = world.base.join("one.md");
    std::fs::write(&file, "one").unwrap();
    world.cli(&["library", "put", &text(&file), "reviews/case/Notes.md"]);
    world.cli(&["library", "put", &text(&file), "reviews/case/notes.md"]);
    world.cli(&["library", "put", &text(&file), "reviews/case/sub/kept.md"]);
    assert_eq!(world.ls("reviews/case"), ["Notes.md", "notes.md", "sub/"]);
    std::fs::write(world.base.join("probe"), "").unwrap();
    let folds_case = world.base.join("PROBE").exists();
    let out = world.base.join("exported");
    if folds_case {
        world.refuses(&["library", "export", "reviews/case", &text(&out)], "notes.md");
        world.refuses(&["library", "export", "reviews/case", &text(&out)], "Nothing was exported");
        assert!(!out.exists());
        let left: Vec<_> = std::fs::read_dir(&world.base).unwrap().flatten().map(|e| e.file_name()).collect();
        assert!(!left.iter().any(|name| name.to_string_lossy().contains(".berman-partial-")));
    } else {
        world.cli(&["library", "export", "reviews/case", &text(&out)]);
        assert_eq!(inventory(&out).len(), 3);
    }
    // Stored spellings are untouched either way.
    assert_eq!(world.ls("reviews/case"), ["Notes.md", "notes.md", "sub/"]);
}

#[test]
fn records_from_the_python_release_verify_import_and_export() {
    let world = World::new();
    let digests: Value = serde_json::from_slice(&std::fs::read(golden().join("digests.json")).unwrap()).unwrap();
    let before = inventory(&golden());
    let listed = world.cli(&["legacy", &text(&golden())]);
    assert_eq!(listed.as_array().unwrap().len(), 3);
    for actor in ["claude", "codex"] {
        let record = golden().join(actor).join("record");
        let (id, digest) = (digests[actor]["episode"].as_str().unwrap(), digests[actor]["sha256"].as_str().unwrap());
        // The project path the record names does not exist here.
        assert_eq!(world.cli(&["verify", &text(&record), "--sha256", digest])["episode"], id);
        let destination = format!("episodes/legacy/{actor}");
        let imported = world.cli(&["import", &text(&record), &destination]);
        assert_eq!((imported["episode"].as_str(), imported["sha256"].as_str()), (Some(id), Some(digest)));
        assert_eq!(
            world.cat(&format!("{destination}/manifest.json")),
            std::fs::read(record.join("manifest.json")).unwrap()
        );
        assert_eq!(world.cli(&["verify", id, "--sha256", digest])["record"], destination);
        let status = world.cli(&["status", id]);
        assert_eq!(
            (status["return"].clone(), status["ending"]["reason"].as_str()),
            (Value::Null, Some("The legacy record is ready."))
        );
        world.refuses(&["import", &text(&record), "episodes/legacy/again"], "already known to the library");
        world.refuses(&["close", id, "--reason", "Again."], "closed where its record was made");
        world.refuses(&["start", id], "closed");
        let out = world.base.join(format!("exported-{actor}"));
        world.cli(&["library", "export", &destination, &text(&out)]);
        assert_eq!(world.cli(&["verify", &text(&out), "--sha256", digest])["episode"], id);
        assert_eq!(inventory(&out), inventory(&record));
    }
    let review = world.cli(&["import", &text(&golden().join("review")), "reviews/legacy"]);
    assert_eq!(review["imported"], "review");
    assert_eq!(world.read("reviews/legacy/question.md"), "What did each actor decide?\n");
    assert!(world.read("reviews/legacy/answer.md").starts_with("Both kept the reader."));
    for source in world.json("reviews/legacy/sources.json").as_array().unwrap() {
        let path = format!("reviews/legacy/{}", source["path"].as_str().unwrap());
        assert_eq!(world.cli(&["verify", &path])["sha256"], source["sha256"]);
    }
    let listed = world.cli(&["legacy", &text(&golden())]);
    let known: Vec<&Value> = listed.as_array().unwrap().iter().filter(|row| row["kind"] == "record").collect();
    assert!(
        known.iter().all(|row| row["in_library"].as_str().is_some_and(|path| path.starts_with("episodes/legacy/")))
    );
    // Importing leaves its input as it was.
    assert_eq!(inventory(&golden()), before);
    world.refuses(&["import", &text(&world.base), "episodes/nothing"], "Expected a record or review directory");
}

#[test]
fn a_damaged_record_is_not_imported() {
    let world = World::new();
    let copy = world.base.join("damaged");
    let status =
        std::process::Command::new("cp").arg("-R").arg(golden().join("claude/record")).arg(&copy).status().unwrap();
    assert!(status.success());
    std::fs::write(copy.join("return/decision.md"), "rewritten").unwrap();
    world.refuses(&["import", &text(&copy), "episodes/damaged"], "changed return/decision.md");
    assert!(world.ls("").is_empty());
}

#[test]
fn a_dedicated_harness_home_is_stable_and_unlisted() {
    let mut world = World::new();
    let home = |world: &World, actor: &str| {
        String::from_utf8(world.run(&["harness-home", actor]).stdout).unwrap().trim().to_string()
    };
    let (codex, claude) = (PathBuf::from(home(&world, "codex")), home(&world, "claude"));
    assert_eq!(codex, PathBuf::from(home(&world, "codex")));
    assert_ne!(codex, PathBuf::from(&claude));
    assert_eq!(std::fs::metadata(&codex).unwrap().permissions().mode() & 0o777, 0o700);
    let parent = codex.parent().unwrap();
    assert_eq!(std::fs::read_dir(parent).unwrap_err().kind(), std::io::ErrorKind::PermissionDenied);
    // An episode opened with that home is bound to it, and its sittings run there.
    world.env.insert("CLAUDE_CONFIG_DIR".into(), claude.clone());
    let state = world.open("episodes/dedicated", "claude", &[]);
    assert_eq!(state["home"], json!(claude));
    assert_eq!(world.send("episodes/dedicated", None, "Begin.")["response"], "done 1: Begin.");
    world.cli(&["close", "episodes/dedicated", "--reason", "Qualified."]);
    assert!(world.read("episodes/dedicated/telemetry/parent/conversation.txt").contains("done 1: Begin."));
}

/// Keeps a key in the login's own keychain or Secret Service, then removes it. Run with --ignored in a login
/// whose store is unlocked.
#[test]
#[ignore = "uses the login's own key store"]
fn the_system_key_store_keeps_and_returns_the_key() {
    let world = World::bare();
    let mut world = world;
    for name in ["USER", "LOGNAME", "DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR"] {
        if let Ok(value) = std::env::var(name) {
            world.env.insert(name.into(), value);
        }
    }
    let system = if cfg!(target_os = "macos") { "keychain" } else { "secret-service" };
    if cfg!(target_os = "macos") {
        // The login keychain is found through the real home directory.
        let real = std::env::var("HOME").unwrap();
        world.env.insert("BERMAN_LIBRARY".into(), text(&world.base.join("library")));
        world.env.insert("HOME".into(), real);
    }
    let place = world.cli(&["library", "init"]);
    assert!(place["key"].as_str().unwrap().contains("berman-library"));
    let data = PathBuf::from(place["library"].as_str().unwrap()).parent().unwrap().to_path_buf();
    assert!(!data.join("library.key").exists());
    let about: Value = serde_json::from_slice(&std::fs::read(data.join("library.json")).unwrap()).unwrap();
    assert_eq!(about["key"], system);
    world.cli(&["library", "mkdir", "episodes"]);
    assert_eq!(world.ls(""), ["episodes/"]);
    let account = about["id"].as_str().unwrap();
    let removed = if cfg!(target_os = "macos") {
        std::process::Command::new("/usr/bin/security")
            .args(["delete-generic-password", "-a", account, "-s", "berman-library"])
            .output()
    } else {
        std::process::Command::new("secret-tool")
            .args(["clear", "service", "berman-library", "account", account])
            .output()
    };
    assert!(removed.unwrap().status.success());
    world.refuses(&["library", "ls"], "The library is unchanged");
}

/// Records exported by the native build on each platform, with the project paths of the machines that made them.
#[test]
fn records_made_on_each_platform_verify_here() {
    let world = World::new();
    let native = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/golden/native");
    let mut platforms = 0;
    for entry in std::fs::read_dir(&native).unwrap().flatten() {
        let made: Value = serde_json::from_slice(&std::fs::read(entry.path().join("digest.json")).unwrap()).unwrap();
        let record = entry.path().join("record");
        let digest = made["sha256"].as_str().unwrap();
        let verified = world.cli(&["verify", &text(&record), "--sha256", digest]);
        assert_eq!(verified["episode"], made["episode"]);
        let state: Value = serde_json::from_slice(&std::fs::read(record.join("episode.json")).unwrap()).unwrap();
        assert!(!Path::new(state["project"].as_str().unwrap()).exists(), "the original project path is unavailable");
        let destination = format!("episodes/from-{}", entry.file_name().to_string_lossy());
        world.cli(&["import", &text(&record), &destination]);
        assert_eq!(world.cli(&["verify", &destination, "--sha256", digest])["sha256"], digest);
        platforms += 1;
    }
    assert_eq!(platforms, 2, "a record from Linux and one from macOS");
}

/// Writes this platform's record under tests/golden/native. Run with --ignored when the record format changes.
#[test]
#[ignore = "writes a fixture"]
fn write_this_platform_s_record() {
    let world = World::new();
    let name = "episodes/Platform Récord";
    world.open(name, "codex", &[&world.job]);
    world.send(name, None, "What is in this checkout?");
    world.cli(&["resume", name, "--request", "again", "--prompt", "Continue."]);
    let receipt = world.cli(&["close", name, "--reason", "A record for another platform to verify."]);
    let platform = if cfg!(target_os = "macos") { "macos" } else { "linux" };
    let out = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/golden/native").join(platform);
    let _ = std::fs::remove_dir_all(&out);
    std::fs::create_dir_all(&out).unwrap();
    world.cli(&["library", "export", name, &text(&out.join("record"))]);
    let made = json!({"episode": receipt["episode"], "sha256": receipt["sha256"], "target": std::env::consts::OS});
    std::fs::write(out.join("digest.json"), format!("{made:#}\n")).unwrap();
}

#[test]
fn a_removed_record_can_be_imported_again() {
    let world = World::new();
    let receipt = closed_episode(&world, "episodes/kept");
    let (id, digest) = (receipt["episode"].as_str().unwrap(), receipt["sha256"].as_str().unwrap());
    let out = world.base.join("exported");
    world.cli(&["library", "export", "episodes/kept", &text(&out)]);
    world.cli(&["library", "rm", "--recursive", "episodes/kept"]);
    world.refuses(&["status", id], "No episode");
    let imported = world.cli(&["import", &text(&out), "episodes/back"]);
    assert_eq!((imported["episode"].as_str(), imported["sha256"].as_str()), (Some(id), Some(digest)));
    assert_eq!(world.cli(&["verify", id])["record"], "episodes/back");
}

#[test]
fn a_review_is_imported_only_with_every_source_listed_and_verified() {
    let world = World::new();
    let copy = |name: &str| {
        let to = world.base.join(name);
        assert!(
            std::process::Command::new("cp")
                .arg("-R")
                .arg(golden().join("review"))
                .arg(&to)
                .status()
                .unwrap()
                .success()
        );
        to
    };
    let listed: Value = serde_json::from_slice(&std::fs::read(golden().join("review/sources.json")).unwrap()).unwrap();
    // Not a list of sources at all: this is some other directory with a file of that name.
    let other = copy("not-a-review");
    std::fs::write(other.join("sources.json"), "{\"anything\": true}").unwrap();
    world.refuses(&["import", &text(&other), "reviews/other"], "Expected a record or review directory");
    // A source carried along without being listed.
    let unlisted = copy("unlisted");
    std::fs::write(unlisted.join("sources.json"), format!("{:#}", json!([listed[0]]))).unwrap();
    world.refuses(&["import", &text(&unlisted), "reviews/unlisted"], "is not listed in sources.json");
    // A path that leaves the review directory is read as a path inside it, where nothing is.
    let outside = copy("outside");
    let record = golden().join("claude/record");
    let entry = json!([{"episode": listed[0]["episode"], "path": text(&record), "sha256": listed[0]["sha256"]}]);
    std::fs::write(outside.join("sources.json"), format!("{entry:#}")).unwrap();
    world.refuses(&["import", &text(&outside), "reviews/outside"], "Expected a record or review directory");
    // An entry with no digest to verify against.
    let undigested = copy("undigested");
    let entry = json!([{"episode": listed[0]["episode"], "path": listed[0]["path"]}, listed[1]]);
    std::fs::write(undigested.join("sources.json"), format!("{entry:#}")).unwrap();
    world.refuses(&["import", &text(&undigested), "reviews/undigested"], "Expected a record or review directory");
    // Each record listed under the other's episode: the paths and digests hold, the attribution does not.
    let swapped = copy("swapped");
    let entry = json!([
        {"episode": listed[1]["episode"], "path": listed[1]["path"], "sha256": listed[0]["sha256"]},
        {"episode": listed[0]["episode"], "path": listed[0]["path"], "sha256": listed[1]["sha256"]},
    ]);
    std::fs::write(swapped.join("sources.json"), format!("{entry:#}")).unwrap();
    world.refuses(&["import", &text(&swapped), "reviews/swapped"], "differs");
    let renamed = copy("renamed");
    let (first, second) = (listed[0]["episode"].as_str().unwrap(), listed[1]["episode"].as_str().unwrap());
    std::fs::rename(renamed.join("sources").join(first), renamed.join("sources/held")).unwrap();
    std::fs::rename(renamed.join("sources").join(second), renamed.join("sources").join(first)).unwrap();
    std::fs::rename(renamed.join("sources/held"), renamed.join("sources").join(second)).unwrap();
    let entry = json!([
        {"episode": first, "path": listed[0]["path"], "sha256": listed[1]["sha256"]},
        {"episode": second, "path": listed[1]["path"], "sha256": listed[0]["sha256"]},
    ]);
    std::fs::write(renamed.join("sources.json"), format!("{entry:#}")).unwrap();
    world.refuses(&["import", &text(&renamed), "reviews/renamed"], "holds the record of another episode");
    // A review without its question.
    let unasked = copy("unasked");
    std::fs::remove_file(unasked.join("question.md")).unwrap();
    world.refuses(&["import", &text(&unasked), "reviews/unasked"], "The review has no question.md");
    assert!(world.ls("").is_empty());
}

#[test]
fn legacy_lists_only_what_berman_made() {
    let world = World::new();
    let top = world.base.join("old");
    std::fs::create_dir_all(top.join("app/nested")).unwrap();
    // Another program's manifest, with a real record below it.
    std::fs::write(top.join("app/manifest.json"), "{\"name\": \"pwa\"}").unwrap();
    std::fs::write(top.join("app/sources.json"), "[]").unwrap();
    let record = top.join("app/nested/record");
    assert!(
        std::process::Command::new("cp")
            .arg("-R")
            .arg(golden().join("claude/record"))
            .arg(&record)
            .status()
            .unwrap()
            .success()
    );
    let listed = world.cli(&["legacy", &text(&top)]);
    assert_eq!(listed.as_array().unwrap().len(), 1, "{listed}");
    assert_eq!(
        (listed[0]["kind"].as_str(), listed[0]["path"].as_str()),
        (Some("record"), Some(text(&record).as_str()))
    );
}
