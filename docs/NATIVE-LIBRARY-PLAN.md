# Native Berman and the producer’s library

Build one Rust application for Linux and macOS. On either machine the producer installs Berman alongside the actor’s usual CLI, chooses an existing project, and opens an episode. The actor works in that checkout with its usual settings. Lima gives a developer on a Mac a place to build and test the Linux distribution. Neither installation requires a guest, container, filesystem driver, or database server.

The producer should be able to leave the library on the same machine as the project. Suppose an instruction intended for project B reaches an actor in project A. A broad search for a filename or phrase may find project B. Berman’s retained handover, telemetry, and reviews should contribute zero matches. Discovering the installed app or its hook is fine. Someone who deliberately calls `berman help library` under the producer’s login should immediately learn how to browse the whole library, including from an unattended terminal.

## What the producer sees

Give each producer a library of directories and files. The following paths are **inside Berman**. A producer chooses names such as `cutover`. Internally an episode keeps its stable ID across directory renames.

```text
episodes/cutover/
  opening.md
  episode.json
  ending.json
  final-project.json
  return/HANDOVER.md
  sittings/
  telemetry/
  coverage.json
  manifest.json
reviews/cutover-question/
  question.md
  answer.md
  sources.json
  sources/
```

Make ordinary directory operations the interface. For example, the proposed `berman library ls episodes` lists the producer’s episodes and `berman library cat episodes/cutover/return/HANDOVER.md` writes the exact file to stdout. Help should demonstrate listing, reading, creating directories, copying in and out, renaming, and removing a file or directory. Perform these operations inside the encrypted store. An explicit export writes a real directory at a fresh destination for ordinary tools or another reader. Help, shell completion, and ordinary actor startup should expose project-specific names only after someone deliberately requests library contents.

Preserve the familiar [record and review layout](records.md). A producer may move a whole record or remove a member using directory operations. The original manifest continues to describe the bytes collected at closing. Removing or editing a member makes verification report the discrepancy. Copies may share stored bytes. Keep those bytes for as long as any record or review references them, so a verified review copy survives changes to its source. Defer transcript-specific retention commands and policy decisions. A producer who wants to remove a file can already name it.

## One store on both systems

