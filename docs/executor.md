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

The connection must match the assignment. Omit `policy` for the harness default. An explicit value names a native Codex sandbox mode or Claude permission mode. Project checks run as native host subprocesses in the checkout. The preparation receipt records their resolved arguments, return codes and output digests, the selected Git state and the observed document hashes. The harness process receives its ordinary environment. A custom home selects `CODEX_HOME` or `CLAUDE_CONFIG_DIR`. An unset home variable stays unset when the binding selects the default home, which matters for Claude's login state.

Inside `SPOOL/operations/OPERATION/packet`, the worker gets `ASSIGNMENT.md`, rendered `INSTRUCTIONS.md` and a writable `notes/`. Only that packet is added to the harness's working directories. Put private research questions, assessments and analysis in the research environment. The worker and developer retain their ordinary native facilities.

## Hooks and launch

Claude receives a per-launch settings file containing observation hooks and a reserved parent session UUID. Inherited subagent callbacks with another session ID are ignored. The executor writes the settings file under the spool.

For Codex, print the proposed shared command groups with the installed executor interpreter.

```sh
python -m berman.executor --spool "$spool" codex-hooks --home "$codex_home"
```

The command prints JSON. At installation transition, close active captures, replace the previous Berman routing groups with the proposed exclusive router, retain unrelated hooks, and review trust in the actual Codex CLI. Launch checks the selected home's hooks file for the expected groups and conflicting Berman handlers. The router ignores ordinary sessions. A Codex parent must submit the exact prepared opening from the assigned checkout before its session ID is bound. A callback identifying a different parent cannot replace that binding.

`launch(spool, operation, one_turn=False)` invokes the native process with the checkout as its current directory. The `berman.launch/1` receipt carries the intent, exact arguments, selected mode, observed PID and exit status, and the native session ID when a callback established one. The intent records the executable, its version and digest, interpreter identity, package source digests, and digests of the local configuration files inspected. Native configuration may have other sources.

For a designated qualification assignment, use the normal connector command or invoke the executor directly.

```sh
berman remote launch run-001 --one-turn
python -m berman.executor --spool "$spool" launch "$operation" --one-turn
```

Choose one invocation. Claude receives `--print --max-turns 4` to allow tool use in answering a single user prompt. Codex receives `exec` with that prompt. Both use closed stdin in this mode. The native response goes to stderr and the executor receipt to stdout. Interactive launches inherit the terminal.

The launch writes its intent before spawning a child. Replaying an intent can recover the receipt. Concurrent launch callers serialize independently of callbacks. After interruption between intent and process observation, inspect the partial receipt, native session and staged callbacks. An absent PID or exit status means that observation is unknown. A retry never spawns a second process for that operation.

## Staging and closeout

Hooks run against local disk and operate while the research environment is offline. Each attributed callback has its own durable event file. The raw event is written first. A second write records the verified transcript generation or an acquisition limitation. A crash between those writes leaves the raw observation available. Callback loss, interrupts, disabled hooks and missing files can all produce incomplete evidence.

The hook checks the session ID against the primary native source. Codex requires a matching session metadata record. Claude requires the reserved filename and matching session identities in the records. Capture reuses Berman's committed transcript generations, derived readers, non-prefix retention and partial-tail handling. A callback after observed native process exit or frozen collection cannot extend hook staging. Closeout separately samples the native source as it then exists. A transcript from a resumed parent can include activity between these observations.

`collect(spool, operation, ending_id)` takes the UUID of the controller's explicit ending declaration. It serializes with hooks, refreshes the bound parent, observes associated native sessions by recorded relationships, and takes a final project snapshot. Missing parent or child stores become coverage limitations. Codex parent relocation uses a filename containing the session ID and verifies the metadata. Claude searches its project stores for the reserved filename. Ambiguous matches are recorded. A successful final read is a time-specific observation.

The frozen `berman.collection/1` receipt contains `ending_id`, nullable `session_id`, `parent_sha256`, observed callback names, associated session IDs, `limitations`, and `members`. The inventory includes launch and preparation receipts, worker material, selected project snapshots, and available staged evidence. Every member names a relative POSIX path and the length and SHA-256 of its frozen bytes.

```python
manifest = collect(spool, operation, ending_id)
for row in manifest['members']:
    source = payload_path(spool, operation, ending_id, row['path'])
    # source is a pathlib.Path verified against row['size'] and row['sha256'].
```

The inventory is limited to 4,096 members, 256 MiB per file and 512 MiB total. A failed acquisition may leave scratch material under the operation. Only the atomically published `collection/manifest.json` identifies a frozen collection. Repeating collection verifies the committed members and returns the original receipt. A different ending ID or changed frozen bytes requires investigation. Source files can subsequently move or disappear. Transfer and research custody are acknowledged separately by the controller.

## Qualification evidence

`qualify(spool, operation, ending_id, expected_child=None)` verifies the frozen collection and evaluates the installation exercised by that launch. It checks observed process start and exit, preparation's checkout and project tool results, opening and Stop callbacks, successful offline capture, attributed source bytes, closeout observation and native permission evidence. Codex supplies the sandbox policy in its native turn context. Claude reports its permission mode in hook events. An explicit requested mode must agree with the observed modes. With the native default, the report lists the modes actually seen. A missing or unrecognized policy observation leaves the corresponding check failed.

Pass the ID of an independently expected child during an associated-session trial. Its source must be in the hashed inventory for the additional check to pass. A trial with no expected child establishes parent capture only. A qualification failure describes evidence from this launch. Engagement endings and decisions about exposure remain with the researcher.

All low-level commands accept `--spool PATH` before the command. `prepare ASSIGNMENT.json BINDING.json`, `launch OPERATION`, `collect OPERATION ENDING_UUID`, `payload-path OPERATION ENDING_UUID MEMBER` and `qualify OPERATION ENDING_UUID [--expected-child ID]` produce JSON. Tests in `tests/integration/test_executor.py` use disposable homes, executable harness doubles and subprocess hook commands. Live harness behavior, credentials and operator hook trust require a native trial.
