# Reading an episode

Start with `opening.md` for what the actor received first and `ending.json` for the producer’s reason. Each sitting under `sittings/` records the message that started it and, for an unattended sitting, the response the actor’s CLI reported. Read `return/` for what the actor deliberately left. An empty return is possible. The producer can act on what came back, ask a new question, or let the project proceed independently.

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

A transcript may omit conversations, helpers, workspace activity or the last turn. Git observations list the state of the checkout at two times. They do not say who made the changes between them: people, other sessions and tools can change the checkout during an episode, and so can the actor. A reported response is what the actor’s CLI said at the end of a sitting, not a confirmation that the work was done. A file in the actor’s return is an account the actor elected to give. Consider these distinctions when using any of them as evidence. The record can contain private project material and session text. Choose its readers and location accordingly.

`berman verify RECORD` checks a directory copy against its manifest. Add `--sha256 DIGEST` to compare it to a separately held manifest digest. An internal check can pass on an incomplete capture. The return is bounded to regular files, with up to 256 MiB per file and a shared limit of 4,096 files and 1 GiB across copied returns, sittings, and telemetry. Collection records omissions.

## Ask a question across episodes

```sh
berman review FIRST/record SECOND/record \
  --question "Which decisions still depend on an assumption?" \
  --output "$HOME/reviews/assumptions"
```

Choose the destination for the audience. A private working directory can hold a producer’s investigation. A reader can later put a decision into the project’s usual decision log. Berman creates a fresh directory, verifies the selected records and copies them under `sources/<episode-id>/`. The selection and manifest digests are in `sources.json`; the reader writes in `answer.md`. Cite paths within that directory and, for a conversation, the passage or raw line supporting a conclusion. Answering the question is work for the reader. The command runs locally and makes no model call.
