# Operating an episode

Write each additional job in its own UTF-8 file. Describe what should catch the actor’s attention during its work, what to do while the episode continues, and what to leave for the producer. `berman template` supplies an editable `job.md` and two independent examples. With `note-taking.md` the actor adds occasional accounts under `return/notes/`. With `handover.md` the actor keeps `return/HANDOVER.md` current for someone picking up the work. An episode can also have no additional job.

## Open and start

Pass one `--job FILE` for each job you want the actor to take on, or none. For a handover alone, use `--job "$HOME/jobs/handover.md"`. This opening asks for both examples.

```sh
berman open EPISODE --project CHECKOUT --actor codex \
  --job "$HOME/jobs/handover.md" --job "$HOME/jobs/note-taking.md"
```

Berman creates a private episode directory and fixes the selected instructions in the order given. Repeating the same open returns the existing episode. Read `EPISODE/opening.md` to see what the actor receives first: the episode, the absolute return path, and any additional jobs. The opening names no project task. Project artifacts belong in their ordinary locations.

`berman start EPISODE` starts the interactive CLI in the checkout and delivers the opening. Add `--prompt` to follow the opening with your first message. Berman inherits the actor’s normal settings and passes the return directory as an additional writable location. The Linux account must already have access to the project and the chosen harness. The producer can use a dedicated harness home by setting `CODEX_HOME` or `CLAUDE_CONFIG_DIR` before opening the episode. Berman binds that home at the opening.

## Set up the actor

Sign the actor in through its own CLI, in the harness home the episode will bind. Berman does not handle credentials. Over SSH or in another login without a browser, `codex login --device-auth` prints a code to enter in a browser on another computer. For Claude Code, run `claude auth login` and follow its prompts. `codex login status` and `claude auth status` show whether a home is signed in. A dedicated home keeps an episode’s hooks and permissions apart from everyday use and needs its own sign-in.

```sh
export CODEX_HOME="$HOME/.codex-berman" CLAUDE_CONFIG_DIR="$HOME/.claude-berman"
mkdir -p "$CODEX_HOME"
codex login --device-auth
claude auth login
```

**Claude Code.** Berman supplies a fresh native session ID and a hook settings file to the launched process. In an attended sitting, Claude Code can ask whether to trust the checkout and the return directory. Print mode does not ask.

**Codex.** Run `berman codex-hooks` from the installed environment. Review the printed command and merge its four groups into the corresponding events in the Linux Codex home’s `hooks.json`, preserving existing handlers. For a new home with no hook file:

```sh
mkdir -p "${CODEX_HOME:-$HOME/.codex}"
( set -C; berman codex-hooks > "${CODEX_HOME:-$HOME/.codex}/hooks.json" )
```

Open the actual Codex CLI in that home and review the entries with `/hooks`. Review them again if you change the installed path or commands. Berman starts the interactive CLI with `--no-daemon` so its hooks receive the episode’s environment. Berman binds the native session when the first sitting’s message arrives as a prompt submitted in the checkout. During ordinary work the hook has no episode and returns immediately. An unreviewed or unavailable hook can leave a sitting without a transcript or a resumable Codex ID.

## Unattended sittings

Add `--unattended` and a `--prompt` to `start` or `resume` to send a message without opening the actor’s interactive interface.

```sh
berman start EPISODE --unattended --prompt "What state is the release branch in?"
berman resume EPISODE --request notes-draft --unattended --prompt "Draft release notes for it."
```

Berman runs `claude --print` or `codex exec` in the checkout with no terminal input, waits for it to exit, and prints the sitting. A first sitting delivers the opening followed by the message. Read these fields before deciding what to say next:

- `response` is the final message the actor’s CLI reported. It is `null` when the native output has none, reports an error, or shows an unfinished turn. Berman does not compose one.
- `denials` lists the tool uses Claude Code reports it denied. Codex tells only the actor when its sandbox or approval policy refuses an operation, so for Codex `denials` is `null`. Read its response and the transcript for what was blocked.
- `limitations` names what Berman could not read or confirm, such as a missing result, a session other than the bound one, or a nonzero exit with the last line of error output.
- `exit` is the process status, and `stdout` and `stderr` name the native output kept beside the sitting record.

A zero exit does not mean the work is finished, and a response can be partial, mistaken or a question. Files the actor leaves under `return/` stay separate from the response. Callbacks capture the transcript as in an attended sitting.

No person answers permission prompts during an unattended sitting. The actor works under its own configuration, and Berman adds no permissions. In Claude Code, anything that would ask for approval is denied and listed, and the actor carries on without it. Codex applies its configured sandbox and approval policy; `codex exec` uses a read-only sandbox unless its configuration says otherwise. For an actor to edit the project or leave files in the return directory without a person, its own settings must already allow those edits. Examples are Claude Code’s `acceptEdits` mode or an allow rule, and Codex’s `workspace-write` sandbox.

An unattended Claude Code sitting uses the episode’s session ID. An unattended Codex sitting names its session in its output. If no reviewed hook bound the first sitting, Berman binds the session that output names. Codex still needs the hook for telemetry during sittings.

Attendance belongs to each sitting. After an attended sitting you can send an unattended message, and the reverse, in the same native session. Berman starts a sitting only when none is running. It cannot join a running sitting or pass messages into one.

## Resume and close

```sh
berman resume EPISODE --request after-lunch
berman resume EPISODE --request after-review --prompt "Include the tradeoff you discussed."
berman status EPISODE
berman close EPISODE --reason "The team has decided how to proceed."
```

Each distinct resume request identifies one sitting of the same native session. Reuse its label, prompt and attendance when retrying. Repeating a request returns the recorded sitting and never starts the actor again. Repeating start also returns the first sitting. Keep sitting requests short and meaningful. A missing exit means the launcher did not observe it; inspect the native process before continuing. If the launcher stopped during an unattended sitting, repeat the same request after the actor exits to read what its output reports. Concurrent starts and closeouts refuse while a sitting holds the episode lock. The Codex CLI does not keep the lock it inherits, so Berman also refuses while the process a sitting recorded is still running. An interrupt from the terminal reaches the actor, and Berman records the resulting exit. Claude Code can exit with status 0 after an interrupt; its result then reports the interruption as an error. A native exit leaves the episode open.

The checkout can change between any two Berman commands. Other people, sessions and tools can work in it without Berman, and their changes do not invalidate the episode. A sitting needs only the checkout, the actor’s CLI and, when resuming, the bound native session. Berman refuses a sitting while the checkout is missing and records any native failure to resume. If the first sitting ends before the actor’s CLI starts a session, for example because the CLI cannot reach its service, there is no session to resume. `berman start EPISODE --request LABEL` delivers the opening again, attended or unattended. Berman refuses another opening once it knows of a native session: Codex’s is bound, or Claude Code’s transcript exists. A Codex session also stays unbound when the episode hook did not run, so check the hook before starting again. Sessions that Berman did not launch stay outside the episode, even in the same checkout and harness home.

Closing records the producer’s reason, closes callback intake, and publishes `EPISODE/record`. A close interrupted during publication can be repeated with the original reason. Its output includes the manifest digest. Keep that digest separately when you need to verify a copy against the record you received.
