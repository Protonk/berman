# Operating a guest study

Commission from the researcher’s terminal. The study's Linux account reads its connection, optional plan and recipe, allocates a number and retains the decision. It returns only the instructions, harness choice, document selectors and project checks, with the collection, run and commission identities. A researcher should review the worker-facing text and checks when selecting a recipe or changing a connection. The executor places the rendered packet and working notes outside the checkout. Its launch uses the participant's own harness home. An ordinary developer can start Codex or Claude directly in the project.

For a new installation, provision the guest and select its collection UUID using the [deployment guide](guest-deployment.md). Install the same build in a macOS Python 3.14 environment. The executable `berman-research` in its bin directory and `berman remote` accept the same arguments. By default the host reads `~/.config/berman/connector.json`. Select another file with `--config FILE` or `BERMAN_CONNECTOR`. For example

```json
{
  "host": "lima-berman-research",
  "ssh_config": "~/.lima/berman-research/ssh.config",
  "study": "example-study",
  "collection_id": "29525f31-3e9d-4c3d-9078-cedd71216be9",
  "spool": "~/.local/share/berman/spool",
  "bindings": {
    "example": {
      "codex": {"project": "/Users/me/project", "home": "~/.codex", "executable": "~/.local/bin/codex"},
      "claude": {"project": "/Users/me/project", "home": "~/.claude", "executable": "~/.local/bin/claude"}
    }
  }
}
```

Replace the UUID with the one in the guest's `collection.json`. The project must be a Git checkout. The home and executable identify the ordinary native installation. A binding may set an explicit harness `policy`. The executor records its launch arguments and checks the policy reported by native evidence during qualification. Codex needs the exclusive hook definition for this spool installed and reviewed in its CLI before launch. Claude receives its capture settings at launch. See [native execution](executor.md).

## From commission to edition

```sh
berman-research commission --request first-account --connection example --harness claude --recipe baseline
berman-research prepare run-001
berman-research launch run-001
berman-research resume run-001 --request second   # each later sitting of the same engagement
berman-research status run-001
berman-research closeout run-001 --reason 'The researcher ended fieldwork after the project review'
```

Run closeout once the sitting has finished and the researcher has declared the ending.
Repeat the same command after an interruption. Once the ending is recorded, the
shorter `berman-research closeout run-001` also resumes it. The connector uses the
guest receipts, transfers outstanding files, and reopens the recording and edition
before reporting success. A completed run can be checked this way with its host
staging offline. Output includes the guest paths, sitting and note counts, and
coverage. Add `--json` for the counts, hashes and detailed gaps.

Closeout reports capture omissions and limitations before acceptance. If an
inventoried file cannot reach the guest, it transfers the rest, names the gaps
and exits unsuccessfully before acceptance. Restore the file and repeat, or
supply the researcher's reason with `closeout run-001 --missing-reason TEXT`.
The guest records the reason and the missing members. The individual operations
remain available for inspection and recovery.

If the worker is active again after collection, keep that too. A supplement adds a second recording beside the first and changes nothing already preserved.

```sh
berman-research supplement run-001 --request returned --reason 'The worker continued after the ending'
berman-research closeout run-001 --supplement 1
```

Use the run returned by commission. Reuse the same request label and arguments after a lost response. Give a fresh commission a fresh label. Omit the recipe to use the connection's default, or pass `--plan NAME` to select the next step from the guest's `plans/NAME/plan.json`. Include prior step consumption when continuing an existing study, or start a separately identified plan for its continuation. The worker does not receive the plan, the recipe's research annotations or the reason for ending.

The macOS launcher waits for the native process. The callback stages observations locally even if SSH is unavailable. Native exit, SessionEnd, successful transfer and researcher ending each have separate receipts.

An engagement lasts until the researcher ends it, however many times the worker's process stops and starts. `launch` starts the first sitting; repeating it only replays that receipt. `resume --request LABEL` starts another sitting and resumes the same native session (`claude --resume`, `codex resume`). The label identifies that sitting: repeating the command with the same label, as after a lost reply, returns the sitting it already started. A sitting's receipt is written once, when its exit is recorded, and is reported to the guest unchanged from then on. If no native session exists yet, because the first start never reached a turn, `resume` opens with the prepared prompt again. A callback from the bound session is staged whether or not Berman started the process that sent it. `collect` reports every finished sitting to the guest before it freezes the collection. Closeout obtains the guest's ending identity, takes a final observation of available native sources and notes, and freezes a local inventory. An unavailable source or missing terminal callback is a coverage limitation. Review the manifest and its limitations before claiming complete coverage. Run `qualify RUN` for the positive native checks and `qualify RUN --expected-child ID` for discovery of a known associated session.

If an exposure matters, record what the participant encountered and how it was handled with `exposure RUN --request LABEL --account TEXT`. This adds a dated researcher account to the guest ledger. Discovery of an installed hook or VM has no automatic lifecycle consequence. Interpretation, assessment and prior fieldwork can stay in the separate guest analysis repository.

