# Running fieldwork

A fieldwork workspace holds berman.json, named connections, versioned recipes,
executable research plans, operations and preserved runs. The software checkout
is independent. Select a workspace with `berman --root PATH status` or run the
command from within that workspace's repository.

## Prepare an installation

`berman init PATH --project PROJECT --connection NAME` creates a new workspace.
The project and live packet root must be disjoint from the workspace. Its
Codex and Claude configuration homes are private directories under .state/;
authentication and qualification are separate operator steps. Configure executable
paths in berman.json. For Claude use CLAUDE_CONFIG_DIR naming that private home.

The generated connection selects README.md and AGENTS.md as context and has no
research plan. Choose the actual documents, commission and project checks for
your project. A recipe uses schema berman.recipe/2, declares its connections and
recording protocol, and names INSTRUCTIONS.md plus COMMISSION.md. Instructions
contain exactly one `{{notes_directory}}` placeholder. The synthetic files under
[minimal study example](../examples/minimal-study/README.md) shows the formats; these are
examples for software testing, not research assignments to adopt automatically.

A plan uses berman.research-plan/1, an id, a connection and ordered steps with
an id and recipe. Select its relative path in the connection's berman.json entry
only when those engagements are wanted. Instructions are retained at reservation;
changing a recipe does not change an already prepared assignment.

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

berman.json names two interpreters. `python` runs access probes and a
connection's project checks inside the fieldworker's sandbox, where the
workspace is denied, so it must lie outside the workspace and packet root.
`runtime_python` is the interpreter that has this package and its dependencies
installed; generated hook commands name it and run in isolated mode. The hooks
themselves load only the standard library and Berman's capture modules. `init`
records the active package interpreter in both settings; configure `python` for
sandbox access while keeping `runtime_python` pinned to the package environment.
Older configurations without `runtime_python` fall back to `python`. A virtual
environment inside the denied workspace is a suitable `runtime_python`: hooks
run from the harness, outside the sandbox.

Before fieldwork, write the workspace's hook template with
`berman capture definition --adapter berman-codex --write`, install it with
`berman capture install --adapter berman-codex`, review /hooks in the actual CLI,
and use `berman capture record-trust --evidence FILE --attest`. A native support
trial demonstrates callback capture. `berman support-trial --help` describes
preparation, launch, collection and cleanup. A model session requires the
operator's decision. Claude uses separately qualified per-launch settings.

## Engagements

`berman new` prepares the next assigned step without choosing a run number.
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
and preserves the encounter. Capture finalization alone is not an ending.
An interrupted command is retried by running the same command again: it
allocates no second run and declares no second ending.
`berman supplement --reason TEXT` retains later activity after a frozen capture.
Use the workflow rather than manually repeating profile, packet or migration steps.

When retrying interrupted preparation or preservation, Berman retains abandoned
writer-owned temporary files under `.state/recovery/RUN/`, with receipts naming
their origins and recording their bytes and metadata. Results identify these
receipts. A receipt's sibling `material` is the retained copy; a receipt alone
means the move did not finish. This recovery happens before inventory, and does
not remove anything from an already preserved run. Keep the recovery directory
with the study; it is not automatically included in a reading edition.

## Verify preserved recordings

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
fails. Building again creates a new immutable edition and retains the old one.

Reading aids decode explicitly identified Codex and Claude streams into neutral
blocks, preserving native roles and source physical lines/JSON pointers. Raw
availability, decoded coverage and unknown formats are separate facts. Unsupported
records remain available in their originals and are not silently counted as
human messages. Original Markdown/physical-line citations remain usable.
