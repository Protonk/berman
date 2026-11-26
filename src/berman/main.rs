//! A producer uses Berman for intermittent project management by correspondence in a local checkout.

use std::io::Write;
use std::path::PathBuf;
use std::process::ExitCode;

use clap::{Arg, ArgAction, ArgMatches, Command};
use serde_json::{Value, json};

use berman::error::{Error, Result, refuse};
use berman::harness::{self, Harness};
use berman::keys::{self, Store};
use berman::library::{Library, ROOT};
use berman::places::{self, Places};
use berman::records::{self, Source};
use berman::{capture, episodes, files, installation, json};

const LIBRARY_EXAMPLES: &str = "\
Examples:
  berman library ls
      List the top of the library. Episodes and reviews are ordinary directories in it.
  berman library ls episodes
      List a directory. Add --long for sizes and times.
  berman library cat episodes/NAME/return/HANDOVER.md
      Write a file's exact bytes to standard output.
  berman library mkdir reviews/archive
      Create a directory.
  berman library put notes.md reviews/NAME/notes.md
      Copy a file or directory in from disk.
  berman library export episodes/NAME ./NAME
      Write a real directory or file at a fresh destination, for ordinary tools or another reader.
  berman library cp episodes/NAME episodes/NAME-copy
      Copy within the library. The copy shares stored bytes with its source.
  berman library mv episodes/NAME episodes/archive/NAME
      Rename or move. An episode keeps its ID wherever its directory goes.
  berman library rm reviews/NAME/notes.md
      Remove a file. Add --recursive for a directory that holds anything.

Paths name places inside the library and use /. The library is one encrypted file, so reading
it goes through these commands: file names and contents do not appear on disk outside it.
Every command here runs from an unattended terminal under the login that owns the library.
`berman library where` shows where the library and its key are kept and how to back them up.";

fn path(name: &'static str) -> Arg {
    Arg::new(name).value_parser(|text: &str| files::physical(text).map_err(|error| error.to_string()))
}

fn reference(name: &'static str) -> Arg {
    Arg::new(name).required(true).value_name("EPISODE").help("the episode's library path or ID")
}

fn sitting(name: &'static str, about: &'static str) -> Command {
    let (request, after) = match name {
        "start" => (
            Arg::new("request").help("label for another attempt at the opening while no native session has started"),
            "message for the actor after the opening; required with --unattended",
        ),
        _ => (
            Arg::new("request").required(true).help("stable nonempty label for this sitting"),
            "message for the actor in this sitting; required with --unattended",
        ),
    };
    Command::new(name)
        .about(about)
        .arg(reference("episode"))
        .arg(request.long("request").value_name("LABEL"))
        .arg(Arg::new("prompt").long("prompt").value_name("MESSAGE").help(after))
        .arg(
            Arg::new("unattended")
                .long("unattended")
                .action(ArgAction::SetTrue)
                .help("run the actor without its interactive interface and print the response it reports"),
        )
}

fn library_commands() -> Command {
    let inside = |name: &'static str| Arg::new(name).required(true).help("a path inside the library");
    Command::new("library")
        .about("browse and arrange the producer’s library of episodes and reviews")
        .after_help(LIBRARY_EXAMPLES)
        .subcommand_required(true)
        .arg_required_else_help(true)
        .subcommand(
            Command::new("init").about("create this login’s library and its key").arg(
                Arg::new("key")
                    .long("key")
                    .value_parser(keys::NAMES)
                    .help("where to keep the library key: the login’s keychain or Secret Service, or a mode-0600 file"),
            ),
        )
        .subcommand(Command::new("where").about("show where the library and its key are kept"))
        .subcommand(
            Command::new("ls")
                .about("list a directory in the library")
                .arg(Arg::new("path").help("a path inside the library; omit for the top"))
                .arg(Arg::new("long").long("long").short('l').action(ArgAction::SetTrue).help("show sizes and times")),
        )
        .subcommand(Command::new("cat").about("write a file’s exact bytes to standard output").arg(inside("path")))
        .subcommand(Command::new("mkdir").about("create a directory").arg(inside("path")))
        .subcommand(
            Command::new("cp")
                .about("copy a file or directory within the library")
                .arg(inside("source"))
                .arg(inside("destination")),
        )
        .subcommand(
            Command::new("mv")
                .about("rename or move a file or directory within the library")
                .arg(inside("source"))
                .arg(inside("destination")),
        )
        .subcommand(
            Command::new("rm").about("remove a file or directory").arg(inside("path")).arg(
                Arg::new("recursive")
                    .long("recursive")
                    .short('r')
                    .action(ArgAction::SetTrue)
                    .help("remove a directory with everything in it"),
            ),
        )
        .subcommand(
            Command::new("put")
                .about("copy a file or directory in from disk")
                .arg(path("local").required(true).help("a file or directory on disk"))
                .arg(inside("destination"))
                .arg(
                    Arg::new("replace")
                        .long("replace")
                        .action(ArgAction::SetTrue)
                        .help("replace an existing file at the destination"),
                ),
        )
        .subcommand(
            Command::new("export")
                .about("write a real file or directory at a fresh destination on disk")
                .arg(inside("path"))
                .arg(path("destination").required(true).help("a path on disk that does not exist yet")),
        )
}

