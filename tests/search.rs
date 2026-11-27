//! An instruction meant for one project reaches an actor in another. A broad search by filename or
//! phrase may find the other project. What Berman retains for it should contribute nothing.
//!
//! The search here walks every directory it is allowed to list from a starting point and reads
//! every file it is allowed to read, hidden and binary ones included, as `find` and `grep -r` do.

mod support;

use std::io::Write;
use std::os::unix::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::time::{Duration, Instant};

use serde_json::Value;
use support::{World, berman, git, text};

const RETURN_NAME: &str = "QUOKKA-HANDOVER-7731.md";
const RETURN_PHRASE: &str = "zebra-crossing-4417";
const PROMPT_PHRASE: &str = "heliotrope-lantern-9902";
const REVIEW_PHRASE: &str = "vermilion-anchor-5521";
const WRITER_PHRASE: &str = "obsidian-ferry-6644";
const CONTROL_NAME: &str = "CONTROL-MARKER-3310.txt";
const CONTROL_PHRASE: &str = "control-phrase-3310";
const NEEDLES: [&str; 5] = [RETURN_NAME, RETURN_PHRASE, PROMPT_PHRASE, REVIEW_PHRASE, WRITER_PHRASE];

fn contains(haystack: &[u8], needle: &str) -> bool {
    let needle = needle.as_bytes();
    haystack.len() >= needle.len() && haystack.windows(needle.len()).any(|window| window == needle)
}

/// Every path below a starting point whose name or bytes hold one of the words.
fn search(top: &Path, words: &[&str]) -> Vec<(String, PathBuf)> {
    let mut hits = Vec::new();
    let mut pending = vec![top.to_path_buf()];
    while let Some(directory) = pending.pop() {
        // A directory that cannot be listed is passed over, as a recursive search does.
        let Ok(listing) = std::fs::read_dir(&directory) else {
            continue;
        };
        for entry in listing.flatten() {
            let path = entry.path();
            let name = entry.file_name().to_string_lossy().into_owned();
            let Ok(kind) = entry.file_type() else {
                continue;
            };
            for word in words.iter().filter(|word| name.contains(**word)) {
                hits.push((format!("name:{word}"), path.clone()));
            }
            if kind.is_dir() {
                pending.push(path);
            } else if kind.is_file() {
                let Ok(bytes) = std::fs::read(&path) else {
                    continue;
                };
                for word in words.iter().filter(|word| contains(&bytes, word)) {
                    hits.push((format!("content:{word}"), path.clone()));
                }
            }
        }
    }
    hits
}

fn dedicated(world: &World, actor: &str) -> String {
    String::from_utf8(world.run(&["harness-home", actor]).stdout).unwrap().trim().to_string()
}

struct Search {
    world: World,
    project_a: PathBuf,
    project_b: PathBuf,
}

impl Search {
    /// Two projects in one home. Each actor's CLI keeps its history in a dedicated home.
    fn new() -> Search {
        let mut world = World::new();
        // The scripted actor's own log of its arguments is not something a real harness leaves.
        world.env.remove("FIXTURE_LOG");
        for (actor, variable) in [("claude", "CLAUDE_CONFIG_DIR"), ("codex", "CODEX_HOME")] {
            let home = dedicated(&world, actor);
            if actor == "codex" {
                std::fs::write(Path::new(&home).join("hooks.json"), world.run(&["codex-hooks"]).stdout).unwrap();
            }
            world.env.insert(variable.into(), home);
        }
        let project_a = world.project.clone();
        let project_b = world.base.join("project b");
        std::fs::create_dir(&project_b).unwrap();
        git(&project_b, &["init", "-q"]);
        // An ordinary match the search must find: a positive control for the search itself.
        std::fs::create_dir(project_b.join("notes")).unwrap();
        std::fs::write(project_b.join("notes").join(CONTROL_NAME), format!("ordinary {CONTROL_PHRASE}\n")).unwrap();
        Search { world, project_a, project_b }
    }

