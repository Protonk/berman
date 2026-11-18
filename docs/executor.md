# Native executor

An assignment is the research controller's decision to dispatch one fieldworker. The macOS executor receives the worker instructions, selected project document names and required checks. The local connection binds those requirements to a checkout, native harness home and executable. Keeping the spool outside the checkout lets a developer search the project without picking up packets, staging or research records. Participants can inspect the apparatus. A researcher can record an exposure and decide its significance.

## Preparing a checkout

`prepare(spool, assignment, binding)` returns a `berman.preparation/1` receipt. The assignment fields are `schema` (`berman.assignment/1`), `collection_id`, `operation`, `run`, `connection`, `harness`, `instructions`, `documents`, and `checks`. Collection and operation IDs are canonical UUID strings. The instruction text has one `{{notes_directory}}` placeholder. The receipt repeats the five identifying fields and `files.digest_json(assignment)` in `assignment_sha256`. All later receipts carry this identity.

A host binding looks like this (supply the actual native paths).

```json
{
  "connection": "example",
  "project": "/Users/researcher/work/example",
  "home": "/Users/researcher/.claude",
  "executable": "/opt/homebrew/bin/claude"
}
```

The connection must match the assignment. Omit `policy` for the harness default. An explicit value names a native Codex sandbox mode or Claude permission mode. Project checks run as native host subprocesses in the checkout. Check arguments are strings. Berman resolves `{python}`, `{project}`, `{packet}` and
`{executable:NAME}` against the executor environment. Each check has a unique
identifier, a nonempty argument list and an optional timeout of 1–120 seconds.
The preparation receipt records their resolved arguments, return codes and output digests, the selected Git state and the observed document hashes. The harness process receives its ordinary environment. A custom home selects `CODEX_HOME` or `CLAUDE_CONFIG_DIR`. An unset home variable stays unset when the binding selects the default home, which matters for Claude's login state.

Inside `SPOOL/operations/OPERATION/packet`, the worker gets `ASSIGNMENT.md`, rendered `INSTRUCTIONS.md` and a writable `notes/`. Only that packet is added to the harness's working directories. Put private research questions, assessments and analysis in the research environment. The worker and developer retain their ordinary native facilities.

## Hooks and launch

Claude receives a per-launch settings file containing observation hooks and a reserved parent session UUID. Inherited subagent callbacks with another session ID are ignored. The executor writes the settings file under the spool.

For Codex, print the proposed shared command groups with the installed executor interpreter.

```sh
python -m berman.executor --spool "$spool" codex-hooks --home "$codex_home"
```

The command prints JSON. At installation transition, close active captures, replace the previous Berman routing groups with the proposed exclusive router, retain unrelated hooks, and review trust in the actual Codex CLI. Launch checks the selected home's hooks file for the expected groups and conflicting Berman handlers. The router ignores ordinary sessions. A Codex parent must submit the exact prepared opening from the assigned checkout before its session ID is bound. A callback identifying a different parent cannot replace that binding.

`launch(spool, operation, one_turn=False, resume=False, prompt=None)` invokes the native process with the checkout as its current directory. Each call that starts a process is a sitting, kept under `sittings/NNNN/` as `intent.json`, `started.json`, `exit.json` and, once the exit is recorded, a `receipt.json` that is never rewritten. Without `resume` it starts the first sitting, and any repeat returns that sitting's receipt without starting anything. With `resume` and a `request` label it starts the next sitting, or returns the sitting that label already started. Claude resumes its reserved session with `--resume` once a native transcript exists for it; Codex resumes the bound session with `codex resume`. Before then a resumed sitting opens with the prepared prompt. The `berman.launch/1` receipt carries the intent, exact arguments, selected mode, observed PID and exit status, the native session ID when a callback established one, and `sitting` for every sitting after the first. The intent records the executable, its version and digest, interpreter identity, package source digests, and digests of the local configuration files inspected. Native configuration may have other sources.

For a designated qualification assignment, use the normal connector command or invoke the executor directly.

```sh
berman remote launch run-001 --one-turn
python -m berman.executor --spool "$spool" launch "$operation" --one-turn
```

Choose one invocation. Claude receives `--print` and Codex receives `exec` with that prompt. Berman adds no turn limit. A resumed one-turn sitting needs `--prompt TEXT`. Both use closed stdin in this mode. The native response goes to stderr and the executor receipt to stdout. Interactive launches inherit the terminal.

The launch writes its intent before spawning a child. Replaying an intent can recover the receipt. Concurrent launch callers serialize independently of callbacks: a launcher holds the launch lock until its child exits. A later call that gets the lock therefore knows no launcher is waiting. If the last sitting has no recorded exit and its process is gone, that call writes an exit with `returncode` null and an `unobserved` note saying whether a process was ever seen to start. `started.json` keeps the process's start time beside its PID; a live process counts as the sitting's only when both match, so a reused PID does not block. If the recorded process is still running, the call refuses. A plain `launch` never spawns a second process for an operation; only `resume` starts another sitting.

