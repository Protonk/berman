# Berman

Berman supports intermittent participant observation of software development.
A designated fieldworker does real project work and keeps an account of it.
Berman prepares the assignment, checks access, captures the designated session
and preserves its records for analysis.

Ordinary development is separate from fieldwork. Research materials remain
outside workers' access except for the assigned packet. Engagements begin by
research decision and end by an explicit declaration; a session callback alone
never declares the research complete.

This repository contains the reusable software. Each study keeps its own
project connections, research plans, instructions, recordings and analysis.
You can develop and test Berman using the included synthetic examples.

## Getting started

Python 3.11 or later and Git are required. The implementation uses the Python
standard library. Native access and capture depend on separately installed Codex
or Claude CLIs and their sandbox facilities; see [current limits](LIMITS.md).

Check the version and run the software checks:

```sh
./berman version
./berman verify
```

Create a workspace for a study and connect it to a project:

```sh
./berman init /path/to/study --project /path/to/project --connection example
./berman --root /path/to/study status
```

`init` creates separate harness homes inside the study, an empty inventory and a
connection with no assigned plan. It installs no hooks, changes no project files
and starts no session. Read [the fieldwork guide](BERMAN.md) before assigning a
plan or qualifying an installation.

`verify` in this source checkout checks software only. To check a configured
study, use `./berman --root /path/to/study verify`. Passing software tests does
not establish native access or capture.

A study can invoke this executable by absolute path, or `berman install` from a
selected study can link it into `~/.local/bin`. Workspace selection uses `--root`
or the nearest `berman.json` in the current repository; it stops at Git boundaries.

## Software, fieldwork and analysis

Berman keeps the tools, collected material and interpretation independently
manageable:

| Repository | Responsibility |
| --- | --- |
| Core software | Workflow, capture, access controls, preservation, reading editions and tests. |
| Fieldwork | Assignments, project connections, original recordings, inventories and installation evidence. |
| Analysis | Questions, excerpts, comparisons and findings, linked to their fieldwork sources. |

New operations record the software version and source digests. Existing studies
can retain a pinned runtime until an explicitly qualified upgrade. A separate
analysis repository must remain within a denied research root or be covered by
an equivalent qualified access policy.

The [maintenance guide](MAINTENANCE.md) covers development checks and
compatibility. [Source provenance](ORIGIN.json) records where this code began;
the original research ledger retains its history separately.
