# Running fieldwork

For a Linux research installation, use the [connector commands](connector.md).
The following commands describe co-located workspaces with their own pinned
installation. Use the [disposition and migration procedure](guest-deployment.md#historical-studies)
when moving an existing study.

A fieldwork workspace holds berman.json, named connections, versioned recipes,
optional research plans, operations and preserved runs. The software checkout
is independent. Select a workspace with `berman --root PATH status` or run the
command from within that workspace's repository.

## Prepare an installation

`berman init PATH --project PROJECT --connection NAME` creates a new workspace.
The project and live packet root must be disjoint from the workspace. Its
Codex and Claude configuration homes are private directories under .state/;
authentication and qualification are separate operator steps. Configure executable
paths in berman.json. For Claude use CLAUDE_CONFIG_DIR naming that private home.

The generated connection selects README.md and AGENTS.md as context and has no
research plan. Choose the actual documents and project checks for your project.
A recipe uses schema berman.recipe/2, declares its connections and recording
protocol, and names INSTRUCTIONS.md, which contains exactly one
`{{notes_directory}}` placeholder. The
[minimal study example](../examples/minimal-study/README.md) holds the baseline
recipe and shows the formats.

A researcher can assign a recipe directly with `berman new --recipe NAME`,
adding `--connection NAME` when needed. Without a recipe argument, a fresh
assignment comes from the selected plan, or the connection's explicit
`default_recipe` when no plan is selected. Without either, Berman asks for a
recipe choice. An empty recipe `connections` list means generic instructions;
a nonempty list restricts them to those connections. `berman new --repeat RUN`
uses that run's retained recipe bytes with freshly observed project context.

A plan is optional. It uses berman.research-plan/1, an id, a connection and
ordered steps with an id and recipe. Select its relative path in the connection's
berman.json entry when those engagements are wanted. Direct assignments neither
consume its steps nor depend on its commitments or relationships; those fields
are study annotations. A missing or invalid selected plan is reported by status,
cheatsheet and workspace verification, while direct assignment, retained retries,
named launch, preservation and development remain available with otherwise valid
inputs. Instructions are retained at reservation; changing a plan or recipe does
not change an already reserved assignment.

## What a fieldworker receives

A fieldworker's packet is its run directory under the packet root. It holds
INSTRUCTIONS.md (the recipe's instructions with the notes directory filled in),
ASSIGNMENT.md (the opening message), run.json and an empty `notes/`.
`berman launch` submits ASSIGNMENT.md as the session's first message.

The opening message is the same for every fieldworker. Only the run, the paths,
the connection's name and the commit observed at preparation vary:

```text
Berman-Fieldwork-Run: run-NNN

Your working directory is `PROJECT`. This assignment was prepared when NAME was at commit HEAD; the project may have moved since, so work from the checkout as you find it.

Your run directory is `PACKET`. You can read and write everything in it, although the folder containing it is otherwise closed to you.

As a fieldworker you have specific instructions, in `PACKET/INSTRUCTIONS.md`. Read them now, directly rather than asking for a copy, and follow them as you work.
```

The first line is the capture marker; a `berman-claude` packet opens with
`Berman-Claude-Fieldwork-Run:`. Berman says nothing else to the fieldworker.
Anything a study wants said about the project, how the work begins or what to
attend to belongs in the recipe's INSTRUCTIONS.md. Before 0.4.4 a connection's
commission and a recipe's research commission were appended to this message; the
[development guide](development.md#version-044-one-opening-message) says what an
adopting study changes.

The baseline recipe in the minimal study example asks for plain accounts of work
in progress, under the `work-accounts/1` protocol. Start a study's own recipe
from it and change what the study needs. The baseline does not tell the
fieldworker that the session is being recorded.

## Adapters and interpreters

A study has one capture adapter per harness: `berman-codex` and `berman-claude`.
A connection's `capture` entry in berman.json selects the one its next
engagements use. List both under `qualified_adapters` to make verification
require hook trust and native capture for each, including the one no connection
currently selects.

The earlier `legacy-codex` adapter was removed in 0.4. A workspace that still
selects it is told to select `berman-codex`. Runs it recorded keep their
preserved packets, which are read in place and no longer captured,
supplemented or migrated; the opening line it used registers nothing and
answers with a notice. The single-project `berman.configuration/1` file it
belonged to is no longer read.

In the local-workspace workflow, `python` runs the checkout and tool probe.
`runtime_python` runs the installed Berman package for capture hooks and falls
back to `python` when omitted. Codex starts in the checkout with the assigned
packet added via `--add-dir`. Claude receives the packet
and a per-launch settings file containing capture callbacks. Both use their
native permission settings. A preparation from a release that generated
research-denial profiles belongs to its pinned runtime. Finish or retire it
there before switching. Historical receipts and recordings keep their bytes.

Before fieldwork, write the workspace's hook template with
`berman capture definition --adapter berman-codex --write`, install it with
`berman capture install --adapter berman-codex`, review /hooks in the actual CLI,
and use `berman capture record-trust --evidence FILE --attest`. A native support
trial demonstrates callback capture. `berman support-trial --help` describes
preparation, launch, collection and cleanup. A model session requires the
operator's decision. Claude uses separately qualified per-launch settings.

## Engagements

`berman new --recipe NAME` prepares a direct assignment without choosing a run
number. Bare `berman new` prepares the selected plan's next step, the connection's
default recipe, or retries an unused preparation. Preparation results and status identify the retained recipe
and assignment origin (`direct` or `plan`). Historical missing origins remain
unrecorded rather than being inferred.
`berman cheatsheet` prints current launch commands. `berman launch --run RUN`
starts a prepared session. `berman status` reports outstanding work.

On an interactive terminal, `status` shows each outstanding run's lifecycle
facts with the evidence for each, or the reason a fact is unknown, and
`cheatsheet` groups commands by whether they can be typed now or wait on the
researcher's decision. Unknown is never shown as no. Redirected output is the
plain text, `BERMAN_OUTPUT=plain` keeps the plain text on a terminal, and
`--json` carries everything either rendering shows.

A record Berman would crash on or misread is refused with its file and the
place of each problem, such as `connections.example.checkout must be a string`.
A run whose operation record is refused is listed by `status` as needing
attention, its facts are unknown, and it blocks preparation on its connection
until the record is repaired. Reading never rewrites a record.

The cheatsheet uses `berman` on PATH and includes `--root` so its commands work
from any directory. Use the study's package environment on PATH. Inside that
workspace you can omit `--root`. Saved launch files and hooks retain explicit
interpreter paths.

On terminals, commands whose arguments contain control characters use ANSI-C
shell quoting (`$'...'`) so copying the displayed escape preserves the original
argument. Bash and Zsh support this quoting, as does POSIX.1-2024; older POSIX
shells may not. Ordinary paths need no special shell support.

`berman finish --reason TEXT` records the researcher's actual ending decision
and preserves the encounter, collects supported associated native sessions,
publishes a neutral reading edition for that run, and verifies it. Capture
finalization alone is not an ending. This command prints a short completion
result; scripts use `--json`. No separate report or archive command is required
for ordinary closeout.
An interrupted command is retried by running the same command again: it
allocates no second run and declares no second ending.
`berman supplement --reason TEXT` retains later activity after a frozen capture.
Use the workflow rather than manually repeating packet or migration steps.

An open engagement requires the existing choice when preparing another:
`berman new --recipe NAME --keep-open` retains it, and
`berman new --recipe NAME --finish-previous 'why it ended'` declares its actual
ending before preserving it. A selected plan can supply the recipe when that
argument is omitted. The cheatsheet labels commands containing `RECIPE` as
templates requiring a recipe choice, separately from runnable prepared sessions.
While an unused preparation or pending combined request binds the assignment,
the cheatsheet offers the retained launch or retry rather than a fresh recipe
template.

An unused preparation retains its exact recipe, instructions and assignment
origin even if its source plan or recipe is edited or removed. Retry with bare
`berman new`; a repeated `--recipe NAME` must match the retained recipe id.
Changing assignment bytes requires cancelling the unused preparation and making
a new assignment. Existing harness, checkout, workspace and access checks still
apply to the reservation.

A combined finish/preparation request binds the next reserved run before
declaring the previous engagement ended. An interrupted request resumes that
same target and reason; a conflicting recipe, ending reason or `--keep-open`
is refused before further changes. Cancelling its unused target cancels further
preparation by that request, preserving any ending already declared. Pending
historical `berman.new-request/1` requests must be settled with their original
installation before adopting this request contract; completed historical
requests remain readable unchanged.

When retrying interrupted preparation or preservation, Berman retains abandoned
writer-owned temporary files under `.state/recovery/RUN/`, with receipts naming
their origins and recording their bytes and metadata. Results identify these
receipts. A receipt's sibling `material` is the retained copy; a receipt alone
means the move did not finish. This recovery happens before inventory, and does
not remove anything from an already preserved run. Keep the recovery directory
with the study; it is not automatically included in a reading edition.

## Verify preserved recordings

`berman verify --native-access` separately checks access using explicitly
diagnostic instructions in disposable workspaces. By default it checks every
configured Codex connection, including connections without a study plan, and a
synthetic target. It retains each connection's actual checkout, selected
documents and project checks; it copies no study plan and chooses no research
recipe. It launches no fieldworker or model and establishes neither hook trust
nor native capture. Claude uses its separate bounded support trial. A direct
research preparation still performs its own readiness checks.

`berman verify` includes a required `recordings` check, separately from checking
the generated reading edition. It reads preserved runs and their inventories,
capture records, migration journals, ending declarations and recording ZIPs.
It checks bytes and recorded metadata, agreement between records, and ZIP
contents. It never repairs, rebuilds or makes a new engagement-ending decision.

A recorded ZIP that is missing or changed fails integrity verification. Historical
absence of an ending, operation or capture record is reported under
`coverage.unknown`; it is not repaired and does not prevent an integrity pass.
An unfinished modern publication remains `not-established`. An integrity pass
means retained evidence agrees, not that capture was complete or an engagement
ended. Native readiness is checked separately by the installation checks.

Use `berman --json verify --skip archive` to inspect recording evidence without
checking the reading edition. `--skip recordings` leaves the overall verdict
incomplete. Status is a discovery view, not a substitute for the full audit.

### Portable recordings

New recordings use `berman.recording/2`. Their immutable manifest retains the
collection UUID, run identity, exact relative membership, hashes, original
filesystem metadata, stream formats, attribution evidence and lifecycle evidence.
A path occurrence belongs to that collection and run; equal bytes in another
run do not become the same occurrence. Original custody verification still checks
recorded mtimes. A verified working copy may have new mtimes; its historical
metadata remains data in `recording.json`.

```sh
berman recording verify /copies/run-008.zip --sha256 TRUSTED_SHA256
berman recording materialize /copies/run-008.zip /analysis/source-copy --sha256 TRUSTED_SHA256
berman recording verify /analysis/source-copy
```

These commands need no workspace. A separately retained package digest pins the
source; without one, verification establishes internal consistency, not external
authenticity. Materialization requires a new destination. Relative links confined
to existing material are preserved; external and parent-traversing links are
refused and remain readable as link bytes in the ZIP. Never attach an unchecked
ZIP using a general-purpose extraction command.

Historical material can be exported without changing its source workspace:

```sh
berman --root /study recording export --run run-008 --historical --output /copies/run-008.zip
```

Export uses the agreed UUID in `collection.json` (`schema: berman.collection/1`,
`id: UUID`) or `berman.json`'s `collection_id`. `--collection-id UUID` supplies an
explicit identity for a historical source that has none. Copies of the same
collection must share the agreed identity; do not mint a new UUID per export.
A historical export preserves missing authority as unknown and never creates a
researcher ending. The output path must be new.

After reviewing an export, `berman --root /study recording import --run run-008`
adds the manifest, retained authority and new package beside unchanged originals.
This is an explicit study maintenance operation. Existing recording ZIPs remain
unchanged. Retain the old runtime and evidence until adoption is qualified.
Immutable manifests govern published recordings; mutable operation and inventory
indexes no longer determine their preserved attribution or ending.

### Capture generations

New capture writes a complete generation, then atomically changes
`session/current.json`. The transcript, reader, metadata and receipt history
become visible together. Staging is outside the packet under
`.state/capture-staging/RUN/`; retries retain unpublished generations there.
Readers follow the pointer, rather than assuming `session/transcript.jsonl`.
Superseded full snapshots are removed only after a committed successor contains
their prefix, or displaced raw/reader bytes have been retained. Distinct partial
tails remain retained. This bounds routine append-only capture storage without
deleting non-prefix revisions. Frozen captures are never compacted.

An older active capture must be closed using its pinned runtime before adopting
this version. Finalization still freezes capture only; `finish --reason` records
the separate researcher declaration. Process-death recovery is tested; sudden
power loss and native callback delivery require separate evidence.

## Reading editions

Build a generic edition explicitly against a workspace:

```sh
berman --root /path/to/study archive
berman --root /path/to/study archive --verify
```

The default selects the workspace's recorded connections. `--connection NAME`
and `--run RUN` narrow it. Its edition.json declares additional relative document
paths to include. The generic payload contains inventoried runs, their selected
inventory, chosen documents and generated aids; it does not collect configuration,
hook receipts or software merely because they share a directory.

`--edition stewart` preserves the explicit historical Stewart packaging behavior.
Its workspace must supply the legacy context documents. A workspace names the
edition it maintains with `edition` in berman.json; `berman archive` and the
archive check of `berman verify` then use it without the option. Never run a builder
against a Git worktree of a preserved collection: Git does not restore recorded
modification times.

Published editions are immutable files under `archive/editions/SHA256.zip`.
A selection pointer identifies the latest publication for an edition/connection/
run selection. Each ZIP contains an edition manifest naming its inputs, reader
identity, generated outputs and exact member hashes and modes.

```sh
berman archive --path /copies/edition.zip
berman --root /study archive --freshness
```

`--verify` checks the published edition using its retained manifest; it neither
rerenders nor compares against today's analysis files or builder. `--freshness`
separately reports `current` or `stale` against current selected inputs. A stale
edition can be intact and exits successfully; a missing or damaged publication
fails. Building again creates a new immutable edition. The current and previous
publication for each selection are retained; older editions are removed only
when no other selection references them. Recordings and original evidence are
never removed. Publication and retention resume together after interruption.

Reading aids decode explicitly identified Codex and Claude streams into neutral
blocks, preserving native roles and source physical lines/JSON pointers. Raw
availability, decoded coverage and unknown formats are separate facts. Unsupported
records remain available in their originals and are not silently counted as
human messages. Original Markdown/physical-line citations remain usable.

## Task readiness and bounded collection

`berman ready` checks the selected connection's assignment and capture preflight
without reserving a run. `berman ready --development codex` and
`berman ready --development claude` run the configured native binary with `--version` in its checkout.
Preparation and launch still perform their own native
access checks. `berman verify` remains the separate comprehensive maintenance
audit; historical gaps do not become prerequisites for starting an ordinary task.

Connection `documents` entries may be relative, nonrecursive glob selectors. They are expanded
separately at preparation, launch and closeout, with the selectors, matching paths,
hashes and missing matches retained. Selection is limited to 256 files, 4 MiB per
file and 16 MiB total. Symlink traversal and Git metadata selection are refused.
Choose bounded documentation and artifact directories rather than an entire checkout.

Associated Codex sessions are selected from native parent/fork metadata, including
`parent_thread_id`, `forked_from_id` and nested `source.subagent.thread_spawn`.
Claude collection supports the parent's `subagents/agent-*.jsonl` directory.
The source receipt names its observed scope and relationships. Raw bytes are kept;
child completion or the absence of undiscovered sessions is never inferred.
The bound is 256 associated sessions, 256 MiB per source and 512 MiB total.
An interrupted closeout retains its first complete auxiliary observation and
resumes publication; it does not silently replace that observation with later data.

A workspace may explicitly list `historical_runs` in berman.json. Those identities
must exist under runs/ and have no active packet. Routine discovery and the default
edition leave them opaque, while allocation reserves their numbers. This is an
intentional compatibility boundary, not a migration. Originals remain intact;
full `verify` still audits retained evidence. An explicit `archive --run RUN`
may read historical material if this release supports it.
