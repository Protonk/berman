# Berman

A **producer** uses Berman for intermittent project management by correspondence. Most days an actor can open the software project in its usual environment and get on with the work. When the producer chooses that route, Berman supplies no instructions and collects no telemetry. When something deserves attention alongside the work, the producer opens an **episode**.

The **actor** is the agent working in the project, currently through Codex or Claude Code. Berman does not need to know what that work is or where it came from. At the opening, Berman tells the actor about the episode, names a place to leave a response, and gives it any additional jobs the producer selected. The producer might ask for occasional notes, a handover kept current, both, or nothing beyond the conversation itself. Asking for that attention is a deliberate intervention. It can change how the actor spends its time or which decisions it makes, and its effect on the project is an open question. The actor can continue independently, talk with the producer, or consult someone else. The opening matters because it might be the only exchange they have.

An episode can span several sittings. The producer can attend a sitting in the actor’s interactive CLI, or send a message for an unattended sitting and read the response Berman reports. Between sittings, people, other sessions and tools can change the project without going through Berman. The producer decides when the episode ends and supplies the reason. Berman collects what the actor deliberately left, along with the telemetry it could attribute to the episode. A producer returning to the project can read both, see gaps in the record, and decide what deserves another episode.

## Install on Linux or macOS

Berman is one program. Install it in the login that has Git, the project, and the actor’s usual CLI. A release carries a build for Linux on arm64 and one for macOS on Apple silicon, with their SHA-256 digests.

```sh
tar -xzf berman-VERSION-TARGET.tar.gz
install -d "$HOME/.local/bin"
install -m 0755 berman-VERSION-TARGET/berman "$HOME/.local/bin/berman"
berman verify
```

To build from the software checkout, use the Rust toolchain that `rust-toolchain.toml` names, with a C compiler, `make` and `perl` for the embedded storage engine. [Developing Berman](docs/development.md)

```sh
cargo install --locked --path . --root "$HOME/.local"
```

Then create the producer’s library, once for each login. Berman keeps episodes and reviews as directories and files inside one encrypted file, so their names and contents do not appear on disk outside it. The key goes in the login keychain on macOS or a Secret Service on Linux. On a Linux login without one, such as a server reached over SSH, pass `--key file` to keep it in a file only that login can read. [The producer’s library](docs/library.md)

```sh
berman library init
```

## Try an episode

Run `berman help` for the command list or `berman help open` for the opening arguments.

Choose the checkout the actor will work in and a place in the library for the episode. This example asks only for notes. For a handover alone, pass `handover.md` as the job. To request both, repeat `--job` with the second file. Omit `--job` for an episode without an additional job. The template also supplies `job.md` for writing your own instructions.

```sh
berman template "$HOME/jobs"
berman open episodes/notes --project "$HOME/code/my-project" \
  --actor claude --job "$HOME/jobs/note-taking.md"
berman library cat episodes/notes/opening.md
berman start episodes/notes
```

Read the opening before starting. Launch from an ordinary interactive terminal and work with the actor as usual. The actor gets the project’s normal instructions and permissions, plus write access to the episode’s return directory, a real directory outside the checkout that the opening names. For Codex, set up its hook before the first sitting. [Actor setup and resumption](docs/operating.md)

To correspond without the interactive CLI, send a message for an unattended sitting. Berman waits for the actor and prints the sitting, including the `response` the actor’s CLI reported and any `limitations`. Read it and decide what to say next.

```sh
berman open episodes/survey --project "$HOME/code/my-project" --actor codex
berman start episodes/survey --unattended --prompt "Which parts of the test suite are slowest?"
berman resume episodes/survey --request profile --unattended \
  --prompt "Profile the slowest one and tell me what you find."
```

Running `claude -p` or `codex exec` directly remains ordinary work outside Berman. [Unattended sittings](docs/operating.md#unattended-sittings)

After a sitting, decide whether to resume or close. Supply the actual reason when closing. Closing moves what the actor left into the episode and removes the return directory.

```sh
berman status episodes/notes
berman close episodes/notes --reason "I have the account I need for the handoff."
berman verify episodes/notes
berman library ls episodes/notes/return
```

Read `opening.md`, then the actor’s files under `return/` if present, with `berman library cat`. Unattended responses stay in their sitting records under `sittings/`. `coverage.json` describes the scope and gaps. To work through a question about one or more episodes:

```sh
berman review episodes/notes \
  --question "Which decision should I revisit with the team, and why?" \
  --output reviews/handoff
berman library export reviews/handoff "$HOME/handoff"
```

The review holds your question, verified copies of the selected records, and an answer file for you or a reader you choose. The export writes it as an ordinary directory for that reader. [Reading and verifying records](docs/records.md) · [MIT License](LICENSE)
