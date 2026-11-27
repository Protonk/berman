# Operating an episode

Write each additional job in its own UTF-8 file. Describe what should catch the actor’s attention during its work, what to do while the episode continues, and what to leave for the producer. `berman template` supplies an editable `job.md` and two independent examples. With `note-taking.md` the actor adds occasional accounts under `notes/` in the return directory. With `handover.md` the actor keeps `HANDOVER.md` current there for someone picking up the work. An episode can also have no additional job.

## Open and start

Pass one `--job FILE` for each job you want the actor to take on, or none. For a handover alone, use `--job "$HOME/jobs/handover.md"`. This opening asks for both examples.

```sh
berman open episodes/cutover --project CHECKOUT --actor codex \
  --job "$HOME/jobs/handover.md" --job "$HOME/jobs/note-taking.md"
```

The destination is a new directory in the producer’s library, outside any other episode’s directory. Berman fixes the selected instructions in the order given and prints the episode with its ID. Every later command takes that ID or the directory’s current path, so an episode can be renamed or moved in the library while it is open. Repeating the same open returns the existing episode. Read the opening with `berman library cat episodes/cutover/opening.md` to see what the actor receives first: the episode, the absolute path of the return directory, and any additional jobs. The opening names no project task. Project artifacts belong in their ordinary locations.

The return directory is a real, initially empty directory outside the checkout, the harness home and the library. The actor can write `notes/`, update `HANDOVER.md`, or leave nothing, and finds the same path at every sitting. Its parent can be entered but not listed, so a search of the home directory does not come across the returns of open episodes. Anyone who has the path can read it. `berman status` prints it. The path is fixed at the opening and does not follow later changes to the login’s environment.

`berman start EPISODE` starts the interactive CLI in the checkout and delivers the opening. Add `--prompt` to follow the opening with your first message. Berman inherits the actor’s normal settings and passes the return directory as an additional writable location. The login must already have access to the project and the chosen harness. Berman binds the harness home at the opening: the one `CODEX_HOME` or `CLAUDE_CONFIG_DIR` names then, or the CLI’s default.

## Set up the actor

Sign the actor in through its own CLI, in the harness home the episode will bind. Berman does not handle credentials. Over SSH or in another login without a browser, `codex login --device-auth` prints a code to enter in a browser on another computer. For Claude Code, run `claude auth login` and follow its prompts. `codex login status` and `claude auth status` show whether a home is signed in.

Each CLI keeps its own history of a session, which repeats the opening and what was said. Codex keeps it under `sessions/` in its home and Claude Code under `projects/`. In the everyday home, `~/.codex` or `~/.claude`, a search of the home directory finds that text. `berman harness-home ACTOR` prints a dedicated home for that actor, the same one each time, under a parent that can be entered but not listed. A dedicated home also keeps an episode’s hooks and permissions apart from everyday use. It needs its own sign-in.

```sh
export CODEX_HOME="$(berman harness-home codex)" CLAUDE_CONFIG_DIR="$(berman harness-home claude)"
codex login --device-auth
claude auth login
```

**Claude Code.** Berman supplies a fresh native session ID and a hook settings file to the launched process. In an attended sitting, Claude Code can ask whether to trust the checkout and the return directory. Print mode does not ask.

**Codex.** Run `berman codex-hooks` with the installed program. Review the printed command and merge its four groups into the corresponding events in the Codex home’s `hooks.json`, preserving existing handlers. For a new home with no hook file:

```sh
mkdir -p "${CODEX_HOME:-$HOME/.codex}"
( set -C; berman codex-hooks > "${CODEX_HOME:-$HOME/.codex}/hooks.json" )
```

Open the actual Codex CLI in that home and review the entries with `/hooks`. Review them again if you move the installed program: the hook names its path and no episode. Berman starts the interactive CLI with `--no-daemon` so its hooks receive the episode’s environment. Berman binds the native session when the first sitting’s message arrives as a prompt submitted in the checkout. During ordinary work the hook has no episode and returns immediately, without opening the library. An unreviewed or unavailable hook can leave a sitting without a transcript or a resumable Codex ID.

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
- `exit` is the process status, and `stdout` and `stderr` name the native output kept with the sitting record.
- `transcript` says when Berman read the session’s transcript as the sitting ended. It is `null` when the session has none yet, and gives the reason when the transcript could not be read. Attended sittings have it too.

A zero exit does not mean the work is finished, and a response can be partial, mistaken or a question. Files the actor leaves in the return directory stay separate from the response. Callbacks capture the transcript as in an attended sitting.

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

Each distinct resume request identifies one sitting of the same native session. Reuse its label, prompt and attendance when retrying. Repeating a request returns the recorded sitting and never starts the actor again. Repeating start also returns the first sitting. Keep sitting requests short and meaningful. A missing exit means the launcher did not observe it; inspect the native process before continuing. An unattended actor’s output goes into the library as it arrives, through a process that outlives the launcher. If the launcher stopped during an unattended sitting, repeat the same request after the actor exits to read what its output reports. Concurrent starts and closeouts refuse while a sitting holds the episode. The Codex CLI does not keep the lock it inherits, so Berman also refuses while the process a sitting recorded is still running. An interrupt from the terminal reaches the actor, and Berman records the resulting exit. Claude Code can exit with status 0 after an interrupt; its result then reports the interruption as an error. A native exit leaves the episode open, with its return directory in place.

Berman reads the session’s transcript at each callback and once more as each sitting ends. It does not read it between sittings or at closing. If you resume the bound native session yourself, outside Berman, that exchange is not collected unless a later sitting follows, whose transcript then includes it. The one exception is a sitting whose end Berman did not observe: closing then reads the transcript, and the coverage says that it can hold what was said afterwards.

The checkout can change between any two Berman commands. Other people, sessions and tools can work in it without Berman, and their changes do not invalidate the episode. A sitting needs only the checkout, the actor’s CLI and, when resuming, the bound native session. Berman refuses a sitting while the checkout is missing and records any native failure to resume. If the first sitting ends before the actor’s CLI starts a session, for example because the CLI cannot reach its service, there is no session to resume. `berman start EPISODE --request LABEL` delivers the opening again, attended or unattended. Berman refuses another opening once it knows of a native session: Codex’s is bound, or Claude Code’s transcript exists. A Codex session also stays unbound when the episode hook did not run, so check the hook before starting again. Sessions that Berman did not launch stay outside the episode, even in the same checkout and harness home.

Closing records the producer’s reason. From then no sitting starts and no callback is taken in, whatever happens to the files in the episode’s directory. It copies each regular file of the return directory into the episode under `return/` with its original name and bytes, and publishes the record. Its output includes the manifest digest. Keep that digest separately when you need to verify a copy against the record you received.

Nothing leaves the return directory until the committed record has been read back and verified. If it does not verify, for example because a member was moved out of the episode’s directory, the return stays whole, and repeating the close collects and publishes again. Once it has verified, closing removes the files it collected and then the directory itself. Each file is first moved aside and judged by its bytes there, so a version saved under the same name in the meantime is never the one removed, and nothing is done through a symbolic link. A file that changed during collection, could not be read, would pass a limit, is a link, or arrived afterwards stays where it is. `preserved` lists those paths, in the output of `close` and of `status`, and the coverage names what was omitted. A version cleanup set aside and found not to be the collected one is returned to its name, or beside a newer file as `NAME.earlier-1`. Move or delete them and close again with the same reason to finish. A close interrupted at any point can be repeated with the original reason and arrives at the same ending and record. Cleanup removes a file only while the record still holds its copy, so removing a member from the record first keeps the file on disk.
