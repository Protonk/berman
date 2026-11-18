# Recordings and editions

Closeout leaves two files in the guest for each run. The recording is the
evidence: the files the host returned, the controller's records about them and
a manifest that pins every byte. The reading edition is a convenience built
from the recording: the same originals with indexes and decoded transcripts for
a reader. Both verify from a copy, with no study beside them.
[Words used here](operating.md#words-used-here)

## What is recorded

Berman records an engagement and nothing else in the project. For one
engagement the host returns, in the main:

- the packet as the worker left it, with the assignment, the instructions and
  the worker's notes;
- the preparation receipt, with the results of the project checks, and a
  receipt for each sitting;
- the callbacks the capture hooks staged from the bound session, with the
  transcript snapshots they obtained, and the associated sessions that
  collection found;
- snapshots of the selected project documents and the Git state at preparation
  and at collection;
- the names of anything it saw and could not take.

The controller adds its own records: the assignment, the commission decision,
its copies of the host's receipts, the ending with its reason and time, the
transfer map and the acceptance. [Native executor](executor.md#collection)
gives the collection's members and bounds exactly.

## What a recording establishes

A recording that verifies establishes that its members are the bytes the guest
accepted for that collection and run, with the hashes, sizes and modes the
manifest records, and that they agree with the controller's records packaged
beside them: the assignment, the commissioner's ending and its time, the host's
collection receipt, the transfer map and the acceptance.

It establishes nothing about the work. A worker's notes are kept as written,
and no verification says that an account is accurate or that the work was done
well.

It does not establish that the capture was complete. An integrity pass can
cover an incomplete capture. What was not observed or not kept is stated, not
inferred:

- `coverage` carries the limitations: the host's, such as a callback with no
  transcript, an unobserved process exit or a session store that was
  unavailable, and the lines custody adds for a receipt or member it does not
  hold.
- A member the host saw and could not take is named in `omitted.json`.
- A member accepted as missing has one coverage line with its path and the
  reason, and the acceptance in `evidence/controller.json` keeps its length and
  digest. If the missing member is any file of the parent transcript generation
  under `session/`, the recording makes no claim about the parent at all.
- Associated sessions are the ones native relationship metadata identified
  within the search bounds. Their presence does not show that a child finished
  or that no other session existed.
- Project snapshots are the selected documents and Git state at preparation and
  at collection, not the checkout.

Verification against a digest kept separately establishes that the copy is the
recording the guest issued. Without one it establishes internal consistency
only. Nothing in a recording says what a worker knew of an engagement's
arrangements beyond the packet. Exposure accounts stay in the guest's ledger
and are not packaged.

## Where they are

Under the study root in the guest (`~/research/STUDY`):

| Path | Holds |
| --- | --- |
| `custody/RUN/recording.zip`, `receipt.json` | The recording and its custody receipt. |
| `custody/RUN/supplements/NNNN/` | A supplement: a second recording of the same run. |
| `archive/editions/SHA256.zip` | Every published edition, immutable. |
| `archive/editions/selections/RUN.json` | Which edition is selected for the run; `RUN-supplement-N.json` for a supplement. |
| `controller/` | The controller's ledger and received objects. Not for reading. |

Closeout prints the recording and edition paths. `berman-research verify RUN`
rereads both in the guest and checks the selection pointer and edition against
the recording and the ledger's publication receipt.

## Verifying and reading a copy

```sh
berman recording verify /copies/run-008.zip --sha256 TRUSTED_SHA256
berman recording materialize /copies/run-008.zip /analysis/source-copy --sha256 TRUSTED_SHA256
berman recording verify /analysis/source-copy
berman archive --path /copies/edition.zip
```

`materialize` writes a working copy into a new destination. Relative links
confined to existing material are preserved; external and parent-traversing
links are refused and remain readable as link bytes in the ZIP. A working copy
may have new modification times; the recorded metadata stays in
`recording.json`. Never unpack an unchecked ZIP with a general-purpose
extraction command.

A recording uses `berman.recording/3`. Its manifest retains the collection
UUID, run identity, exact relative membership, hashes, portable filesystem
modes, stream formats, attribution evidence and lifecycle evidence. A path
occurrence belongs to that collection and run; equal bytes in another run are
not the same occurrence.

In a `berman.reading-edition/3` ZIP every original member sits at its recorded
path under `berman-analysis/`, including the commission decision, which the
worker never received. `recording.json` inventories the originals. `edition-manifest.json`
pins that manifest by its exact byte digest, every reading aid, and the source
modules that built the edition. The reading aids decode Codex and Claude
streams into neutral blocks that keep native roles and cite source lines and
JSON pointers. Raw availability, decoded coverage and unknown formats are
separate facts: an unsupported record stays available in its original and is
not counted as a message.

This software writes `recording/3` and `reading-edition/3`. It also verifies
guest `recording/2` (the `split` profile) and single-recording
`reading-edition/2`, and replaying publication of one honors its original
selection pointer. Releases through 0.26.5 cannot read the `/3` formats.

What a commissioner does with a recording is theirs to decide, including
bringing a worker's notes back into the project. A study that keeps earlier
engagements from later workers keeps its recordings, and whatever is written
from them, on guest-owned storage, outside the worker packet, the executor's
spool and the project checkout. When citing a recording, give the collection
UUID, run, supplement when applicable, member path and digest.

## How custody builds them

The controller owns commissions, declarations and the SQLite lifecycle ledger.
Custody (`berman.custody`) reads a snapshot of that authority and produces the
two artifacts.

```python
from berman import custody

# state is the controller's accepted snapshot. These paths are resolved by the
# controller from its object store. A host member name only names a ZIP member.
blobs = {name: objects / digest for name, digest in state['transferred'].items()}
recording = custody.preserve_recording(study, state, blobs)
# Commit recording as the preserve receipt. Publication can run later.
edition = custody.publish_edition(study, recording)
```

`preserve_recording` checks each received file's size and SHA-256 against the
observed inventory, and checks the transfer map, the acceptance count and the
fingerprint of the accepted inventory. The capture receipt must identify the
operation that declared the ending. When the executor supplies a parent
transcript, its native session, digest and capture generation must agree with
the retained capture.

The recording's single inventory covers the accepted files, package directories
and controller evidence. Its connection is checked against the retained
assignment. Run-relative names come from the executor, including
`packet/notes/`, `events/`, `session/` and `associated/`. The guest records
package permissions for returned regular files. `evidence/controller.json`
carries the assignment, the private decision if supplied, native preparation
and launch receipts when available, the explicit ending, capture receipt,
transfer map and acceptance. `evidence/ending.json` retains the declaration and
its time.

Preservation returns a `berman.custody-recording/1` receipt. Its
`relative_path` resolves under the study root, `package_sha256` identifies the
exact ZIP, and `manifest_sha256` identifies the manifest value under Berman's
recording identity convention. The writer syncs both staged files and installs
their enclosing directory with a single rename. A conflicting occurrence fails
with its first accepted bytes available for inspection.

`preserve_recording(study, state, blobs, supplement=N)` packages a supplement.
Its declaration is in `evidence/supplement.json`, the first recording's custody
receipt is in its controller record, `supplement.sequence` and
`supplement.follows_recording_sha256` are in its manifest, and a coverage line
says that it neither repeats nor replaces the first. The first recording must
be in custody before its supplement, and custody refuses a supplement that
names any other predecessor. The verifier refuses a package whose capture
receipt, declaration, sequence and named predecessor do not all agree, and a
first recording whose capture receipt says it was collected for a supplement.

`verify_recording(study, recording)` checks the stored receipt and reopens the
recording. `publish_edition` takes that receipt, verifies again and reads the
retained ZIP members directly for the reading aids, rendering text and
transcript indexes in memory. Modes, link targets and recorded timestamps come
from the verified package. The renderer never installs recorded symlinks or
executes saved programs. A supplement gets its own edition and its own
selection pointer, so the run's first edition stays selected and unchanged.

The publication receipt records the edition and pointer paths relative to the
study. A retry verifies the edition against its source manifest and reuses it.
If publication stops after installing the ZIP, a retry finishes installing the
pointer. Temporary directories prefixed with `.incoming-` have no custody
authority; inspect them after an interruption and remove them with writers
stopped.

## Importing a guest recording

A recording issued by one guest study can enter another with the same
collection UUID. Use the original ZIP and its independently retained SHA-256.
Importing stores the original bytes and a custody receipt. The controller
allocates future numbers above existing custody occurrences and the study's
`reserved_through` floor.

Use the guest interpreter that contains the controller package. This example
takes `digest` from the trusted recording receipt and `bytes` from its byte
count. The SSH destination and guest paths are installation settings.

```sh
ssh -F "$ssh_config" "$guest" \
  ".local/berman/bin/python -I -m berman.custody --root research/example --sha256 $digest --size $bytes" \
  < "$recording_zip"
```

Only substitute a validated lowercase SHA-256 digest and decimal byte count
into the remote command. The importer reads exactly the declared length and
checks for EOF and digest agreement. A premature disconnect or an extra byte
fails before custody commits. If the acknowledgement is lost, replay the same
byte stream with the same digest. For a file already staged in the guest, use
`import_recording(study, path, expected_sha256=digest)`. Both entry points
return the same recording receipt, which `publish_edition` accepts.

The importer accepts Berman's single-disk, comment-free stored or DEFLATE
recording ZIPs. Bounds are 544 MiB each for uploaded and expanded bytes,
256 MiB per member, 16 MiB for the manifest, 8 MiB for the central directory
and 16,384 members. It checks expansion lengths and member paths before the
recording verifier checks exact membership, modes, hashes and retained
authority. The receipt identifies bytes accepted by the guest. Establishing
their historical origin depends on the provenance of the supplied digest.
