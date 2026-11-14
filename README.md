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

Python 3.14 and Git are required. Berman has three runtime dependencies, which
pip installs with it: `jsonschema` checks the structure of the records it reads
and writes, `rich` lays out its output on a terminal, and `packaging` interprets
the dependency requirements checked by software verification. Capture hooks, sandbox
probes and the archive builder use the standard library only. Native access and
capture depend on separately installed Codex or Claude CLIs and their sandbox
facilities; see [current limits](docs/limitations.md).

Install from this checkout in a virtual environment:

```sh
python3.14 -m venv .venv
. .venv/bin/activate
python -m pip install .
berman version
berman verify
```

To work on Berman itself, use the locked environment the
[development guide](docs/development.md) describes instead.

Create a workspace for a study and connect it to a project:

```sh
berman init /path/to/study --project /path/to/project --connection example
berman --root /path/to/study status
```

`init` creates separate harness homes inside the study, an empty inventory and a
connection with no assigned plan. It installs no hooks, changes no project files
and starts no session. The [minimal study example](examples/minimal-study/README.md)
shows the configuration formats. Read [the fieldwork guide](docs/usage.md) before
assigning a plan or qualifying an installation.

`berman verify --software` checks the installed package. To check a configured
study, use `berman --root /path/to/study verify`. Developer tests run separately;
neither software verification nor tests establish native access or capture.
Study verification includes a read-only audit of preserved originals, ending
evidence and recording ZIPs. Missing historical authority stays unestablished;
verification never invents an ending or repairs preserved material.

On an interactive terminal, `status`, `verify`, `cheatsheet` and the result of
`new` are laid out for reading. Redirected to a file or a pipe they print the
same plain text as before, and `BERMAN_OUTPUT=plain` keeps that text on a
terminal too. `--json` gives each command's result for programs.

Both `berman` and `python -m berman` work outside the checkout. Workspace selection
uses `--root` or the nearest `berman.json` in the current repository; it stops at
Git boundaries. Keep the environment installed while a study uses it.

## Software, fieldwork and analysis

Berman keeps the tools, collected material and interpretation independently
manageable:

| Repository | Responsibility |
| --- | --- |
| Core software | Workflow, capture, access controls, preservation, reading editions and tests. |
| Fieldwork | Assignments, project connections, original recordings, inventories and installation evidence. |
| Analysis | Questions, excerpts, comparisons and findings, linked to their fieldwork sources. |

New operations record the software version, its source digests and the
interpreter and dependency versions it ran with. Existing studies
can retain a pinned runtime until an explicitly qualified upgrade. A separate
analysis repository must remain within a denied research root or be covered by
an equivalent qualified access policy.

The [development guide](docs/development.md) covers tests, packaging and
compatibility. [Source provenance](docs/provenance/origin.json) records where this code began;
the original research ledger retains its history separately.

Berman is released under the [MIT License](LICENSE).
