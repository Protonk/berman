# Reading an episode

An episode’s directory in the library is its record once the producer has closed it. Start with `opening.md` for what the actor received first and `ending.json` for the producer’s reason. Each sitting under `sittings/` records the message that started it and, for an unattended sitting, the response the actor’s CLI reported. Read `return/` for what the actor deliberately left. An empty return is possible. The producer can act on what came back, ask a new question, or let the project proceed independently.

```sh
berman library ls episodes/notes
berman library cat episodes/notes/return/HANDOVER.md
```

A record contains only these members.

| Within a record | What it can tell you |
| --- | --- |
| `opening.md`, `ending.json` | The message the actor received and the producer’s reason for closing. |
| `return/` | The regular files the actor left in the return directory. |
| `episode.json`, `final-project.json` | The opening, actor, project path, and the Git revision and worktree status Berman observed at the opening and at closing, where available. |
| `sittings/` | Which sittings were requested and with what message, whether each delivered the opening or was attended, which executable started, and which exits the launcher observed. An unattended sitting also keeps its native output and the response, denials and limitations Berman read in it. |
| `telemetry/events/` | Callbacks attributed to the bound native session. An event that arrives before the transcript exists says so and is not counted as a gap. |
| `telemetry/parent/` | The available parent JSONL, a readable conversation with raw-line references, and the capture history. |
| `telemetry/displaced/`, `telemetry/tails/` | Earlier transcript versions that a later capture did not extend, and incomplete final lines, when observed. |
| `coverage.json` | Scope, missing observations, and entries that could not be collected. |
| `manifest.json` | The episode identity and SHA-256 digests of all retained members. |

The sittings and telemetry are in the episode’s directory from the first sitting, so a producer can read them while the episode is open. `return/`, `ending.json`, `final-project.json`, `coverage.json` and `manifest.json` arrive at closing.

A transcript may omit conversations, helpers, workspace activity or the last turn. Git observations list the state of the checkout at two times. They do not say who made the changes between them: people, other sessions and tools can change the checkout during an episode, and so can the actor. A reported response is what the actor’s CLI said at the end of a sitting, not a confirmation that the work was done. A file in the actor’s return is an account the actor elected to give. Consider these distinctions when using any of them as evidence. The record can contain private project material and session text. Choose its readers accordingly.

## Verify a record or a copy

`berman verify RECORD` checks a record against its manifest. Name a record in the library by its path or episode ID. Name a directory on disk with a path that begins with `/`, `./`, `../` or `~/`. Add `--sha256 DIGEST` to compare it to a separately held manifest digest. A failed verification names the members that are missing, changed or unlisted.

```sh
berman verify episodes/notes
berman library export episodes/notes "$HOME/notes-record"
berman verify "$HOME/notes-record" --sha256 DIGEST
```

A record has the same members and manifest in the library and in an exported directory, on Linux and on macOS, so a copy made on one system verifies on another without the original project. An internal check can pass on an incomplete capture. The return is bounded to regular files, with up to 256 MiB per file and a limit of 4,096 files and 1 GiB across the return. Collection records omissions. Sittings and telemetry are already in the episode when it closes and are kept whole. A transcript is captured up to 256 MiB. If the captured transcript does not agree with its own description at closing, the coverage says so and its members are kept as found.

## Ask a question across episodes

```sh
berman review episodes/first episodes/second \
  --question "Which decisions still depend on an assumption?" \
  --output reviews/assumptions
```

Berman creates a fresh directory in the library, verifies the selected records and copies them under `sources/<episode-id>/`. A selected record can be in the library or a directory on disk. The selection and manifest digests are in `sources.json`; the reader writes in `answer.md`. Cite paths within that directory and, for a conversation, the passage or raw line supporting a conclusion. Answering the question is work for the reader. The command runs locally and makes no model call.

Choose how the review reaches its audience. A producer can read it in place with `berman library cat` and put an answer back with `berman library put --replace`. `berman library export reviews/assumptions DESTINATION` writes an ordinary directory for another reader, who can later put a decision into the project’s usual decision log. [The producer’s library](library.md)
