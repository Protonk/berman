# Running fieldwork

A fieldwork workspace holds berman.json, named connections, versioned recipes,
executable research plans, operations and preserved runs. The software checkout
is independent. Select a workspace with `berman --root PATH status` or run the
command from within that workspace's repository.

## Prepare an installation

`berman init PATH --project PROJECT --connection NAME` creates a new workspace.
The project and live packet root must be disjoint from the workspace. Its
Codex and Claude configuration homes are private directories under .state/;
authentication and qualification are separate operator steps. Configure executable
paths in berman.json. For Claude use CLAUDE_CONFIG_DIR naming that private home.

The generated connection selects README.md and AGENTS.md as context and has no
research plan. Choose the actual documents, commission and project checks for
your project. A recipe uses schema berman.recipe/2, declares its connections and
recording protocol, and names INSTRUCTIONS.md plus COMMISSION.md. Instructions
contain exactly one `{{notes_directory}}` placeholder. The synthetic files under
[tools/fixtures/workspace](tools/fixtures/workspace) show the formats; they are
examples for software testing, not research assignments to adopt automatically.

A plan uses berman.research-plan/1, an id, a connection and ordered steps with
an id and recipe. Select its relative path in the connection's berman.json entry
only when those engagements are wanted. Instructions are retained at reservation;
changing a recipe does not change an already prepared assignment.

Before fieldwork, install and review the generated router definition with
`berman capture install --adapter berman-codex`, review /hooks in the actual CLI,
and use `berman capture record-trust --evidence FILE --attest`. A native support
trial demonstrates callback capture. `berman support-trial --help` describes
preparation, launch, collection and cleanup. A model session requires the
operator's decision. Claude uses separately qualified per-launch settings.

## Engagements

`berman new` prepares the next assigned step without choosing a run number.
`berman cheatsheet` prints current launch commands. `berman launch --run RUN`
starts a prepared session. `berman status` reports outstanding work.

`berman finish --reason TEXT` records the researcher's actual ending decision
and preserves the encounter. Capture finalization alone is not an ending.
`berman supplement --reason TEXT` retains later activity after a frozen capture.
Use the workflow rather than manually repeating profile, packet or migration steps.

## Reading editions

Build a generic edition explicitly against a workspace:

```sh
python3 -I -B tools/build_archive.py --root /path/to/study
python3 -I -B tools/build_archive.py --root /path/to/study --verify
```

The default selects the workspace's recorded connections. `--connection NAME`
and `--run RUN` narrow it. Its edition.json declares additional relative document
paths to include. The generic payload contains inventoried runs, their selected
inventory, chosen documents and generated aids; it does not collect configuration,
hook receipts or software merely because they share a directory.

`--edition stewart` preserves the explicit historical Stewart packaging behavior.
Its workspace must supply the legacy context documents. Never run a builder
against a Git worktree of a preserved collection: Git does not restore recorded
modification times.
