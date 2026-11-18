# Native executor

The executor is the macOS half of an engagement. It receives an assignment
from the controller, binds it to a checkout and a harness on this host,
prepares the worker's packet, launches the harness, stages what the capture
hooks observe and freezes a collection for the guest. It keeps everything in a
spool outside the checkout, so a developer can search the project without
picking up packets, staging or research records.

Operators reach it through `berman-research` ([operating a study](operating.md)).
This page is the reference for what it does and records. The code is
`berman.executor`; its commands are also available as
`python -m berman.executor --spool PATH COMMAND`.

## Preparing a checkout

`prepare(spool, assignment, binding)` returns a `berman.preparation/1` receipt.
The assignment fields are `schema` (`berman.assignment/1`), `collection_id`,
`operation`, `run`, `connection`, `harness`, `instructions`, `documents` and
`checks`. Collection and operation IDs are canonical UUID strings. The
instruction text has one `{{notes_directory}}` placeholder. The receipt repeats
the five identifying fields and `files.digest_json(assignment)` in
`assignment_sha256`. All later receipts carry this identity.

A host binding names the actual native paths:

```json
{
  "connection": "example",
  "project": "/Users/researcher/work/example",
  "home": "/Users/researcher/.claude",
  "executable": "/opt/homebrew/bin/claude"
}
```

The connection must match the assignment. Omit `policy` for the harness
default; an explicit value names a native Codex sandbox mode or Claude
permission mode. The optional `model` selects the native CLI's `--model` value
for every sitting.

Project checks run as native host subprocesses in the checkout. Check arguments
are strings. Berman resolves `{python}`, `{project}`, `{packet}` and
`{executable:NAME}` against the executor environment. Each check has a unique
identifier, a nonempty argument list and an optional timeout of 1 to 120
seconds. The preparation receipt records their resolved arguments, return codes
and output digests, the selected Git state and the observed document hashes.

The harness process receives its ordinary environment. A custom home selects
`CODEX_HOME` or `CLAUDE_CONFIG_DIR`. An unset home variable stays unset when the
binding selects the default home, which matters for Claude's login state.

Inside `SPOOL/operations/OPERATION/packet` the worker gets `ASSIGNMENT.md`,
rendered `INSTRUCTIONS.md` and a writable `notes/`. Only that packet is added to
the harness's working directories. The opening message in `ASSIGNMENT.md` is the
marker line `Berman-Assignment: OPERATION`, the instruction to read
`INSTRUCTIONS.md` and follow it in the checkout, and the commit the checkout was
at during preparation.

## Bounded project context

Connection `documents` entries may be relative, nonrecursive glob selectors.
The executor expands them at preparation and again at collection, and retains
the selectors, matching paths, hashes and missing matches with the observed Git
state. Selection is limited to 256 files, 4 MiB per file and 16 MiB total.
Symlink traversal and Git metadata selection are refused. A snapshot is the
selected documents and Git state at that moment, not a copy of the checkout.

## Hooks and launch

One router serves each harness home, and it is the only Berman handler there.

Claude receives a per-launch settings file containing observation hooks and a
reserved parent session UUID. Inherited subagent callbacks with another session
ID are ignored. The executor writes the settings file under the spool.

For Codex, print the proposed shared hook groups with the installed executor
interpreter:

```sh
python -m berman.executor --spool "$spool" codex-hooks --home "$codex_home"
```

The command prints JSON. To install or change it: close active captures,
replace any earlier Berman routing groups in the home's `hooks.json` with the
proposed ones, keep unrelated hooks, and review trust in the actual Codex CLI.
Launch checks the selected home's hooks file for exactly the expected groups
and refuses when any other Berman handler is present, including the hook
commands of earlier releases. The router ignores ordinary sessions. A Codex
parent must submit the exact prepared opening from the assigned checkout before
its session ID is bound. A callback identifying a different parent cannot
replace that binding.

