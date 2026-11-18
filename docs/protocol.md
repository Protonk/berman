# The connector protocol, version 1

This is the contract between the macOS connector (`berman.connector`) and the
guest controller (`berman.controller`). Operators do not need it; it is here
for changing either side and for reading receipts.
[Operating a study](operating.md) covers the commands.

## Transport

The host starts one predetermined guest command with OpenSSH:
`.local/berman/bin/python -I -m berman.controller --study STUDY`, in the guest
login directory. Lima supplies the SSH configuration, including the host key
and the multiplexed socket. Berman needs only stdin, stdout, stderr and a
process exit status. OpenSSH handles authentication and reconnection on
subsequent invocations. [Lima SSH](https://lima-vm.io/docs/usage/ssh/)

## Requests and receipts

A request begins with one UTF-8 JSON line of at most 4 MiB. Every request
names `protocol` (`berman.connector/1`), `collection_id` and `command`. A run
command includes `run`. Each mutation includes a stable UUID in `operation`. A
transfer appends exactly the declared raw bytes and EOF.

Success returns a JSON receipt on stdout. A mutation receipt includes the
operation, run, timestamp, canonical request SHA-256 and command result. Errors
go to stderr and set a nonzero exit status. The exact JSON encoding for the
digest is `protocol.json_bytes`. Receipts identify historical events; `verify`
rereads the guest's preserved bytes.

The connector derives each operation UUID from the collection, the run, the
command and the caller's request label or member path, so a repeated command is
the same operation. The guest commits each lifecycle change and its response
together in SQLite and answers a repeated operation with the original receipt.
Submitting different input under an existing operation identity fails.

| Guest command | Input or result |
| --- | --- |
| commission | Connection, harness, optional recipe or plan. Returns an immutable assignment. |
| assignment | Fetch one worker assignment. |
| status | Without a run: every run with its connection, harness, facts, sitting count and supplement facts. With a run: also observed and transferred members, what is outstanding, acceptance and publication. |
| preparation, launch, sitting | Attributed native observations naming the commission and its digest. `launch` is the first sitting; `sitting` carries a later one and its number. |
| ending, closeout | Record an explicit ending reason. Return its identity for native collection. |
| capture | Frozen host collection receipt naming that ending, with member names, lengths, SHA-256 and coverage limitations. |
| transfer | One inventoried path, byte count, digest and byte stream. Records successful transfer separately for this occurrence. |
| accept | Reread all transferred objects, check their lengths and digests, then commit the inventory digest. If a member never arrived or the store has lost it, acceptance fails and names each one. With `missing_reason` it accepts the rest and records the missing members and the reason. |
| supplement | Declare later activity after a frozen collection, with a reason. Returns its number. `closeout`, `capture`, `transfer`, `accept`, `preserve`, `publish` and `status` then take that number in `supplement`. |
| preserve, publish, verify | Build a portable recording from accepted evidence, build a reading edition, or independently verify the retained publication. |
| exposure | Append a dated account of an exposure to the run, under a separate operation ID. |

## Guest storage and transfer

The guest keeps its ledger in `controller/ledger.sqlite` and received bytes in
`controller/objects/SHA256` under the study. Receiving an object writes a
temporary file, verifies length and SHA-256, syncs it and atomically links it
into content-addressed storage. A killed receiver may leave a temporary file or
an unreferenced object; neither counts as transferred. Identical content may
serve distinct occurrences, and each run needs its own transfer and acceptance
facts.

Limits are 256 MiB per member, 512 MiB total and 4,096 named files per
collection. A file beyond a limit is left out of the collection and named in
it, with its size; the collection still freezes.

`status RUN` lists `outstanding`: observed members the guest does not hold at
the right length. `transfer` asks for the same list with every object reread
(`deep`, reported as `outstanding_check: sha256`), so an object damaged without
changing length is sent again too. Repeating a transfer whose object has since
gone missing or been damaged stores it again, moves any damaged bytes aside as
`.damaged-…`, and adds an entry to the run's `repairs`.

A short stream, excess bytes or a bad digest leaves the member outstanding.
`transfer` before `collect` has reported the inventory is refused. Acceptance
closes a collection: a member that turns up afterwards is refused for it and
belongs in a supplement, and only a repeat of an earlier transfer is still
taken, to repair the store. An unreachable host leaves the guest's last
recorded facts available.
