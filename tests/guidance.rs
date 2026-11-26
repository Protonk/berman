//! Keep the maintained operating instructions usable from a source checkout.

mod support;

use std::path::{Path, PathBuf};

use support::World;

fn root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
}

fn help(world: &World, arguments: &[&str]) -> String {
    let output = world.run(arguments);
    assert!(output.status.success(), "{arguments:?}: {}", String::from_utf8_lossy(&output.stderr));
    String::from_utf8(output.stdout).unwrap()
}

/// The commands a help page lists.
fn commands(page: &str) -> Vec<String> {
    let listed = page.lines().skip_while(|line| *line != "Commands:").skip(1).take_while(|line| line.starts_with("  "));
    let names = listed.filter(|line| !line.starts_with("   ")).filter_map(|line| line.split_whitespace().next());
    names.filter(|name| *name != "help").map(str::to_string).collect()
}

fn markdown(directory: &Path, found: &mut Vec<PathBuf>) {
    for entry in std::fs::read_dir(directory).unwrap().flatten() {
        let path = entry.path();
        if path.is_dir() {
            markdown(&path, found);
        } else if path.extension().is_some_and(|extension| extension == "md") {
            found.push(path);
        }
    }
}

fn documents() -> Vec<PathBuf> {
    let mut found = vec![root().join("README.md"), root().join("AGENTS.md")];
    markdown(&root().join("docs"), &mut found);
    markdown(&root().join("development"), &mut found);
    found
}

#[test]
fn help_aliases_match_and_hide_the_callback_commands() {
    let world = World::bare();
    let top = help(&world, &["help"]);
    assert_eq!(top, help(&world, &["--help"]));
    let names = commands(&top);
    assert!(names.contains(&"library".to_string()) && names.contains(&"open".to_string()));
    assert!(!names.iter().any(|name| name == "capture" || name == "relay"));
    for name in &names {
        assert_eq!(help(&world, &["help", name]), help(&world, &[name, "--help"]), "{name}");
    }
    for name in commands(&help(&world, &["help", "library"])) {
        assert_eq!(help(&world, &["help", "library", &name]), help(&world, &["library", &name, "--help"]), "{name}");
    }
}

#[test]
fn local_links_and_documented_commands() {
    let world = World::bare();
    let names = commands(&help(&world, &["help"]));
    let library = commands(&help(&world, &["help", "library"]));
    for path in documents() {
        let body = std::fs::read_to_string(&path).unwrap();
        for (at, _) in body.match_indices("](") {
            let target = body[at + 2..].split([')', ' ']).next().unwrap();
            if target.contains("://") || target.starts_with('#') {
                continue;
            }
            let file = path.parent().unwrap().join(target.split('#').next().unwrap());
            assert!(file.exists(), "{} links to {target}", path.display());
        }
        for line in body.lines() {
            let Some(rest) = line.strip_prefix("uv run ").unwrap_or(line).strip_prefix("berman ") else {
                continue;
            };
            let mut words = rest.split_whitespace();
            let command = words.next().unwrap_or_default();
            assert!(names.iter().any(|name| name == command), "{} documents berman {command}", path.display());
            if command == "library" {
                let inner = words.next().unwrap_or_default();
                assert!(
                    library.iter().any(|name| name == inner),
                    "{} documents berman library {inner}",
                    path.display()
                );
            }
        }
    }
}

fn sources(directory: &Path, extension: &str, found: &mut Vec<PathBuf>) {
    for entry in std::fs::read_dir(directory).unwrap().flatten() {
        let path = entry.path();
        if path.is_dir() && path.file_name().is_some_and(|name| name != "golden") {
            sources(&path, extension, found);
        } else if path.extension().is_some_and(|found| found == extension) {
            found.push(path);
        }
    }
}

/// Earlier names for the person who opens and closes episodes, as whole words.
fn former_role_names(text: &str) -> Vec<String> {
    let lower = text.to_lowercase();
    let mut found = Vec::new();
    for word in lower.split(|c: char| !c.is_alphanumeric() && c != '_') {
        let singular = word.strip_suffix('s').unwrap_or(word);
        if ["manager", "operator", "researcher"].contains(&singular) {
            found.push(word.to_string());
        }
    }
    found
}

#[test]
fn the_role_is_named_producer() {
    let world = World::bare();
    // The person who opens and closes episodes is the producer. Release notes keep their history.
    let mut files = vec![root().join("README.md"), root().join("AGENTS.md"), root().join("Cargo.toml")];
    markdown(&root().join("development"), &mut files);
    let mut guides = Vec::new();
    markdown(&root().join("docs"), &mut guides);
    files.extend(guides.into_iter().filter(|path| !path.components().any(|part| part.as_os_str() == "releases")));
    sources(&root().join("src/berman"), "rs", &mut files);
    sources(&root().join("src/berman"), "md", &mut files);
    sources(&root().join("tests"), "rs", &mut files);
    let mut texts: Vec<(String, String)> = Vec::new();
    for path in files.into_iter().filter(|path| !path.ends_with("tests/guidance.rs")) {
        texts.push((path.display().to_string(), std::fs::read_to_string(&path).unwrap()));
    }
    let top = help(&world, &["help"]);
    for name in commands(&top) {
        texts.push((format!("berman help {name}"), help(&world, &["help", &name])));
    }
    for name in commands(&help(&world, &["help", "library"])) {
        texts.push((format!("berman help library {name}"), help(&world, &["help", "library", &name])));
    }
    texts.push(("berman help".into(), top));
    for (source, text) in texts {
        assert_eq!(former_role_names(&text), Vec::<String>::new(), "{source}");
    }
}