## Command and receipt contract, version 1

The host starts one predetermined guest command with OpenSSH. Lima supplies the SSH configuration, including the host key and the multiplexed socket. Berman only needs stdin, stdout, stderr and a process exit status. OpenSSH handles authentication and reconnection on subsequent invocations. [Lima SSH](https://lima-vm.io/docs/usage/ssh/)

The request begins with one UTF-8 JSON line of at most 4 MiB. Every request identifies `protocol` (`berman.connector/1`), `collection_id` and `command`. A run command includes `run`. Each mutation includes a stable UUID in `operation`. A transfer appends exactly the declared raw bytes and EOF. Success returns a JSON receipt on stdout. A mutation receipt includes the operation, run, timestamp, canonical request SHA-256 and command result. Errors go to stderr and set a nonzero exit status. The exact JSON encoding for the digest is defined in `protocol.json_bytes`. Receipts identify historical events. The verify command rereads the guest's preserved bytes.

| Guest command | Input or result |
| --- | --- |
| commission | Connection, harness, optional recipe or plan. Returns an immutable assignment. |
| assignment, status | Fetch one worker assignment, or discover recorded facts, observed and transferred members, acceptance and publication. |
| preparation, launch, sitting | Attributed native observations naming the commission and its digest. `launch` is the first sitting; `sitting` carries a later one and its number. |
| ending, closeout | Record an explicit ending reason. Return its identity for native collection. |
| capture | Frozen host collection receipt naming that ending, with member names, lengths, SHA-256 and coverage limitations. |
| transfer | One inventoried path, byte count, digest and byte stream. Records successful transfer separately for this occurrence. |
| accept | Re-read all transferred objects, check their lengths and digests, then commit the inventory digest. If a member never arrived or the store has lost it, acceptance fails and names each one. With `missing_reason` it accepts the rest and records the missing members and the reason. |
| supplement | Declare later activity after a frozen collection, with a reason. Returns its number. `closeout`, `capture`, `transfer`, `accept`, `preserve`, `publish` and `status` then take that number in `supplement`. |
| preserve, publish, verify | Build a portable recording from accepted evidence, build a reading edition, or independently verify the retained publication. |
| exposure | Append a researcher account under a separate operation ID. |

The guest commits each lifecycle change and its response together in SQLite. Submitting different input under an existing operation identity fails. Receiving an object writes a temporary file, verifies length and SHA-256, syncs it and atomically links it into content-addressed storage. A killed receiver may leave a temporary file or an unreferenced object. Neither counts as transferred. Identical content may serve distinct occurrences. Each run needs its own transfer and acceptance facts. Limits are 256 MiB per member, 512 MiB total and 4,096 named files per collection. A file beyond a limit is left out of the collection and named in it, with its size; the collection still freezes.

The guest stores its ledger in `controller/ledger.sqlite` and received bytes in `controller/objects/SHA256` under the study. `status RUN` lists `outstanding`: observed members the guest does not hold at the right length. `transfer` asks for the same list with every object reread (`deep`, reported as `outstanding_check: sha256`), so an object damaged without changing length is sent again too. Repeating a transfer whose object has since gone missing or been damaged stores it again, moves any damaged bytes aside as `.damaged-…`, and adds an entry to the run's `repairs`.

## Resuming work

- After loss of connectivity, keep the spool and reconnect to the same collection. Repeat the same command. A repeated launch with a recorded intent does not start a second process. Inspect the local launch intent and native session if process startup or exit remains uncertain.
- A short stream, excess bytes or a bad digest leaves the member outstanding. Transfer sends every member the guest reports outstanding. A staged file that is gone or no longer matches its frozen digest is skipped and listed in `unavailable_on_host`, and the command exits 1 after sending the rest. Acceptance fails until every member verifies, and says which did not. To take what arrived into custody, run `accept RUN --missing-reason TEXT`: the recording then states each missing member in its coverage. Acceptance closes that collection. A member that turns up afterwards is refused for it and belongs in a supplement; only a repeat of an earlier transfer is still taken, to repair the store. `transfer` before `collect` has reported the inventory is refused. An unreachable host leaves the guest's last recorded facts available.
- If a launcher dies before its process exits, the next `launch` or `resume` records that the exit was not observed, once the process is gone. When a PID is live and its start time cannot be checked, the sitting stays open until its exit can be established.
- Preserve and publish can be retried after interruption. Published receipts identify immutable ZIPs. `verify` checks them using only the guest. An integrity pass can include uncertain capture coverage.
- Keep the analysis repository on guest storage, adjacent to the fieldwork collection. Link or cite its originals from the analysis side. A packet only receives the worker material in its assignment. Context gathering within a project then stays on project files, including anything participants themselves place there.