`launch(spool, operation, one_turn=False, resume=False, prompt=None)` invokes
the native process with the checkout as its current directory. Each call that
starts a process is a sitting, kept under `sittings/NNNN/` as `intent.json`,
`started.json`, `exit.json` and, once the exit is recorded, a `receipt.json`
that is never rewritten.

- Without `resume` it starts the first sitting, and any repeat returns that
  sitting's receipt without starting anything.
- With `resume` and a `request` label it starts the next sitting, or returns
  the sitting that label already started. Claude resumes its reserved session
  with `--resume` once a native transcript exists for it; Codex resumes the
  bound session with `codex resume`. Before then a resumed sitting opens with
  the prepared prompt.

The `berman.launch/1` receipt carries the intent, exact arguments, selected
mode, observed PID and exit status, the native session ID when a callback
established one, and `sitting` for every sitting after the first. The intent
records the executable, its version and digest, the interpreter, the digest of
every installed Berman module, and digests of the local configuration files
inspected. Native configuration may have other sources.

`--one-turn` runs a noninteractive sitting for qualification: Claude receives
`--print` and Codex receives `exec` with the prompt, both with closed stdin.
Berman adds no turn limit. A resumed one-turn sitting needs `--prompt TEXT`.
The native response goes to stderr and the receipt to stdout. Interactive
launches inherit the terminal.

The launch writes its intent before spawning a child. A launcher holds the
launch lock until its child exits, so a later call that gets the lock knows no
launcher is waiting. If the last sitting has no recorded exit and its process
is gone, that call writes an exit with `returncode` null and an `unobserved`
note saying whether a process was ever seen to start. `started.json` keeps the
process's start time beside its PID; a live process counts as the sitting's
only when both match, so a reused PID does not block. If the recorded process
is still running, the call refuses. A plain `launch` never spawns a second
process for an operation; only `resume` starts another sitting.

## Staging

Hooks run against local disk and work while the research guest is offline.
Each attributed callback has its own durable event file. The raw event is
written first. A second write records the verified transcript generation or an
acquisition limitation, so a crash between them leaves the raw observation
available. Callback loss, interrupts, disabled hooks and missing files can all
produce incomplete evidence.

The hook checks the session ID against the primary native source. Codex
requires a matching session metadata record. Claude requires the reserved
filename and matching session identities in the records.

Staging follows the engagement, not the process. A callback from the bound
parent is kept after the launched process has exited, including from a session
the operator resumed by hand in a home that has the hooks. Once a collection is
frozen, callbacks are staged under `late/` with their own transcript
generations and wait there for the next supplement. A callback is ignored only
when it comes from a session that is not bound to the operation.

A capture is a sequence of complete generations: transcript, derived reading
copy, metadata and receipt history become visible together when
`session/current.json` changes. Readers follow that pointer and never assume
`session/transcript.jsonl`. A superseded full snapshot is removed only after a
committed successor contains its prefix, or after the displaced raw and reading
bytes have been retained. Distinct partial tails are retained. The reading
copies are rendered by `berman.executor.claude` and `berman.executor.codex`;
the raw JSONL is always kept and the reading copy never substitutes for it.

## Collection

`collect(spool, operation, declaration_id, supplement=None)` takes the UUID of
the controller's explicit ending declaration, or with `supplement=N` the UUID
of that supplement's declaration. It serializes with hooks, refreshes the bound
parent, observes associated native sessions by recorded relationships, and
takes a final project snapshot. A supplement freezes `supplements/NNNN/` from
the `late/` area, a fresh closeout observation, the packet as it then is, and a
new project snapshot; its receipt adds `supplement_id` and `sequence`.

