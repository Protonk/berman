# Operating an episode

Write an additional job in an ordinary UTF-8 file. Describe what should catch the actor’s attention during ordinary work, what to do while the episode continues, and what to leave for the manager. `berman template` supplies an editable `job.md` and two complete examples. With `note-taking.md` the actor adds accounts as work proceeds. With `handover.md` the actor revises one document so someone arriving at any point can pick up the work. Pass the chosen file to `open --job`.

## Open and start

`berman open EPISODE --project CHECKOUT --actor codex --job FILE` creates a private episode directory and fixes the opening instructions. Repeating the same open returns the existing episode. The actor receives the additional job in the opening message with the absolute return path. Read `EPISODE/opening.md` to inspect it. Project artifacts belong in their ordinary locations.

`berman start EPISODE` starts the interactive CLI in the checkout. Berman inherits the actor’s normal settings and passes the return directory as an additional writable location. The Linux account must already have access to the project and the chosen harness. The operator can use a dedicated harness home by setting `CODEX_HOME` or `CLAUDE_CONFIG_DIR` before opening the episode. Berman binds that home at the opening.

## Set up the actor

**Claude Code.** Berman supplies a fresh native session ID and a hook settings file to the launched process. Sign in to Claude Code through its ordinary interactive CLI.

**Codex.** Run `berman codex-hooks` from the installed environment. Review the printed command and merge its four groups into the corresponding events in the Linux Codex home’s `hooks.json`, preserving existing handlers. For a new home with no hook file:

```sh
mkdir -p "${CODEX_HOME:-$HOME/.codex}"
( set -C; berman codex-hooks > "${CODEX_HOME:-$HOME/.codex}/hooks.json" )
```

Open the actual Codex CLI, sign in, and review the entries with `/hooks`. Review them again if you change the installed path or commands. Berman starts the episode CLI with `--no-daemon` so its hooks receive the episode’s environment. The opening event must match the commissioned prompt and checkout before Berman binds the native session. During ordinary work the hook has no episode and returns immediately. An unreviewed or unavailable hook can leave a sitting without a transcript or a resumable Codex ID.

## Resume and close

```sh
berman resume EPISODE --request after-lunch
berman resume EPISODE --request after-review --prompt "Include the tradeoff you discussed."
berman status EPISODE
berman close EPISODE --reason "The team has decided how to proceed."
```

Each distinct resume request identifies one sitting of the same native session. Reuse its label and prompt when retrying. Repeating start also returns the first sitting. Keep sitting requests short and meaningful. A missing exit means the launcher did not observe it; inspect the native process before continuing. Concurrent starts and closeouts refuse while a sitting holds the episode lock. A native exit leaves the episode open.

Closing records the manager’s reason, closes callback intake, and publishes `EPISODE/record`. A close interrupted during publication can be repeated with the original reason. Its output includes the manifest digest. Keep that digest separately when you need to verify a copy against the record you received.