## Staging and closeout

Hooks run against local disk and operate while the research environment is offline. Each attributed callback has its own durable event file. The raw event is written first. A second write records the verified transcript generation or an acquisition limitation. A crash between those writes leaves the raw observation available. Callback loss, interrupts, disabled hooks and missing files can all produce incomplete evidence.

The hook checks the session ID against the primary native source. Codex requires a matching session metadata record. Claude requires the reserved filename and matching session identities in the records. Capture reuses Berman's committed transcript generations, derived readers, non-prefix retention and partial-tail handling. Staging follows the engagement, not the process: a callback from the bound parent is kept after the launched process has exited, including from a session the operator resumed by hand in a home that has the hooks. Once a collection is frozen, callbacks are staged under `late/` with their own transcript generations, and wait there for the next supplement. A callback is ignored only when it comes from a session that is not bound to the operation. Closeout separately samples the native source as it then exists.

`collect(spool, operation, declaration_id, supplement=None)` takes the UUID of the controller's explicit ending declaration, or with `supplement=N` the UUID of that supplement's declaration. A supplement freezes `supplements/NNNN/` from the `late/` area, a fresh closeout observation, the packet as it then is, and a new project snapshot. Its receipt adds `supplement_id` and `sequence`. It serializes with hooks, refreshes the bound parent, observes associated native sessions by recorded relationships, and takes a final project snapshot. Missing parent or child stores become coverage limitations. Codex parent relocation uses a filename containing the session ID and verifies the metadata. Claude searches its project stores for the reserved filename. Ambiguous matches are recorded. A successful final read is a time-specific observation.

The frozen `berman.collection/1` receipt contains `ending_id`, nullable `session_id`, `parent_sha256`, observed callback names, associated session IDs, `limitations`, and `members`. The inventory includes launch and preparation receipts, worker material, selected project snapshots, and available staged evidence. Every member names a relative POSIX path and the length and SHA-256 of its frozen bytes.

```python
manifest = collect(spool, operation, ending_id)
for row in manifest['members']:
    source = payload_path(spool, operation, ending_id, row['path'])
    # source is a pathlib.Path verified against row['size'] and row['sha256'].
```

The inventory is limited to 4,096 members, 256 MiB per file and 512 MiB total. One member never fails the collection. A symbolic link, a special file, a directory or entry that cannot be read, a name a portable recording cannot carry, a file over a bound, a file beyond the member or total bound, and a file that changes on two successive reads are each left out, listed in the receipt's `omitted` (path, kind, detail), named in `limitations`, and recorded in an `omitted.json` member. A link keeps its target text and an oversized file its length and SHA-256. Receipts, the parent transcript and the packet are taken first, so a bound is spent on callbacks and project context last. `omitted.json` counts toward the same bounds; members taken last give way to it. A failed acquisition may leave scratch material under the operation. Only the atomically published `collection/manifest.json` identifies a frozen collection. Repeating collection returns the original receipt. A different declaration ID is refused. `damaged(spool, operation, declaration_id)` lists frozen members whose staged bytes are now absent or changed; `payload_path` refuses each of those individually. Source files can subsequently move or disappear. Transfer and research custody are acknowledged separately by the controller.

## Qualification evidence

`qualify(spool, operation, ending_id, expected_child=None)` verifies the frozen collection and evaluates the installation exercised by that launch. It checks observed process start and exit, preparation's checkout and project tool results, opening and Stop callbacks, successful offline capture, attributed source bytes, closeout observation and native permission evidence. Codex supplies the sandbox policy in its native turn context. Claude reports its permission mode in hook events. `native_policy_observed` says only that the harness reported a mode; that report is the harness's own account. `selected_policy_exercised` is true or false when the binding selected a mode, and null when it selected none, in which case the trial says nothing about a selected mode. `later_sittings_observed` appears when the engagement had more than one sitting.

Pass the ID of an independently expected child during an associated-session trial. Its source must be in the hashed inventory for the additional check to pass. A trial with no expected child establishes parent capture only. A qualification failure describes evidence from this launch. Engagement endings and decisions about exposure remain with the researcher.

All low-level commands accept `--spool PATH` before the command. `prepare ASSIGNMENT.json BINDING.json`, `launch OPERATION [--resume --request LABEL] [--prompt TEXT]`, `collect OPERATION DECLARATION_UUID [--supplement N]`, `damaged OPERATION DECLARATION_UUID [--supplement N]`, `payload-path OPERATION DECLARATION_UUID MEMBER [--supplement N]` and `qualify OPERATION ENDING_UUID [--expected-child ID]` produce JSON. Tests in `tests/integration/test_executor.py` use disposable homes, executable harness doubles and subprocess hook commands. Live harness behavior, credentials and operator hook trust require a native trial.