/// Show each value's placeholder in capitals, as the guides write them.
fn capitals(command: Command) -> Command {
    let command = command.mut_args(|argument| {
        if argument.get_value_names().is_none() && argument.get_action().takes_values() {
            let name = argument.get_id().as_str().to_uppercase();
            return argument.value_name(name);
        }
        argument
    });
    command.mut_subcommands(capitals)
}

fn application() -> Command {
    capitals(commands())
}

fn commands() -> Command {
    let record = "a record in the library by path or episode ID, or a directory on disk given as /…, ./…, ../… or ~/…";
    Command::new("berman")
        .about("A producer uses Berman for intermittent project management by correspondence in a local checkout.")
        .subcommand_required(true)
        .arg_required_else_help(true)
        .subcommand(
            Command::new("template")
                .about("create an editable additional job and the note-taking and handover examples")
                .arg(path("destination").required(true).help("a new directory on disk")),
        )
        .subcommand(
            Command::new("open")
                .about("open an episode and fix any additional jobs")
                .arg(
                    Arg::new("episode")
                        .required(true)
                        .value_name("DESTINATION")
                        .help("a new library path for the episode, such as episodes/cutover"),
                )
                .arg(path("project").long("project").required(true).help("Git checkout the actor works in"))
                .arg(Arg::new("actor").long("actor").required(true).value_parser(harness::NAMES))
                .arg(path("job").long("job").value_name("FILE").action(ArgAction::Append).help(
                    "UTF-8 instructions for one additional job. Repeat for each job in the opening, or omit for none.",
                )),
        )
        .subcommand(sitting("start", "start the first sitting with the opening"))
        .subcommand(sitting("resume", "continue the same actor session in another sitting"))
        .subcommand(Command::new("status").about("show the opening, sittings, and ending").arg(reference("episode")))
        .subcommand(
            Command::new("close")
                .about("record the producer’s ending and collect the return")
                .arg(reference("episode"))
                .arg(Arg::new("reason").long("reason").required(true)),
        )
        .subcommand(
            Command::new("verify")
                .about("identify the installation or verify an episode record")
                .arg(Arg::new("record").help(record))
                .arg(
                    Arg::new("sha256")
                        .long("sha256")
                        .value_name("DIGEST")
                        .help("expected digest of the manifest from a separate copy"),
                ),
        )
        .subcommand(
            Command::new("review")
                .about("prepare verified sources and an answer file for a producer’s question")
                .arg(Arg::new("records").required(true).num_args(1..).help(record))
                .arg(Arg::new("question").long("question").required(true))
                .arg(
                    Arg::new("output")
                        .long("output")
                        .required(true)
                        .value_name("DESTINATION")
                        .help("a new library path for the review, such as reviews/cutover-question"),
                ),
        )
        .subcommand(library_commands())
        .subcommand(
            Command::new("import")
                .about("verify a record or review directory on disk and copy it into the library")
                .arg(path("directory").required(true))
                .arg(Arg::new("destination").required(true).help("a new library path for the copy")),
        )
        .subcommand(
            Command::new("legacy")
                .about("list plaintext episode, record and review directories below a directory on disk")
                .arg(path("directory").required(true)),
        )
        .subcommand(Command::new("codex-hooks").about("print the Codex hook entries for review and installation"))
        .subcommand(
            Command::new("harness-home")
                .about("print a dedicated home for an actor’s CLI, kept where broad searches do not list it")
                .arg(Arg::new("actor").required(true).value_parser(harness::NAMES)),
        )
        .subcommand(Command::new("capture").hide(true).arg(Arg::new("actor").required(true)))
        .subcommand(
            Command::new("relay")
                .hide(true)
                .arg(Arg::new("episode").required(true))
                .arg(Arg::new("name").required(true)),
        )
}