Associated Codex sessions are selected from native parent and fork metadata,
including `parent_thread_id`, `forked_from_id` and nested
`source.subagent.thread_spawn`. Claude collection supports the parent's
`subagents/agent-*.jsonl` directory. The source receipt names its observed
scope and relationships. Raw bytes are kept; child completion and the absence
of undiscovered sessions are never inferred. The bound is 256 associated
sessions, 256 MiB per source and 512 MiB total. Missing parent or child stores
become coverage limitations. Codex parent relocation uses a filename containing
the session ID and verifies the metadata. Claude searches its project stores
for the reserved filename. Ambiguous matches are recorded. A successful final
read is an observation at that time.

The frozen `berman.collection/1` receipt contains `ending_id`, nullable
`session_id`, `parent_sha256`, observed callback names, associated session IDs,
`limitations` and `members`. The inventory includes launch and preparation
receipts, worker material, selected project snapshots and available staged
evidence. Every member names a relative POSIX path and the length and SHA-256
of its frozen bytes.

```python
manifest = collect(spool, operation, ending_id)
for row in manifest['members']:
    source = payload_path(spool, operation, ending_id, row['path'])
    # source is a pathlib.Path verified against row['size'] and row['sha256'].
```

The inventory is limited to 4,096 members, 256 MiB per file and 512 MiB total.
One member never fails the collection. A symbolic link, a special file, a
directory or entry that cannot be read, a name a portable recording cannot
carry, a file over a bound, a file beyond the member or total bound, and a file
that changes on two successive reads are each left out, listed in the receipt's
`omitted` (path, kind, detail), named in `limitations`, and recorded in an
`omitted.json` member. A link keeps its target text and an oversized file its
length and SHA-256. Receipts, the parent transcript and the packet are taken
first, so a bound is spent on callbacks and project context last.
`omitted.json` counts toward the same bounds; members taken last give way to it.

A failed acquisition may leave scratch material under the operation. Only the
atomically published `collection/manifest.json` identifies a frozen collection.
Repeating collection returns the original receipt. A different declaration ID
is refused. `damaged(spool, operation, declaration_id)` lists frozen members
whose staged bytes are now absent or changed; `payload_path` refuses each of
those individually. Source files can subsequently move or disappear. Transfer
and research custody are acknowledged separately by the controller.

## Qualification evidence

`qualify(spool, operation, ending_id, expected_child=None)` verifies the frozen
collection and evaluates the installation exercised by that launch. It checks
the observed process start and exit, preparation's checkout and project check
results, opening and Stop callbacks, a snapshot staged by a hook, attributed
source bytes, the closeout observation and native permission evidence.

Codex supplies the sandbox policy in its native turn context. Claude reports
its permission mode in hook events. `native_policy_observed` says only that the
harness reported a mode; that report is the harness's own account.
`selected_policy_exercised` is true or false when the binding selected a mode,
and null when it selected none, in which case the trial says nothing about a
selected mode. `later_sittings_observed` appears when the engagement had more
than one sitting.

Pass the ID of an independently expected child during an associated-session
trial. Its source must be in the hashed inventory for the additional check to
pass. A trial with no expected child establishes parent capture only.

A qualification describes evidence from one launch on one installation. It
does not carry to another machine, harness version or build of Berman: every
receipt names the digests of the modules and harness that produced it. The
source suite in `tests/integration/test_executor.py` uses disposable homes,
executable harness doubles and subprocess hook commands, and establishes none
of this.

## Low-level commands

All take `--spool PATH` before the command and print JSON:
`prepare ASSIGNMENT.json BINDING.json`,
`launch OPERATION [--one-turn] [--resume --request LABEL] [--prompt TEXT]`,
`collect OPERATION DECLARATION_UUID [--supplement N]`,
`damaged OPERATION DECLARATION_UUID [--supplement N]`,
`payload-path OPERATION DECLARATION_UUID MEMBER [--supplement N]`,
`qualify OPERATION ENDING_UUID [--expected-child ID]`,
`codex-hooks --home HOME` and `hook --home HOME [--operation OPERATION]`, which
is the command the installed hooks run.
