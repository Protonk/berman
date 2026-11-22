# Berman

A **producer** uses Berman for intermittent project management by correspondence. Most days an actor can open the software project in its usual environment and get on with the work. When the producer chooses that route, Berman supplies no instructions and collects no telemetry. When something deserves attention alongside the work, the producer opens an **episode**.

The **actor** is the agent working in the project, currently through Codex or Claude Code. Berman does not need to know what that work is or where it came from. At the opening, Berman tells the actor about the episode, names a place to leave a response, and gives it any additional jobs the producer selected. The producer might ask for occasional notes, a handover kept current, both, or nothing beyond the conversation itself. Asking for that attention is a deliberate intervention. It can change how the actor spends its time or which decisions it makes, and its effect on the project is an open question. The actor can continue independently, talk with the producer, or consult someone else. The opening matters because it might be the only exchange they have.

An episode can span several sittings. The producer can attend a sitting in the actor’s interactive CLI, or send a message for an unattended sitting and read the response Berman reports. Between sittings, people, other sessions and tools can change the project without going through Berman. The producer decides when the episode ends and supplies the reason. Berman collects what the actor deliberately left, along with the telemetry it could attribute to the episode. A producer returning to the project can read both, see gaps in the record, and decide what deserves another episode.

## Install on Linux

Use Python 3.13 or later, Git, and the actor’s usual CLI and login. From the software checkout:

```sh
python3 -m venv "$HOME/.local/share/berman/venv"
"$HOME/.local/share/berman/venv/bin/python" -m pip install .
export PATH="$HOME/.local/share/berman/venv/bin:$PATH"
berman verify
```

The checkout, actor, and episode files share a Linux environment. A terminal on another computer can reach it over SSH. [Development on a Mac with Lima and rootless Podman](development/lima/README.md)

## Try an episode

Run `berman help` for the command list or `berman help open` for the opening arguments.

Choose the checkout the actor will work in. Keep the episode directory outside the checkout and the actor’s configuration directory. This example asks only for notes. For a handover alone, pass `handover.md` as the job. To request both, repeat `--job` with the second file. Omit `--job` for an episode without an additional job. The template also supplies `job.md` for writing your own instructions.

```sh
berman template "$HOME/jobs"
berman open "$HOME/episodes/notes" --project "$HOME/code/my-project" \
  --actor claude --job "$HOME/jobs/note-taking.md"
berman start "$HOME/episodes/notes"
```

Read the opening before starting. Launch from an ordinary interactive terminal and work with the actor as usual. The actor gets the project’s normal instructions and permissions, plus write access to the episode’s return directory. For Codex, set up the Linux hook before the first sitting. [Actor setup and resumption](docs/operating.md)

To correspond without the interactive CLI, send a message for an unattended sitting. Berman waits for the actor and prints the sitting, including the `response` the actor’s CLI reported and any `limitations`. Read it and decide what to say next.

```sh
berman open "$HOME/episodes/survey" --project "$HOME/code/my-project" --actor codex
berman start "$HOME/episodes/survey" --unattended --prompt "Which parts of the test suite are slowest?"
berman resume "$HOME/episodes/survey" --request profile --unattended \
  --prompt "Profile the slowest one and tell me what you find."
```

Running `claude -p` or `codex exec` directly remains ordinary work outside Berman. [Unattended sittings](docs/operating.md#unattended-sittings)

After a sitting, decide whether to resume or close. Supply the actual reason when closing.

```sh
berman status "$HOME/episodes/notes"
berman close "$HOME/episodes/notes" --reason "I have the account I need for the handoff."
berman verify "$HOME/episodes/notes/record"
```

Open `record/opening.md`, then read the actor’s files under `record/return/` if present. Unattended responses stay in their sitting records under `record/sittings/`. `record/coverage.json` describes the scope and gaps. To work through a question about one or more episodes:

```sh
berman review "$HOME/episodes/notes/record" \
  --question "Which decision should I revisit with the team, and why?" \
  --output "$HOME/reviews/handoff"
```

The chosen directory contains your question, verified copies of the selected sources, and an answer file for you or a reader you choose. [Reading and verifying records](docs/records.md) · [Developing Berman](docs/development.md) · [MIT License](LICENSE)
