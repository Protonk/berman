# The producer’s library

Each login that runs Berman has one library. It holds the producer’s episodes and reviews as directories and files, and the producer chooses the names.

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

`berman help library` shows how to list, read, create, copy in and out, rename and remove, with an example of each. The commands work from an unattended terminal under the login that owns the library. An episode keeps its ID wherever its directory goes, and `start`, `resume`, `status`, `close`, `verify` and `review` take either the ID or the current path.

## Where it is kept

The library is one encrypted file, `library.db`, in the login’s application data: `~/Library/Application Support/Berman` on macOS, and `$XDG_DATA_HOME/berman`, normally `~/.local/share/berman`, on Linux. Directory names, file contents, project paths and indexes are all inside encrypted pages, and so are the journal files that appear beside it while a command runs. Reading a file streams it to the caller and leaves no plaintext copy. `berman library where` prints the locations for this login. Set `BERMAN_LIBRARY` to a directory to keep a library somewhere else.

The key is 32 random bytes, generated once by `berman library init`. By default it goes in the login keychain on macOS and in a Secret Service on Linux, through `security` and `secret-tool`, so the item stays readable after Berman is upgraded. A callback during an unattended sitting needs the key without a person present. Where the system store is locked in such sessions, as a keychain is over SSH, use `berman library init --key file` instead. The key file is `library.key` beside the library, readable by its owner alone. Whoever can run commands as that login can read the library either way. The encryption keeps its contents out of searches of the disk.

A missing key is reported as missing. Berman does not create a second key or replace a library that already exists.

To back up, copy `library.db`, any `library.db-wal` beside it, and `library.json` together while no Berman command is running, and keep a copy of the key from where `berman library where` says it is. Restore them to the same places. A backup without its key cannot be read.

## What a search of the disk finds

Suppose an instruction meant for one project reaches an actor working in another, and it searches the home directory for a filename or a phrase.

| Material | A search from the home directory |
| --- | --- |
| Episodes and reviews in the library | Finds nothing: no names and no contents, including in journals and after an interrupted write. |
| The return directory of an open episode | Does not come across it. Its parent can be entered but not listed. Whoever has the exact path, as the episode’s actor does, can read it. |
| The actor CLI’s own session history | Finds it in an everyday harness home. A [dedicated home](operating.md#set-up-the-actor) is under a parent that cannot be listed. |
| A directory you exported | Finds it where you put it. An export is ordinary files. |
| Job files, and anything else you keep on disk | Finds them. They are yours and outside the library. |

The installed program, the hook command and Berman’s own directory can be found. They name no project. Lock files beside the library are named by episode ID.

On macOS the return directories and dedicated homes are under `working.noindex` in the library’s directory, which Spotlight does not index. On Linux they are under `$XDG_STATE_HOME/berman`, normally `~/.local/state/berman`. To remove those directories by hand, first make their `returns` and `homes` parents listable with `chmod u+r`.

## Arranging and removing

A whole record can be moved or copied, and any member can be removed by name. The manifest continues to describe the bytes collected at closing, so verification of a changed record names each member that is missing, changed or unlisted. A copy shares stored bytes with its source, and bytes stay for as long as any file refers to them. A review’s verified copy of a record therefore survives changes to the record and its removal.

An open episode’s directory cannot be removed while its return directory exists. Close the episode first.

Stored names keep their spelling. Before an export writes anything, it creates every name at the destination. If that filesystem cannot hold two of the library’s names side by side, for example names that differ only in case, the export reports them and leaves nothing behind.

## Records from earlier releases

Releases through 0.26.9 kept each episode as a plaintext directory with its record inside. `berman legacy DIRECTORY` lists the episode, record and review directories below a directory and says which records the library already holds. Close any episode that is still open with the release that opened it, using the hooks reviewed for that release.

```sh
berman legacy "$HOME/episodes"
berman import "$HOME/episodes/notes/record" episodes/notes
berman import "$HOME/reviews/handoff" reviews/handoff
```

`berman import` verifies a record directory, copies it to a fresh place in the library with its original manifest bytes, and verifies the copy against the original digest. A review directory is carried with its question, answer and verified sources. The input is left as it was. The plaintext directories remain yours to keep or delete.