    /// Search from project A and from the home directory. Returns the hits for Berman's material.
    fn broad(&self) -> Vec<(String, PathBuf)> {
        let control = search(&self.world.base, &[CONTROL_NAME, CONTROL_PHRASE]);
        assert_eq!(control.len(), 2, "the search finds an ordinary project match by name and by content: {control:?}");
        let mut hits = search(&self.project_a, &NEEDLES);
        hits.extend(search(&self.world.base, &NEEDLES));
        hits
    }

    fn open(&mut self, name: &str, actor: &str, project: &Path) -> Value {
        self.world.project = project.to_path_buf();
        self.world.open(name, actor, &[])
    }
}

#[test]
fn retained_material_for_another_project_gives_a_broad_search_nothing() {
    let mut search_world = Search::new();
    let (project_a, project_b) = (search_world.project_a.clone(), search_world.project_b.clone());
    let mut returns = Vec::new();
    for actor in ["claude", "codex"] {
        let episode = format!("episodes/b-{actor}");
        let state = search_world.open(&episode, actor, &project_b);
        let world = &search_world.world;
        let sitting = world.send(&episode, None, &format!("Remember the {PROMPT_PHRASE}."));
        assert!(sitting["response"].as_str().unwrap().contains(PROMPT_PHRASE));
        // The actor leaves correspondence with a distinctive name and phrase.
        let directory = PathBuf::from(state["return"].as_str().unwrap());
        #[cfg(target_os = "linux")]
        assert_eq!(std::fs::read(directory.parent().unwrap().parent().unwrap().join(".nomedia")).unwrap(), b"");
        std::fs::create_dir(directory.join("notes")).unwrap();
        std::fs::write(directory.join(RETURN_NAME), format!("Where things stand: {RETURN_PHRASE}\n")).unwrap();
        std::fs::write(directory.join("notes/.hidden"), [b"\x00\xff binary ", RETURN_PHRASE.as_bytes()].concat())
            .unwrap();
        returns.push(directory);
    }
    // An actor is at work in project A while the search runs, so the library's journals are live.
    search_world.open("episodes/a", "claude", &project_a);
    let mut world = search_world.world;
    world.env.insert("FIXTURE_SLEEP".into(), "4".into());
    let mut command = world.command(&berman());
    command.args(["start", "episodes/a", "--unattended", "--prompt", "Work in project A."]).process_group(0);
    let mut sitting = command.stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null()).spawn().unwrap();
    world.env.remove("FIXTURE_SLEEP");
    search_world = Search { world, project_a, project_b };
    let world = &search_world.world;
    let journal = world.data().join("library.db-wal");
    let deadline = Instant::now() + Duration::from_secs(20);
    while !journal.exists() {
        assert!(Instant::now() < deadline, "the sitting never wrote to the library");
        std::thread::sleep(Duration::from_millis(20));
    }
    assert_eq!(search_world.broad(), []);
    assert!(journal.exists(), "the write-ahead log was live during the search");
    // A producer reads through Berman meanwhile. Reading leaves nothing behind for the search.
    assert!(world.read("episodes/b-claude/sittings/000001.json").contains(PROMPT_PHRASE));
    assert!(world.read("episodes/b-codex/telemetry/parent/conversation.txt").contains(PROMPT_PHRASE));
    assert_eq!(search_world.broad(), []);
    // The live return is still an ordinary directory for whoever holds its exact path.
    let known = std::fs::read_to_string(returns[0].join(RETURN_NAME)).unwrap();
    assert!(known.contains(RETURN_PHRASE));
    assert!(sitting.wait().unwrap().success());

    // A writer is killed while it stores bytes that hold a phrase. What it leaves gives the search nothing.
    let large =
        world.base.parent().unwrap().join(format!("{}-large", world.base.file_name().unwrap().to_string_lossy()));
    let mut file = std::fs::File::create(&large).unwrap();
    for number in 0..400_000 {
        writeln!(file, "{number} {WRITER_PHRASE} {}", "x".repeat(120)).unwrap();
    }
    drop(file);
    let before = std::fs::metadata(world.data().join("library.db")).unwrap().len();
    let mut writer = world.command(&berman());
    writer.args(["library", "put", &text(&large), "reviews/large.txt"]).stdout(Stdio::null()).stderr(Stdio::null());
    let mut writer = writer.spawn().unwrap();
    let written = || {
        let wal = std::fs::metadata(&journal).map(|m| m.len()).unwrap_or(0);
        wal + std::fs::metadata(world.data().join("library.db")).unwrap().len()
    };
    let deadline = Instant::now() + Duration::from_secs(60);
    while written() < before + 8 * 1024 * 1024 {
        assert!(Instant::now() < deadline && writer.try_wait().unwrap().is_none(), "the writer was not caught writing");
        std::thread::sleep(Duration::from_millis(1));
    }
    writer.kill().unwrap();
    writer.wait().unwrap();
    std::fs::remove_file(&large).unwrap();
    assert!(journal.exists(), "the killed writer left its journal");
    assert_eq!(search_world.broad(), []);
    // The library recovers, and the interrupted file is not in it.
    assert!(!world.exists("reviews/large.txt"));

    // Close both episodes and review them with a distinctive question.
    for actor in ["claude", "codex"] {
        let receipt = world.cli(&["close", &format!("episodes/b-{actor}"), "--reason", "Collected."]);
        assert_eq!((&receipt["return"], receipt["preserved"].as_array().unwrap().len()), (&Value::Null, 0));
    }
    world.cli(&["close", "episodes/a", "--reason", "Collected."]);
    let question = format!("What became of the {REVIEW_PHRASE}?");
    world.cli(&[
        "review",
        "episodes/b-claude",
        "episodes/b-codex",
        "--question",
        &question,
        "--output",
        "reviews/anchor",
    ]);
    assert_eq!(search_world.broad(), []);
    assert!(returns.iter().all(|directory| !directory.exists()));

    // Someone who asks for the library under the same login is shown how to browse all of it.
    let help = String::from_utf8(world.run(&["help", "library"]).stdout).unwrap();
    assert!(help.contains("berman library ls episodes") && help.contains("berman library cat episodes/NAME/return/"));
    assert_eq!(world.ls("episodes"), ["a/", "b-claude/", "b-codex/"]);
    for actor in ["claude", "codex"] {
        let episode = format!("episodes/b-{actor}");
        assert!(world.ls(&format!("{episode}/return")).contains(&RETURN_NAME.to_string()));
        assert_eq!(world.read(&format!("{episode}/return/{RETURN_NAME}")), known);
        let hidden = world.cat(&format!("{episode}/return/notes/.hidden"));
        assert_eq!(hidden, [b"\x00\xff binary ", RETURN_PHRASE.as_bytes()].concat());
        assert!(world.read(&format!("{episode}/telemetry/parent/transcript.jsonl")).contains(PROMPT_PHRASE));
    }
    assert_eq!(world.read("reviews/anchor/question.md"), format!("{question}\n"));

    // A deliberate export is plaintext where the producer put it, and the search finds it there.
    let exported = world.base.join("exported");
    world.cli(&["library", "export", "episodes/b-claude", &text(&exported)]);
    let found = search_world.broad();
    assert!(!found.is_empty() && found.iter().all(|(_, path)| path.starts_with(&exported)), "{found:?}");
    std::fs::remove_dir_all(&exported).unwrap();
    assert_eq!(search_world.broad(), []);
}

#[test]
fn an_everyday_harness_home_keeps_its_own_searchable_history() {
    // With the actor's everyday home, the harness's own session store repeats what was said. Nothing else does.
    let mut world = World::new();
    world.env.remove("FIXTURE_LOG");
    for actor in ["claude", "codex"] {
        let episode = format!("episodes/everyday-{actor}");
        world.open(&episode, actor, &[]);
        world.send(&episode, None, &format!("Remember the {PROMPT_PHRASE}."));
        world.cli(&["close", &episode, "--reason", "Collected."]);
    }
    let hits = search(&world.base, &NEEDLES);
    assert!(!hits.is_empty());
    for (_, path) in &hits {
        let native = path.starts_with(world.base.join(".claude/projects"))
            || path.starts_with(world.base.join(".codex/sessions"));
        assert!(native, "{} holds session text outside the harness's own store", path.display());
    }
}