Keep the shared Rust application and job templates under `src/berman/`. Share the CLI, directory operations, versioned schema, episode lifecycle, capture rules, record format, and verifier. Embed the same version of SQLCipher, the encrypted SQLite engine, in both builds. Keep directory names, project paths, file contents, and any indexes inside encrypted pages. Configure encrypted journals and write-ahead logs and keep SQLite temporary tables in memory. Stream reads to the caller. A library read should leave no plaintext checkout, preview cache, diagnostic containing a prompt, or desktop-search entry. [SQLCipher storage design](https://www.zetetic.net/sqlcipher/design/)

Use the producer’s Application Support directory on macOS and `$XDG_DATA_HOME/berman` (normally `~/.local/share/berman`) on Linux. Generate a random library key once. Use Keychain or a Linux Secret Service where it supports the login’s normal unattended use. Offer a user-owned, mode-`0600` key file at setup, including on headless Linux. The same login that runs Berman can read the library through its commands. Encryption keeps ordinary searches of disk bytes empty. Document how to back up and restore the database consistently with its key, and report a missing key without replacing an existing library. [Apple directory guide](https://developer.apple.com/library/archive/documentation/FileManagement/Conceptual/FileSystemProgrammingGuide/MacOSXDirectories/MacOSXDirectories.html) · [XDG directory specification](https://specifications.freedesktop.org/basedir/latest/)

## Where the actor leaves correspondence

Use a real, initially empty directory for each open episode. The actor can write `notes/`, update `HANDOVER.md`, or leave nothing. Give it the same path at every sitting, including after a reboot. Opening instructions, sitting logs, capture state, reviews, and archived returns belong in the producer’s library.

Allocate each return directory under an opaque name, outside the checkout, the harness home, and the library. Put the mapping in SQLite. Give the owning login search and write permission on the parent and omit directory-listing permission. This lets the actor use its exact path while an ordinary recursive walk from the home directory cannot enumerate other open returns. Keep live returns out of desktop indexing and check this on both platforms. The known path and any copies a producer explicitly exports remain readable.

Keep persistent hook configuration generic. Supply the opaque episode reference only to the launched sitting. Native harness histories can repeat an opening or response independently of Berman. Offer a dedicated harness home under a similarly unlisted, unindexed parent for producers who want those histories outside broad searches too. Qualify sign-in, hook review, and resume there. Document where each CLI keeps searchable history when a producer chooses their everyday harness home.

Collection happens at **explicit producer close**. A sitting exit, an actor’s final response, or a missing transcript leaves the episode and its return directory available for resumption.

1. Hold the episode against concurrent starts and closeouts, check for a live actor, record the producer’s ending and stop callback intake. Capture the final attributable observations. Read stable regular return files without following links.
2. Commit the correspondence under the episode’s `return/` directory with its original relative names and bytes. Publish the record, coverage, manifest and a pending-cleanup inventory durably. Read back and verify the committed record. Retrying with the original reason must converge on the same ending and record.
3. Retire only entries still matching the verified inventory. If a file changed, was unreadable, exceeded a limit, or arrived after collection, preserve it and report its path to the producer. A valid manifest can cover a partial collection. After an interruption, retry cleanup from the committed inventory. Successful full collection removes the episode’s working directory. With all episodes closed and cleanup complete, the correspondence area is empty.

## Telemetry at realistic sizes

Small returns can be stored directly. A long session transcript needs a different physical layout behind the same file interface. Stream large members into bounded encrypted chunks and record their order, length and digest. On each attributed callback, check whether the previous bytes are a prefix. Reuse verified chunks for the common prefix. Persist the attributed event even if the transcript is missing. When bytes are available, publish the generation and its references atomically. A rewrite or truncation retains displaced bytes, and an incomplete final line retains its own bytes. Preserve the derived reading copy and raw-line references. Stream unattended stdout and stderr into encrypted storage as they arrive so a retry can recover the sitting result. Keep observations distinct from actor correspondence and later interpretation. The [capture implementation](../src/berman/capture.rs) and [retention tests](../tests/capture.rs) specify what must survive.

Stream verification and export as well as ingestion. Keep the existing collection limits and coverage reporting during the port. Measure peak memory, database and journal high-water sizes, and callback latency against repeated appends, rewritten transcripts, and captures near the existing 256 MiB file and 1 GiB record limits. Doubling the number of observations of the same prefix should add history rows and references, with no additional full transcript copies. Exercise short native hook deadlines and disk-full recovery. The last committed generation must remain verifiable after an interrupted write.

## Migration and acceptance

1. **Establish the common behavior.** Use the current Python implementation and synthetic actor fixtures as references. Produce native Rust binaries from the same revision and exercise the producer workflow on the Mac and in the Linux guest. Keep Lima and the container packaging check under `development/`. Cover installation and build identification, packaged jobs, attended and unattended sittings, retry, interruption, resume, and close. Replace Linux-specific assumptions such as `/proc` process identity with a native check on each platform.
2. **Deliver the library and directory operations.** A new `open` takes a library destination and returns a stable episode reference. `start`, `resume`, `status`, `close`, and `review` resolve library references. Keep detached directory verification and exports. Test cross-platform verification in both directions, including detached records whose original project path is unavailable. Preserve stored path spellings and report collisions before exporting to a filesystem that cannot represent two distinct names.
3. **Move correspondence and capture.** Exercise concurrent episodes, empty returns, both additional jobs, interrupted publication, and interrupted cleanup. Repeat every failure case with the original request. Port the Codex and Claude Code adapters with their existing session attribution. A directly launched actor should receive no opening and generate no Berman observations. Review changed shared hooks in the actual CLI. Run native sittings on each platform with a producer-chosen project and actor, separately from routine synthetic checks. [Development checks](development.md)
4. **Prove the search behavior.** In disposable homes, put distinct filenames and phrases in project B’s return, telemetry, and review. Search from project A and from the home directory by filename and by content, including hidden and binary files. Include any enabled desktop index, live database journals, concurrent open returns, a producer reading through Berman, and recovery after a killed writer. Plant a separate ordinary-project match as a positive control. Require zero hits from Berman-managed retained material and from recursive discovery of sibling returns. Then call `berman help library` and follow its examples to recover the exact bytes under the same login. Account explicitly for direct reads of a known live return, native harness history, and deliberate plaintext exports.
5. **Bring existing producers across.** Verify and import a selected legacy closed record into a fresh library destination. Preserve the original manifest bytes and digest, verify an export against that digest, and leave the input intact. Carry selected review directories with their questions, answers, and verified sources. Finish open legacy episodes with the existing binary and reviewed hooks before switching that harness home. Identify remaining plaintext legacy directories so the producer can manage them. Switch the installation guides and default commands once both native builds pass the same checks. Record shipped changes under unreleased and keep release numbering on the existing schedule.
6. **Retire the Berman checkout in Lima.** Keep Berman’s development checkout on the Mac. Commit there and use the Mac’s Git credentials for maintainer-authorized pushes. Pass a source archive of the selected commit and its commit ID to a temporary directory in Lima. Verify that a fresh guest without a Berman clone or repository credentials can build that revision, run the Linux suite, and pass the container packaging check. Preserve any guest-only work before retiring the old checkout, and update the [Lima guide](../development/lima/README.md), [development checks](development.md), and [maintainer instructions](../AGENTS.md) at cutover.
