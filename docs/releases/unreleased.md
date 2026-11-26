# Unreleased

The latest release is [Berman 0.26.9](0.26.9.md).

Berman is now one native program for Linux and macOS, and it keeps each producer’s episodes and reviews in an encrypted library. This replaces the Python package and its plaintext episode directories. Install the program, run `berman library init` once, and name episodes by a place in the library, such as `episodes/cutover`. [Installation and first episode](../../README.md)

The person who opens and closes episodes is now the producer throughout the help, refusals, templates and guides.

## The producer’s library

Episodes and reviews are directories and files inside one encrypted file in the login’s application data. Their names and contents, the project paths they record, and the journals written while a command runs are all encrypted, so a search of the disk by filename or phrase finds none of it. `berman library` lists, reads, creates, copies, renames, removes, takes files in from disk and exports them, and `berman help library` shows an example of each. The key is kept in the login keychain, a Secret Service, or a file only the login can read. [The producer’s library](../library.md)

`berman open DESTINATION` takes a new library path and prints the episode with its stable ID. `start`, `resume`, `status`, `close`, `verify` and `review` take that ID or the directory’s current path, so an open episode can be renamed or moved. An episode’s sittings and telemetry are readable in its directory from the first sitting. `berman verify` and `berman review` also take a directory on disk, written as a path that begins with `/`, `./`, `../` or `~/`, and `--output` for a review is a library path. A failed verification now names the members that are missing, changed or unlisted.

A record has the same members and manifest format as before. `berman library export` writes one as an ordinary directory, which verifies on either system. `episode.json` drops `directory`, adds the `return` path, and has the schema `berman.episode/2`, so 0.26.9 does not verify a record made by this release. This release verifies and imports records made by 0.26.9.

## The return directory

The actor’s return directory is a real directory outside the checkout, the harness home and the library, with the same path at every sitting. Its parent can be entered but not listed. Closing copies its regular files into the episode under `return/`, publishes and re-reads the record, and then removes what it collected and the directory itself. A file that changed, could not be read, would pass a limit, is a link, or arrived late stays in place, and `close` and `status` list it under `preserved`. Repeating a close with the original reason finishes an interrupted one. The limits of 4,096 files and 1 GiB now apply to the return alone: sittings and telemetry are already in the episode and are never left out to fit.

`berman harness-home ACTOR` prints a dedicated home for an actor’s CLI under a parent that cannot be listed, for producers who want the CLI’s own session history out of broad searches as well. [Actor setup](../operating.md#set-up-the-actor)

## Unattended sittings

`berman start` and `berman resume` take `--unattended` with a `--prompt` message. The actor runs through `claude --print` or `codex exec` without its interactive interface, and Berman prints the sitting with the response the CLI reported, the denials Claude Code reported, and the limitations Berman found. The native output goes into the library as it arrives and is kept with the sitting record. Attendance belongs to each sitting, so attended and unattended sittings can alternate in one native session. Berman adds no permissions to an unattended actor. [Unattended sittings](../operating.md#unattended-sittings)

`--job` is now optional, and the opening no longer tells the actor to continue its ordinary work. It names the episode, Berman’s record and the return directory, followed by any additional jobs. `berman start --prompt` follows the opening with a first message in either attendance.

Repeating an unattended request after the launcher stopped reads what the actor’s output reports without starting it again. Because the Codex CLI drops the episode lock it inherits, a sitting records when its actor process started, and later sittings, retries and closeouts refuse while that process is still running, on Linux and on macOS. A sitting refuses while the checkout is missing. If no reviewed hook bound the first unattended Codex sitting, Berman binds the session that `codex exec` names in its output. The record’s coverage lists each sitting’s limitations and states that the Git observations show the checkout at two times, not who changed it. Both Git observations carry the time they were made.

When the first sitting ends before the actor’s CLI starts a session, `berman start EPISODE --request LABEL` delivers the opening again. Berman refuses another opening once it knows of a native session, and `resume` refuses while it knows of none.

A callback that arrives before the actor’s CLI has written its transcript, as Claude Code’s first callbacks do, notes that instead of appearing in the record’s coverage as a gap. A transcript missing after one has been captured is still a gap.

## Capture

Each callback publishes the transcript in one transaction, so an interrupted callback leaves the last published version intact and verifiable. A growing transcript shares its stored bytes with earlier versions. Transcripts are read and rendered in bounded pieces, and a callback on a 250 MiB transcript used about 25 MiB of memory in testing. Numbers in callback events keep the spelling the actor’s CLI wrote. If the captured transcript no longer agrees with its own description at closing, the coverage says so and its members are kept as found, where 0.26.9 left them out of the record.

## Coming from 0.26.9

The Codex hook command is now `berman capture codex`. Run `berman codex-hooks`, replace the entries in each Codex home’s `hooks.json`, and review them with `/hooks`. Until then the old entries keep working for episodes opened with 0.26.9 while that installation remains.

Close episodes opened with 0.26.9 using 0.26.9. `berman legacy DIRECTORY` lists the plaintext episode, record and review directories below a directory. `berman import` verifies a record or review directory and copies it into the library with its original manifest, leaving the input as it was. [Records from earlier releases](../library.md#records-from-earlier-releases)

## Development

The application is Rust under `src/berman/`, with SQLCipher embedded in both builds. The checks are `cargo fmt`, `cargo clippy` and `cargo test`. The Mac holds the checkout, and the Lima guest builds and tests a commit from a source archive without a clone or repository credentials. `development/release/build` makes a release’s source archive, tested binaries for both systems and their digests from one commit. [Developing Berman](../development.md)
