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
berman-research status run-001
berman-research end run-001 --reason 'The researcher ended fieldwork after the project review'
berman-research collect run-001
berman-research transfer run-001
berman-research accept run-001
berman-research preserve run-001
berman-research publish run-001
berman-research verify run-001
```

Use the run returned by commission. Reuse the same request label and arguments after a lost response. Give a fresh commission a fresh label. Omit the recipe to use the connection's default, or pass `--plan NAME` to select the next step from the guest's `plans/NAME/plan.json`. Include prior step consumption when continuing an existing study, or start a separately identified plan for its continuation. The worker does not receive the plan, the recipe's research annotations or the reason for ending.

The macOS launcher waits for the native process. The callback stages observations locally even if SSH is unavailable. Native exit, SessionEnd, successful transfer and researcher ending each have separate receipts. Closeout obtains the guest's ending identity, takes a final observation of available native sources and notes, and freezes a local inventory. An unavailable source or missing terminal callback is a coverage limitation. Review the manifest and its limitations before claiming complete coverage. Run `qualify RUN` for the positive native checks and `qualify RUN --expected-child ID` for discovery of a known associated session.

If an exposure matters, record what the participant encountered and how it was handled with `exposure RUN --request LABEL --account TEXT`. This adds a dated researcher account to the guest ledger. Discovery of an installed hook or VM has no automatic lifecycle consequence. Interpretation, assessment and prior fieldwork can stay in the separate guest analysis repository.

## Command and receipt contract, version 1

The host starts one predetermined guest command with OpenSSH. Lima supplies the SSH configuration, including the host key and the multiplexed socket. Berman only needs stdin, stdout, stderr and a process exit status. OpenSSH handles authentication and reconnection on subsequent invocations. AsyncSSH supports command channels and part of the OpenSSH configuration language. Using the system client keeps Lima's generated configuration authoritative and avoids maintaining a second SSH client configuration or event loop. [Lima SSH](https://lima-vm.io/docs/usage/ssh/) · [AsyncSSH client options](https://asyncssh.readthedocs.io/en/latest/api.html#config-file-support)

The request begins with one UTF-8 JSON line of at most 4 MiB. Every request identifies `protocol` (`berman.connector/1`), `collection_id` and `command`. A run command includes `run`. Each mutation includes a stable UUID in `operation`. A transfer appends exactly the declared raw bytes and EOF. Success returns a JSON receipt on stdout. A mutation receipt includes the operation, run, timestamp, canonical request SHA-256 and command result. Errors go to stderr and set a nonzero exit status. The exact JSON encoding for the digest is defined in `protocol.json_bytes`. Receipts identify historical events. The verify command rereads the guest's preserved bytes.

| Guest command | Input or result |
| --- | --- |
| commission | Connection, harness, optional recipe or plan. Returns an immutable assignment. |
| assignment, status | Fetch one worker assignment, or discover recorded facts, observed and transferred members, acceptance and publication. |
| preparation, launch | Attributed native observations naming the commission and its digest. |
| ending, closeout | Record an explicit ending reason. Return its identity for native collection. |
| capture | Frozen host collection receipt naming that ending, with member names, lengths, SHA-256 and coverage limitations. |
| transfer | One inventoried path, byte count, digest and byte stream. Records successful transfer separately for this occurrence. |
| accept | Re-read all transferred objects, check their lengths and digests, then commit the inventory digest. |
| preserve, publish, verify | Build a portable recording from accepted evidence, build a reading edition, or independently verify the retained publication. |
| exposure | Append a researcher account under a separate operation ID. |

The guest commits each lifecycle change and its response together in SQLite. Submitting different input under an existing operation identity fails. Receiving an object writes a temporary file, verifies length and SHA-256, syncs it and atomically links it into content-addressed storage. A killed receiver may leave a temporary file or an unreferenced object. Neither counts as transferred. Identical content may serve distinct occurrences. Each run needs its own transfer and acceptance facts. Limits are 256 MiB per member, 512 MiB total and 4,096 named files per collection.

## Resuming work

- After loss of connectivity, keep the spool and reconnect to the same collection. Repeat the same command. A repeated launch with a recorded intent does not start a second process. Inspect the local launch intent and native session if process startup or exit remains uncertain.
- A short stream, excess bytes or a bad digest leaves the member outstanding. Transfer compares guest status with the frozen host inventory and retries missing members. Acceptance fails until every member verifies. An unreachable host leaves the guest's last recorded facts available.
- Preserve and publish can be retried after interruption. Published receipts identify immutable ZIPs. `verify` checks them using only the guest. An integrity pass can include uncertain capture coverage.
- Keep the analysis repository on guest storage, adjacent to the fieldwork collection. Link or cite its originals from the analysis side. A packet only receives the worker material in its assignment. Context gathering within a project then stays on project files, including anything participants themselves place there.
