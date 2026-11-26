//! A producer uses Berman for intermittent project management by correspondence in a local checkout.

use std::path::PathBuf;
use std::process::ExitCode;

use clap::{Arg, ArgAction, ArgMatches, Command};
use serde_json::Value;

use berman::error::{Result, refuse};
use berman::harness::{self, Harness};
use berman::{capture, episodes, files, installation, json, records};

fn path(name: &'static str) -> Arg {
    Arg::new(name).value_parser(|text: &str| files::physical(text).map_err(|error| error.to_string()))
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
        .arg(path("episode").required(true))
        .arg(request.long("request").value_name("LABEL"))
        .arg(Arg::new("prompt").long("prompt").value_name("MESSAGE").help(after))
        .arg(
            Arg::new("unattended")
                .long("unattended")
                .action(ArgAction::SetTrue)
                .help("run the actor without its interactive interface and print the response it reports"),
        )
}

fn application() -> Command {
    Command::new("berman")
        .about("A producer uses Berman for intermittent project management by correspondence in a local checkout.")
        .subcommand_required(true)
        .arg_required_else_help(true)
        .subcommand(
            Command::new("template")
                .about("create an editable additional job and the note-taking and handover examples")
                .arg(path("destination").required(true)),
        )
        .subcommand(
            Command::new("open")
                .about("open an episode and fix any additional jobs")
                .arg(path("episode").required(true))
                .arg(path("project").long("project").required(true).help("Git checkout the actor works in"))
                .arg(Arg::new("actor").long("actor").required(true).value_parser(harness::NAMES))
                .arg(path("job").long("job").value_name("FILE").action(ArgAction::Append).help(
                    "UTF-8 instructions for one additional job. Repeat for each job in the opening, or omit for none.",
                )),
        )
        .subcommand(sitting("start", "start the first sitting with the opening"))
        .subcommand(sitting("resume", "continue the same actor session in another sitting"))
        .subcommand(
            Command::new("status").about("show the opening, sittings, and ending").arg(path("episode").required(true)),
        )
        .subcommand(
            Command::new("close")
                .about("record the producer’s ending and collect the return")
                .arg(path("episode").required(true))
                .arg(Arg::new("reason").long("reason").required(true)),
        )
        .subcommand(
            Command::new("verify")
                .about("identify the installation or verify an episode record")
                .arg(path("record"))
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
                .arg(path("records").required(true).num_args(1..))
                .arg(Arg::new("question").long("question").required(true))
                .arg(path("output").long("output").required(true)),
        )
        .subcommand(Command::new("codex-hooks").about("print the Codex hook entries for review and installation"))
        .subcommand(Command::new("capture").hide(true).arg(Arg::new("actor").required(true)))
}

fn one<'a>(matches: &'a ArgMatches, name: &str) -> &'a PathBuf {
    matches.get_one::<PathBuf>(name).expect("a required path")
}

fn word<'a>(matches: &'a ArgMatches, name: &str) -> Option<&'a str> {
    matches.get_one::<String>(name).map(String::as_str)
}

/// Run one command. A sitting also reports the status the process should end with.
fn run(name: &str, matches: &ArgMatches) -> Result<(Option<Value>, u8)> {
    let result = match name {
        "capture" => {
            capture::main(word(matches, "actor").unwrap_or_default())?;
            return Ok((None, 0));
        }
        "template" => episodes::install_template(one(matches, "destination"))?,
        "open" => {
            let jobs: Vec<PathBuf> = matches.get_many::<PathBuf>("job").into_iter().flatten().cloned().collect();
            let actor = word(matches, "actor").unwrap_or_default();
            episodes::open_episode(one(matches, "episode"), one(matches, "project"), actor, &jobs)?
        }
        "start" | "resume" => {
            let opening = name == "start";
            let (request, prompt) = (word(matches, "request"), word(matches, "prompt"));
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
            let sitting = episodes::launch(one(matches, "episode"), request, prompt, !unattended, opening)?;
            let code = match sitting["exit"].as_i64() {
                _ if json::truthy(&sitting["error"]) => 1,
                Some(code) if code < 0 => (128 - code) as u8,
                Some(code) => code as u8,
                None => 0,
            };
            return Ok((Some(sitting), code));
        }
        "status" => episodes::status(one(matches, "episode"))?,
        "close" => episodes::close(one(matches, "episode"), word(matches, "reason").unwrap_or_default())?,
        "verify" => match (matches.get_one::<PathBuf>("record"), word(matches, "sha256")) {
            (Some(record), expected) => records::verify(record, expected)?,
            (None, Some(_)) => return refuse("--sha256 needs a record directory"),
            (None, None) => installation::verify()?,
        },
        "review" => {
            let records: Vec<PathBuf> = matches.get_many::<PathBuf>("records").into_iter().flatten().cloned().collect();
            records::review(&records, word(matches, "question").unwrap_or_default(), one(matches, "output"))?
        }
        "codex-hooks" => capture::hook_settings(Harness::Codex)?,
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
