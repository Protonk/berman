# Guest recording custody

The guest study root contains a `collection.json` with the study UUID. The controller owns commissions, declarations and the SQLite lifecycle ledger. Custody reads a snapshot of that authority and produces two independently verifiable artifacts. The portable recording preserves the observations and supporting records. A separate reading edition indexes them for a researcher.

## Controller calls

```python
from berman import custody

# state is the controller's accepted snapshot. These paths are resolved by the
# controller from its object store. A host member name only names a ZIP member.
blobs = {name: objects / digest for name, digest in state['transferred'].items()}
recording = custody.preserve_recording(study, state, blobs)
# Commit recording as the preserve receipt. Publication can run later.
edition = custody.publish_edition(study, recording)
```

`preserve_recording` checks each received file's size and SHA-256 against the observed inventory. It also checks the transfer map, the acceptance count and the fingerprint of the accepted inventory. The capture receipt must identify the operation that declared the ending. When the executor supplies a parent transcript, its native session, digest and capture generation must agree with the retained capture. Associated session files can accompany that transcript. Their inclusion says which related sources the host found within its search scope.

A new recording uses `berman.recording/3`. Its single inventory covers the accepted files, package directories and controller evidence. Its connection is checked against the retained assignment. Run-relative names come from the executor, including `packet/notes/`, `events/`, `session/` and `associated/`. The guest records package permissions for returned regular files. `evidence/controller.json` carries the assignment, the private decision if supplied, native preparation and launch receipts when available, the explicit ending, capture receipt, transfer map and acceptance. `evidence/ending.json` retains the declaration and its time. The recording carries the native coverage limitations. An incomplete capture can enter custody with those limitations.

Evidence that was observed on the host and never reached the guest can also enter custody as a stated gap. When acceptance was given a reason for missing members, `preserve_recording` packages the accepted members only and adds one coverage line for each missing member, with its path and why it is absent. The acceptance in `evidence/controller.json` keeps the full list with lengths and digests, and the verifier requires exactly that coverage line for each. If a missing member is any file of the parent transcript generation under `session/`, the recording makes no claim about the parent: its capture reference is null and its coverage says the generation is not complete in custody and is not verified.

`preserve_recording(study, state, blobs, supplement=N)` packages a supplement: later observations collected after the first recording's collection froze. It is a second recording of the same run at `custody/RUN/supplements/NNNN/`, with its declaration in `evidence/supplement.json`, the first recording's custody receipt in the controller record, `supplement.sequence` and `supplement.follows_recording_sha256` in the manifest, and a coverage line saying that it neither repeats nor replaces the first. The first recording must be in custody before its supplement, and custody refuses a supplement that names any other predecessor. The verifier refuses a package whose capture receipt, declaration, sequence and named predecessor do not all agree, and a first recording whose capture receipt says it was collected for a supplement. `publish_edition` gives a supplement its own edition and its own selection pointer, so the run's first edition stays selected and unchanged.

Preservation returns a `berman.custody-recording/1` receipt. Its `relative_path` resolves under the study root. Its `package_sha256` identifies the exact ZIP and `manifest_sha256` identifies the manifest value under Berman's recording identity convention. The stored pair lives at `custody/RUN/recording.zip` and `custody/RUN/receipt.json`. The writer syncs both staged files and installs their enclosing directory with a single rename. A conflicting occurrence fails with its first accepted bytes available for inspection.

`verify_recording(study, recording)` checks the stored receipt and reopens the recording. `publish_edition` takes that receipt, verifies again and reads the retained ZIP members directly for the reading aids. Modes, link targets and recorded timestamps come from the verified package. The renderer never installs recorded symlinks or executes saved programs. Host path strings remain available for citation. Reading an absolute path in a receipt requires no access to that path.

Each edition is published under `archive/editions/SHA256.zip`. The pointer in
`archive/editions/selections/RUN.json` contains its schema and the ZIP digest.
Supplements use `RUN-supplement-N.json`. The publication receipt records both
paths relative to the study. A retry verifies the edition against its source
manifest and reuses it. If publication stops after installing the ZIP, a retry
can finish installing the pointer. Guest verification also checks the pointer
and edition against the recording and the ledger's publication receipt.

Temporary directories prefixed with `.incoming-` have no custody authority.
They can be inspected after interruption and removed with writers stopped.
Publication renders text and transcript indexes in memory.

## Importing a guest recording

Use the original guest recording ZIP and its independently retained SHA-256.
Select a guest study with the same collection UUID. Importing stores the original
bytes and a custody receipt. The controller allocates future numbers above
existing custody occurrences and the study's `reserved_through` floor.

Use the guest interpreter containing the controller package. This example takes `digest` from the trusted recording receipt and `bytes` from its byte count. The SSH destination and guest paths are installation settings.

```sh
ssh -F "$ssh_config" "$guest" \
  ".local/berman/bin/python -I -m berman.custody --root research/example --sha256 $digest --size $bytes" \
  < "$recording_zip"
```

Only substitute a validated lowercase SHA-256 digest and decimal byte count into the remote command. The importer reads exactly the declared length and checks for EOF and digest agreement. A premature disconnect or an extra byte fails before custody commits. If the acknowledgement is lost, replay the same byte stream with the same digest. For a file already staged in the guest, use `import_recording(study, path, expected_sha256=digest)`. Both entry points return the same recording receipt.

The importer accepts Berman's single-disk, comment-free stored or DEFLATE recording ZIPs. Bounds are 544 MiB each for uploaded and expanded bytes, 256 MiB per member, 16 MiB for the manifest, 8 MiB for the central directory and 16,384 members. It checks expansion lengths and member paths before the recording verifier checks exact membership, modes, hashes and retained authority. The original ZIP bytes are preserved. The receipt identifies bytes accepted by the guest. Establishing their historical origin depends on the provenance of the supplied digest.

## Reading and analysis

Pass the import receipt to `publish_edition(study, recording)`. The edition
contains every original recording member at its recorded path under
`berman-analysis/`, including the researcher-facing commission decision.
`recording.json` names and verifies the originals. The edition manifest
identifies the recording manifest by its exact byte digest and lists the
digests of each reading aid and of the modules that built them. The recording
receipt separately pins the exact recording ZIP.

```sh
berman recording verify /copies/recording.zip --sha256 "$recording_digest"
berman archive --path "/copies/$edition_digest.zip"
```

Use a reader supporting the artifact's format. This checkout writes
`berman.recording/3` and `berman.reading-edition/3`. Berman 0.26.5 cannot
read them. This reader also verifies published guest `recording/2` (split
profile) and single-recording `reading-edition/2` artifacts. Replaying
publication honors their original selection pointer. Importing an earlier
guest recording by itself can produce a new edition from its original bytes.

Keep the private analysis repository on guest-owned storage, outside the worker
packet, capture spool and project checkout. Cite the collection UUID, run,
supplement when applicable, member path and digest.
