# Running fieldwork

Fieldwork has one lifecycle. A research controller in the Linux guest
commissions each engagement, records its ending and keeps custody of what was
collected. A native executor on macOS prepares the worker's packet, launches the
designated harness and stages its observations. Nothing else in Berman prepares,
launches, captures, ends, collects, preserves or supplements fieldwork.

Standalone commands check the installed software and read portable guest recordings
and reading editions.

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
berman remote closeout run-001 --reason 'The researcher ended fieldwork after the project review'
```

The [command and receipt guide](connector.md) explains each step, the request
labels that make a lost response safe to retry, and how work resumes after an
interruption. [Native execution](executor.md) covers hook review, launch,
staging, closeout and qualification. [Custody](custody.md) covers the portable
recording and the reading edition the guest publishes from it.

Each of these is a separate fact with its own receipt: native exit, a
`SessionEnd` callback, a successful transfer, the researcher's ending. The researcher supplies the ending
declaration to `closeout --reason` or to the individual `end` command.
Repeat closeout after an interruption. If some observed files cannot be
transferred, the command names them and awaits recovery or the researcher's
missing-evidence reason. Capture coverage is
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

These commands read a guest recording or its materialized working copy. A separately retained package digest pins the
source; without one, verification establishes internal consistency, not external
authenticity. Materialization requires a new destination. Relative links confined
to existing material are preserved; external and parent-traversing links are
refused and remain readable as link bytes in the ZIP. Never attach an unchecked
ZIP using a general-purpose extraction command.

## Capture generations

A capture is a sequence of complete generations: transcript, derived reader,
metadata and receipt history become visible together when
`session/current.json` changes. Readers follow that pointer and never assume
`session/transcript.jsonl`. Superseded full snapshots are removed only after a
committed successor contains their prefix, or displaced raw and reader bytes
have been retained. Distinct partial tails remain retained. The executor stages native captures in this format.

Freezing a capture and ending an engagement are different facts.

## Reading editions

The guest publishes each run's edition from its recording
(`berman remote publish RUN`). Any published edition verifies from a copy, with
no study beside it:

```sh
berman archive --path /copies/edition.zip
```

Published editions are immutable files under `archive/editions/SHA256.zip`.
A selection pointer identifies the latest publication for an edition/connection/
run selection. Each ZIP contains an edition manifest naming its inputs, reader
identity, generated outputs and exact member hashes and modes.

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
