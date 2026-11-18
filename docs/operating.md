# Operating a study

An engagement is one fieldworker's assigned work in one project, from the
researcher's commission to the researcher's ending. The controller in the Linux
guest commissions it, records its ending and keeps custody of what was
collected. The executor on macOS prepares the worker's packet, launches the
harness and stages its observations. Nothing else in Berman prepares, launches,
captures, ends, collects, preserves or supplements fieldwork.

The operator works from an ordinary macOS terminal with `berman-research`. It
is the same program as `berman remote` and takes the same arguments.

## Finding the next command

```sh
berman cheatsheet
```

The cheatsheet asks the guest what is recorded and prints, for each unfinished
engagement, the command that moves it forward. It also prints the commission
command for each bound connection, the checkout for ordinary development and
the commands that verify a copy. It writes nothing. Words in capitals, such as
`LABEL` and `REASON`, are placeholders.

A study is selected by its connector settings. The default file is
`~/.config/berman/connector.json`; `BERMAN_CONNECTOR` names another, and so does
`--config FILE`, which comes before the command: `berman-research --config FILE status`.
The cheatsheet writes `--config` into every command it prints for a study that
is not the default. [Connector settings](guest-deployment.md#connector-settings-on-macos)

## An engagement, in order

```sh
berman-research commission --request first-account --connection example --harness claude --recipe baseline
berman-research prepare run-001
berman-research launch run-001
berman-research resume run-001 --request second
berman-research closeout run-001 --reason 'The researcher ended fieldwork after the project review'
```

| Step | Who | What happens |
| --- | --- | --- |
| `commission` | Operator | The guest reads the connection and recipe, allocates the run number and returns the assignment. Omit `--recipe` for the connection's default, or pass `--plan NAME` for a plan's next step. |
| `prepare RUN` | Operator | The executor places the packet in its spool, snapshots the selected documents and runs the project checks. It prints the packet path and the launch command. Review the packet before launch. |
| `launch RUN` | Researcher, in an ordinary terminal | Starts the first sitting and waits for the harness to exit. Repeating it never starts a second process. |
| `resume RUN --request LABEL` | Researcher | Starts a further sitting of the same engagement and resumes its native session. Each sitting has its own label. |
| `closeout RUN --reason TEXT` | Operator, once the researcher has declared the ending | Records the ending, freezes the host collection, transfers it, and has the guest accept, preserve, publish and verify it. |

A request label makes a lost reply safe: repeat the same command with the same
label and the guest returns the original result. A new decision takes a new
label.

An engagement lasts until the researcher ends it, however many times the
worker's process stops and starts. Native exit, a `SessionEnd` callback, a
successful transfer and the researcher's ending are separate facts with
separate receipts, and none implies another.

`status` lists every run with its recorded facts. `status RUN` adds what was
observed, transferred and outstanding. The guest learns of a sitting when it
ends; while one runs, `status` shows the facts recorded before it started.

## Closeout

Run closeout when the sitting has finished and the researcher has declared the
ending. It prints the sitting and note counts, the capture's limitations and
omissions, and the guest paths of the recording and the reading edition, both
reread before it reports success. `--json` gives counts, hashes and the
detailed gaps.

Repeat the same command after an interruption. Once the ending is recorded,
`closeout RUN` without a reason resumes it. A completed run can be checked this
way without its host staging.

An unavailable native source or a missing terminal callback is a coverage
limitation, printed before acceptance. Read the limitations before claiming
that a capture is complete.

If an inventoried file cannot reach the guest, closeout sends the rest, names
the missing files and exits unsuccessfully before acceptance. Restore the file
and repeat, or pass the researcher's reason with `--missing-reason TEXT`. The
guest then records the reason and the missing members, and the recording states
the gap for good. Acceptance closes that collection: the same evidence arriving
later belongs in a supplement.

`end`, `collect`, `transfer`, `accept`, `preserve` and `publish` are closeout's
steps as separate commands, for inspection and recovery. `verify RUN` rereads
the preserved recording and edition in the guest.

## Later activity

If the worker is active again after the collection froze, keep that too. A
supplement adds a second recording beside the first and changes nothing already
preserved.

```sh
berman-research supplement run-001 --request returned --reason 'The worker continued after the ending'
berman-research closeout run-001 --supplement 1
```

## Exposure

A participant may recognize an installed hook, remember earlier fieldwork or
look inside the guest. When that matters, record what they encountered and how
it was handled:

```sh
berman-research exposure run-001 --request noticed-hook --account 'The worker asked about the capture hook; the researcher explained it'
```

This adds a dated researcher account to the run. Discovery alone changes
nothing in the lifecycle.

## After an interruption

- **Lost connection.** Keep the spool, reconnect to the same collection and
  repeat the same command. Hooks stage observations on macOS while the guest is
  unreachable.
- **A launcher that died.** The next `launch` or `resume` records that the exit
  was not observed, once the process is gone. If the recorded process may still
  be running, the command refuses until that is settled.
- **A transfer that was cut short.** The member stays outstanding and the next
  `closeout` or `transfer` sends it again. A staged file that is gone or changed
  is listed and skipped; the command exits 1 after sending the rest.
- **Preserve or publish stopped part-way.** Repeat closeout. Published editions
  are immutable, and a retry reuses them.

[The connector protocol](protocol.md) gives the exact replay and repair rules.

## Qualifying an installation

Software checks use synthetic sessions. Whether this machine's harness, hooks
and permissions produce a capture is established only by a native session
here. Commission a designated assignment, run it with
`berman-research launch RUN --one-turn`, close it out, then evaluate the
collection with `berman-research qualify RUN`. It reports each native check and
records nothing in the guest.
[What qualification checks](executor.md#qualification-evidence)

## Study inputs

A study's inputs live in the guest, outside the worker's packet and the
connected checkout.

- A connection (`connections/NAME/connection.json`, schema
  `berman.connection/1`) names the project documents to observe, optional
  project checks and an optional `default_recipe`.
- A recipe (`recipes/NAME/recipe.json`, schema `berman.recipe/2`) declares its
  connections and recording protocol and names INSTRUCTIONS.md, which contains
  exactly one `{{notes_directory}}` placeholder. An empty `connections` list
  means generic instructions; a nonempty list restricts them to those
  connections.
- A plan (`plans/NAME/plan.json`, schema `berman.research-plan/1`) is optional:
  an id, a connection and ordered steps, each with an id and a recipe.
  `commission --plan NAME` takes its next unstarted step. A commission with
  `--recipe` leaves the plan's sequence untouched.

The [minimal study](../examples/minimal-study/README.md) holds a baseline
recipe to start from. A record Berman would misread is refused with its file
and the place of each problem, such as `documents must be an array`. Reading
never rewrites a record.

## What a fieldworker receives

The packet lies in the executor's spool, outside the checkout. It holds
ASSIGNMENT.md (the opening message), INSTRUCTIONS.md (the recipe's instructions
with the notes directory filled in) and a writable `notes/`. Only that packet
is added to the harness's working directories.

The assignment that reaches the host carries the instructions, the harness, the
document selectors and the project checks, with the collection, run and
commission identities. The plan, the recipe's research annotations and the
reason an engagement ended stay in the guest. Anything a study wants said about
the project, how the work begins or what to attend to belongs in the recipe's
INSTRUCTIONS.md. [Native executor](executor.md#preparing-a-checkout) gives the
packet and the opening message exactly.

## Ordinary development

Ordinary development needs no Berman command. Run Codex or Claude from the
project checkout with the normal harness, credentials and permissions. The
executor's Codex router ignores every session that does not open with a
prepared assignment from the assigned checkout, and Claude receives capture
hooks only on a fieldwork launch.
