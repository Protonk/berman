# Running fieldwork

Fieldwork has one lifecycle. A research controller in the Linux guest
commissions each engagement, records its ending and keeps custody of what was
collected. A native executor on macOS prepares the worker's packet, launches the
designated harness and stages its observations. Nothing else in Berman prepares,
launches, captures, ends, collects, preserves, migrates or supplements fieldwork.

The remaining `berman` commands read: the installed software, portable
recordings, reading editions, and a [co-located workspace](#co-located-workspaces)
made by an earlier release, which this version never writes.

## From commission to edition

Provision the guest and the macOS binding with the
[deployment guide](guest-deployment.md). The operator then works from an
ordinary terminal with `berman-research`, which is the same program as
`berman remote`:

```sh
berman remote commission --request first-account --connection example --harness claude --recipe baseline
berman remote prepare run-001
berman remote launch run-001
berman remote status run-001
berman remote end run-001 --reason 'The researcher ended fieldwork after the project review'
berman remote collect run-001
berman remote transfer run-001
berman remote accept run-001
berman remote preserve run-001
berman remote publish run-001
berman remote verify run-001
```

The [command and receipt guide](connector.md) explains each step, the request
labels that make a lost response safe to retry, and how work resumes after an
interruption. [Native execution](executor.md) covers hook review, launch,
staging, closeout and qualification. [Custody](custody.md) covers the portable
recording and the reading edition the guest publishes from it.

Each of these is a separate fact with its own receipt: native exit, a
`SessionEnd` callback, a successful transfer, the researcher's ending. Only
`end --reason` records the researcher's declaration. Capture coverage is
reported with its limitations and is never inferred from a later step.

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
  connections. The [minimal study example](../examples/minimal-study/README.md)
  holds the baseline recipe.
- A plan (`plans/NAME/plan.json`, schema `berman.research-plan/1`) is optional:
  an id, a connection and ordered steps, each with an id and a recipe.
  `commission --plan NAME` takes its next unstarted step. A commission with
  `--recipe` neither consumes a step nor depends on the plan's `commitment` or
  `relationships`, which are study annotations.

A record Berman would crash on or misread is refused with its file and the
place of each problem, such as `documents must be an array`. Reading never
rewrites a record.

## What a fieldworker receives

The worker's packet lies in the executor's spool, outside the checkout. It
holds ASSIGNMENT.md (the opening message), INSTRUCTIONS.md (the recipe's
instructions with the notes directory filled in) and a writable `notes/`.
Only that packet is added to the harness's working directories.

The assignment that reaches the host carries the instructions, the harness, the
document selectors and the project checks, with the collection, run and
commission identities. The plan, the recipe's research annotations and the
reason an engagement ended stay in the guest. Anything a study wants said about
the project, how the work begins or what to attend to belongs in the recipe's
INSTRUCTIONS.md. [Native execution](executor.md#preparing-a-checkout) gives the
packet and the opening message exactly.

The baseline recipe in the minimal study example asks for plain accounts of work
in progress, under the `work-accounts/1` protocol. Start a study's own recipe
from it and change what the study needs. The baseline does not tell the
fieldworker that the session is being recorded.

## Ordinary development

Ordinary development needs no Berman command. Run Codex or Claude from the
project checkout with the normal harness, credentials and permissions.
Fieldwork packets and capture registration belong to designated assignments;
the executor's Codex router ignores every session that does not open with a
prepared assignment from the assigned checkout.

## Co-located workspaces

Releases up to 0.26.3 ran everything in one co-located workspace: a directory
with `berman.json`, named connections, recipes, plans, `operations/` and
preserved `runs/`, on the same machine account as the worker. This version
keeps that evidence readable and verifiable and takes no part in operating it.

- **It is read-only here.** `status`, `verify`, `recording export` and
  `archive --verify` read the workspace. No command prepares, launches, ends,
  supplements, cancels or migrates a run, creates a workspace, installs a hook
  or runs a harness there. `archive` without `--verify` publishes a new edition
  under `archive/editions/`, where edition retention applies, and
  `verify --record` adds one new evidence folder under `experiments/`; neither
  touches a run, an operation record, an inventory or a recording.
- **Unsettled work stays with the release that opened it.** A reserved,
  prepared or open engagement, a declared ending whose preservation, migration,
  packaging or reading publication never finished, a pending combined request
  and an open support trial are reported by `status` and by the `settled` check
  of `verify`, each with the statement that it has to be closed with the release
  that opened it. This version does not close it. Keep that released runtime
  installed for the workspace, and use it, until nothing is outstanding.
- **Nothing is inferred.** A run at rest without an ending declaration or a
  recording package is reported as it is. It is not called unsettled, and it
  gains no ending, no package and no completeness by being read.
- **A study continues in the guest.** Export each preserved run as a portable
  recording, import it into guest custody and reserve the run numbers already
  used, as [historical studies](guest-deployment.md#historical-studies)
  describes. New engagements are commissioned there.

Select a workspace with `berman --root PATH status` or run the command from
within that workspace's repository. Selection is silent and stops at Git
boundaries.

`status` lists each connection's selected plan as its records left it (the
first unstarted step, or that every step was started), every run that is not at
rest, and what is unsettled. On an interactive terminal it shows each listed
run's lifecycle facts with the evidence for each, or the reason a fact is
unknown. Unknown is never shown as no. Redirected output is plain text,
`BERMAN_OUTPUT=plain` keeps the plain text on a terminal, and `--json` carries
everything either rendering shows.

A run whose operation record is refused is listed as needing attention and its
facts are unknown. What such a run awaits is unknown too, so it is reported as a
problem and not as unsettled work.

A workspace may list `historical_runs` in berman.json. Those identities must
exist under runs/ and have no active packet. Discovery and the default edition
leave them opaque; `verify` still audits their retained evidence, and an
explicit `archive --run RUN` may read them.

## Verify a co-located workspace

`berman verify` runs these checks on a selected workspace, each a separate fact:

| Check | What it establishes |
| --- | --- |
| `frozen-collector` | The installed historical collector retains its frozen bytes. |
| `recorded-hashes` | Files listed in experiment checksum lists and preparation manifests keep their recorded bytes. |
| `workspace` | berman.json, the catalogue and the connections load, and `status` reports no problem. |
| `settled` | Nothing awaits the release that opened it. Unsettled work makes the verdict incomplete; it is never closed. |
| `recordings` | Preserved originals match their inventory; capture, migration and ending evidence agree; recording ZIPs match their recorded hashes and contents. |
| `archive` | The published reading edition matches its retained member manifest. |

It runs no harness and reads no harness configuration, so it says nothing about
hook trust, native capture or native access. The executor qualifies a native
installation from its own launches (`berman remote qualify RUN`), and a guest
study's recording and edition are verified there (`berman remote verify RUN`).

The `recordings` check reads preserved runs and their inventories, capture
records, migration journals, ending declarations and recording ZIPs. It never
repairs, rebuilds or makes an engagement-ending decision. A recorded ZIP that is
missing or changed fails. Historical absence of an ending, operation or capture
record is reported under `coverage.unknown`; it is not repaired and does not
prevent an integrity pass. A publication that never finished remains
`not-established`. An integrity pass means retained evidence agrees, not that
capture was complete or an engagement ended.

Use `berman --json verify --skip archive` to inspect recording evidence without
checking the reading edition. `--skip recordings` leaves the overall verdict
incomplete. `--record experiments/NAME` keeps the result and raw receipts in a
new folder. Without a workspace, or with `--software`, `berman verify` checks the
installed package only.

## Portable recordings

Recordings use `berman.recording/2`. Their immutable manifest retains the
collection UUID, run identity, exact relative membership, hashes, original
filesystem metadata, stream formats, attribution evidence and lifecycle evidence.
A path occurrence belongs to that collection and run; equal bytes in another
run do not become the same occurrence. A verified working copy may have new
mtimes; its historical metadata remains data in `recording.json`.

```sh
berman recording verify /copies/run-008.zip --sha256 TRUSTED_SHA256
berman recording materialize /copies/run-008.zip /analysis/source-copy --sha256 TRUSTED_SHA256
berman recording verify /analysis/source-copy
```

These commands need no workspace and read a guest study's recordings and a
co-located workspace's alike. A separately retained package digest pins the
source; without one, verification establishes internal consistency, not external
authenticity. Materialization requires a new destination. Relative links confined
to existing material are preserved; external and parent-traversing links are
refused and remain readable as link bytes in the ZIP. Never attach an unchecked
ZIP using a general-purpose extraction command.

A preserved run of a co-located workspace is exported without changing that
workspace:

```sh
berman --root /study recording export --run run-008 --historical --output /copies/run-008.zip
```

Export uses the agreed UUID in `collection.json` (`schema: berman.collection/1`,
`id: UUID`) or `berman.json`'s `collection_id`. `--collection-id UUID` supplies an
explicit identity for a historical source that has none. Copies of the same
collection must share the agreed identity; do not mint a new UUID per export.
`--historical` preserves missing authority as unknown and never creates a
researcher ending; without it, a run that has no ending declaration is refused.
The output path must be new and lies outside the preserved originals. The export
is what [guest custody imports](custody.md#importing-an-existing-study).

## Capture generations

A capture is a sequence of complete generations: transcript, derived reader,
metadata and receipt history become visible together when
`session/current.json` changes. Readers follow that pointer and never assume
`session/transcript.jsonl`. Superseded full snapshots are removed only after a
committed successor contains their prefix, or displaced raw and reader bytes
have been retained. Distinct partial tails remain retained. The executor stages
with this format, and the packets a co-located workspace preserved hold it.

Freezing a capture and ending an engagement are different facts. A frozen
capture in a co-located workspace is still an open engagement until its own
release records the researcher's declaration.

## Reading editions

The guest publishes each run's edition from its recording
(`berman remote publish RUN`). Any published edition verifies from a copy, with
no study beside it:

```sh
berman archive --path /copies/edition.zip
```

For a co-located workspace, build or check its edition explicitly:

```sh
berman --root /path/to/study archive
berman --root /path/to/study archive --verify
berman --root /path/to/study archive --freshness
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

`--verify` checks the published edition using its retained manifest; it neither
rerenders nor compares against today's analysis files or builder. `--freshness`
separately reports `current` or `stale` against current selected inputs. A stale
edition can be intact and exits successfully; a missing or damaged publication
fails. Building again creates a new immutable edition. The current and previous
publication for each selection are retained; older editions are removed only
when no other selection references them. Recordings and original evidence are
never removed.

Reading aids decode explicitly identified Codex and Claude streams into neutral
blocks, preserving native roles and source physical lines/JSON pointers. Raw
availability, decoded coverage and unknown formats are separate facts. Unsupported
records remain available in their originals and are not silently counted as
human messages. Original Markdown/physical-line citations remain usable.

## Bounded collection

Connection `documents` entries may be relative, nonrecursive glob selectors. The
executor expands them at preparation and again at closeout, and retains the
selectors, matching paths, hashes and missing matches. Selection is limited to
256 files, 4 MiB per file and 16 MiB total. Symlink traversal and Git metadata
selection are refused. Choose bounded documentation and artifact directories
rather than an entire checkout.

Associated Codex sessions are selected from native parent/fork metadata, including
`parent_thread_id`, `forked_from_id` and nested `source.subagent.thread_spawn`.
Claude collection supports the parent's `subagents/agent-*.jsonl` directory.
The source receipt names its observed scope and relationships. Raw bytes are kept;
child completion or the absence of undiscovered sessions is never inferred.
The bound is 256 associated sessions, 256 MiB per source and 512 MiB total.