fn one<'a>(matches: &'a ArgMatches, name: &str) -> &'a PathBuf {
    matches.get_one::<PathBuf>(name).expect("a required path")
}

fn word<'a>(matches: &'a ArgMatches, name: &str) -> &'a str {
    matches.get_one::<String>(name).map(String::as_str).unwrap_or_default()
}

fn optional<'a>(matches: &'a ArgMatches, name: &str) -> Option<&'a str> {
    matches.get_one::<String>(name).map(String::as_str)
}

/// A dedicated harness home under a parent the login can enter but not list. The same path every time.
fn harness_home(library: &Library, actor: &str) -> Result<PathBuf> {
    let key = format!("home.{actor}");
    let name = match library.setting(&key)? {
        Some(name) => name,
        None => {
            let name = files::uuid_hex();
            library.set_setting(&key, &name)?;
            name
        }
    };
    places::unlisted(&library.places.homes)?;
    let home = library.places.homes.join(name);
    files::create_private_directory(&home)?;
    Ok(home)
}

fn whereabouts(library: &Library) -> Value {
    let places = &library.places;
    let key = match library.store {
        Store::File => format!("the file {}", places.key_file().display()),
        Store::Keychain => "the login keychain, as the generic password with service berman-library".into(),
        Store::SecretService => "the Secret Service, as the item with service berman-library".into(),
    };
    json!({
        "library": files::text(&places.database()),
        "key": key,
        "return_directories": files::text(&places.returns),
        "backup": format!(
            "With no Berman command running, copy library.db, any library.db-wal beside it, and library.json from \
             {} together, and keep a copy of the key from {key}. Restore all of them to the same places.",
            places.data.display()
        ),
    })
}

fn library_command(name: &str, matches: &ArgMatches) -> Result<Option<Value>> {
    if name == "init" {
        let chosen = optional(matches, "key");
        let store = match chosen {
            Some(name) => Store::named(name)?,
            None => Store::system(),
        };
        let library = Library::create(Places::find()?, store).map_err(|error| match chosen {
            None => Error::Refused(format!(
                "{error}\nTo keep the key in a file only this login can read, run: berman library init --key file"
            )),
            Some(_) => error,
        })?;
        return Ok(Some(whereabouts(&library)));
    }
    let library = Library::open()?;
    let result = match name {
        "where" => whereabouts(&library),
        "ls" => {
            let found = library.expect(ROOT, optional(matches, "path").unwrap_or_default())?;
            let listed = if found.directory { library.children(found.id)? } else { vec![found] };
            let mut out = std::io::stdout().lock();
            for entry in listed {
                let name = if entry.directory { format!("{}/", entry.name) } else { entry.name };
                if matches.get_flag("long") {
                    writeln!(out, "{:>12}  {}  {name}", entry.size, entry.modified)?;
                } else {
                    writeln!(out, "{name}")?;
                }
            }
            return Ok(None);
        }
        "cat" => {
            let found = library.expect(ROOT, word(matches, "path"))?;
            let Some(content) = found.content else {
                return refuse(format!("{} is a directory. List it with: berman library ls", word(matches, "path")));
            };
            let mut out = std::io::stdout().lock();
            library.stream(content, &mut out)?;
            out.flush()?;
            return Ok(None);
        }
        "mkdir" => json!({"created": library.path(library.make_directory(ROOT, word(matches, "path"))?.id)?}),
        "cp" => {
            let copy = library.copy(word(matches, "source"), word(matches, "destination"))?;
            json!({"copied": library.path(copy.id)?})
        }
        "mv" => {
            let moved = library.rename(word(matches, "source"), word(matches, "destination"))?;
            json!({"moved": library.path(moved.id)?})
        }
        "rm" => {
            let found = library.expect(ROOT, word(matches, "path"))?;
            if found.directory && !matches.get_flag("recursive") && !library.children(found.id)?.is_empty() {
                return refuse("The directory is not empty. Add --recursive to remove it with everything in it.");
            }
            let removed = library.path(found.id)?;
            library.remove(&found)?;
            json!({"removed": removed})
        }
        "put" => {
            let replace = matches.get_flag("replace");
            let placed = library.put(one(matches, "local"), word(matches, "destination"), replace)?;
            json!({"stored": library.path(placed.id)?})
        }
        "export" => {
            let found = library.expect(ROOT, word(matches, "path"))?;
            library.export(&found, one(matches, "destination"))?;
            json!({"exported": files::text(one(matches, "destination"))})
        }
        other => return refuse(format!("Unknown library command: {other}")),
    };
    Ok(Some(result))
}

