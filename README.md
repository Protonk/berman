# Berman

Berman supports intermittent management of work in a software project. Most days an actor can open the project in its usual environment and get on with the work. When the manager chooses that route, Berman supplies no instructions and collects no telemetry. When something deserves attention alongside the work, the manager opens an **episode**.

The **actor** is the agent doing the ordinary work, currently through Codex or Claude Code. At the opening, Berman gives the actor the manager’s additional instructions and a place to leave a response. The manager might ask for occasional notes, a handover kept current, or both. Asking for that attention is a deliberate intervention. It can change how the actor spends its time or which decisions it makes, and its effect on the project is an open question. The actor can continue independently, talk with the manager, or consult someone else. The opening matters because it might be the only exchange they have.

An episode can span several sittings. The manager decides when it ends and supplies the reason. Berman collects what the actor deliberately left, along with the telemetry it could attribute to the episode. A manager returning to the project can read both, see gaps in the record, and decide what deserves another episode.

## Install on Linux

Use Python 3.13 or later, Git, and the actor’s usual CLI and login. From the software checkout:

```sh
python3 -m venv "$HOME/.local/share/berman/venv"
"$HOME/.local/share/berman/venv/bin/python" -m pip install .
export PATH="$HOME/.local/share/berman/venv/bin:$PATH"
berman verify
```

The checkout, actor, and episode files share a Linux environment. A terminal on another computer can reach it over SSH. [Development on a Mac with Lima and rootless Podman](apparatus/lima/README.md)

## Try an episode

Choose a checkout where ordinary work is already under way. Keep the episode directory outside the checkout and the actor’s configuration directory. This example asks only for notes. For a handover alone, pass `handover.md` as the job. To request both, repeat `--job` with the second file. The template also supplies `job.md` for writing your own instructions.

```sh
berman template "$HOME/jobs"
berman open "$HOME/episodes/notes" --project "$HOME/code/my-project" \
  --actor claude --job "$HOME/jobs/note-taking.md"
berman start "$HOME/episodes/notes"
```

Read the opening before starting. Launch from an ordinary interactive terminal and work with the actor as usual. The actor gets the project’s normal instructions and permissions, plus write access to the episode’s return directory. For Codex, set up the Linux hook before the first sitting. [Actor setup and resumption](docs/operating.md)

After the sitting, decide whether to resume or close. Supply the actual reason when closing.

```sh
berman status "$HOME/episodes/notes"
berman close "$HOME/episodes/notes" --reason "I have the account I need for the handoff."
berman verify "$HOME/episodes/notes/record"
```

Open `record/opening.md`, then read the actor’s files under `record/return/` if present. `record/coverage.json` describes the scope and gaps. To work through a question about one or more episodes:

```sh
berman review "$HOME/episodes/notes/record" \
  --question "Which decision should I revisit with the team, and why?" \
  --output "$HOME/reviews/handoff"
```

The chosen directory contains your question, verified copies of the selected sources, and an answer file for you or a reader you choose. [Reading and verifying records](docs/records.md) · [Developing Berman](docs/development.md) · [MIT License](LICENSE)