/// Run one command. A sitting also reports the status the process should end with.
fn run(name: &str, matches: &ArgMatches) -> Result<(Option<Value>, u8)> {
    let result = match name {
        "capture" => {
            capture::main(word(matches, "actor"))?;
            return Ok((None, 0));
        }
        "relay" => {
            episodes::relay_output(&Library::open()?, word(matches, "episode"), word(matches, "name"))?;
            return Ok((None, 0));
        }
        "library" => {
            let (name, matches) = matches.subcommand().expect("a library subcommand is required");
            return Ok((library_command(name, matches)?, 0));
        }
        "template" => episodes::install_template(one(matches, "destination"))?,
        "open" => {
            let jobs: Vec<PathBuf> = matches.get_many::<PathBuf>("job").into_iter().flatten().cloned().collect();
            let (destination, actor) = (word(matches, "episode"), word(matches, "actor"));
            episodes::open_episode(&Library::open()?, destination, one(matches, "project"), actor, &jobs)?
        }
        "start" | "resume" => {
            let opening = name == "start";
            let (request, prompt) = (optional(matches, "request"), optional(matches, "prompt"));
            let unattended = matches.get_flag("unattended");
            if request.is_some_and(|label| label.trim().is_empty()) {
                return refuse("Supply a nonempty request label");
            }
            if (unattended || opening) && prompt.is_some_and(|message| message.trim().is_empty()) {
                return refuse("Supply a nonempty prompt");
            }
            if unattended && prompt.is_none() {
                return refuse("An unattended sitting needs a message in --prompt");
            }
            let library = Library::open()?;
            let sitting = episodes::launch(&library, word(matches, "episode"), request, prompt, !unattended, opening)?;
            let code = match sitting["exit"].as_i64() {
                _ if json::truthy(&sitting["error"]) => 1,
                Some(code) if code < 0 => (128 - code) as u8,
                Some(code) => code as u8,
                None => 0,
            };
            return Ok((Some(sitting), code));
        }
        "status" => episodes::status(&Library::open()?, word(matches, "episode"))?,
        "close" => episodes::close(&Library::open()?, word(matches, "episode"), word(matches, "reason"))?,
        "verify" => match (optional(matches, "record"), optional(matches, "sha256")) {
            (Some(record), expected) => records::verify_source(Library::open, &Source::named(record)?, expected)?,
            (None, Some(_)) => return refuse("--sha256 needs a record"),
            (None, None) => installation::verify()?,
        },
        "review" => {
            let named = matches.get_many::<String>("records").into_iter().flatten();
            let sources = named.map(|text| Source::named(text)).collect::<Result<Vec<Source>>>()?;
            records::review(&Library::open()?, &sources, word(matches, "question"), word(matches, "output"))?
        }
        "import" => records::import(&Library::open()?, one(matches, "directory"), word(matches, "destination"))?,
        "legacy" => {
            // The list is useful before a library exists. With one, it also says what has been imported.
            let library = Library::open().ok();
            records::legacy(library.as_ref(), one(matches, "directory"))?
        }
        "codex-hooks" => capture::hook_settings(Harness::Codex)?,
        "harness-home" => {
            println!("{}", harness_home(&Library::open()?, word(matches, "actor"))?.display());
            return Ok((None, 0));
        }
        other => return refuse(format!("Unknown command: {other}")),
    };
    Ok((Some(result), 0))
}

fn main() -> ExitCode {
    let matches = application().get_matches();
    let (name, matches) = matches.subcommand().expect("a subcommand is required");
    match run(name, matches) {
        Ok((result, code)) => {
            if let Some(result) = result {
                println!("{}", json::pretty(&result));
            }
            ExitCode::from(code)
        }
        Err(error) => {
            eprintln!("berman: {error}");
            ExitCode::FAILURE
        }
    }
}
