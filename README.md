# Berman

Berman establishes, maintains and preserves intermittent access to software
development through designated participant observers. A fieldworker does real
project work and keeps an account of it. Berman prepares the assignment, checks
access, captures the designated session and preserves its records for analysis.

Ordinary development is separate from fieldwork. Research materials remain
outside workers' access except for the assigned packet. Engagements begin by
research decision and end by an explicit declaration; a session callback alone
never declares the research complete.

This repository contains the reusable machinery. Each study owns its connection,
research plans, instructions, recordings, installation state and analysis.
There is no Stewart dataset or installation configuration in this checkout.
The synthetic tests retain some historical names to check format compatibility.

## Use the source

Python 3.11 or later and Git are required. The implementation uses the Python
standard library. Native access and capture depend on separately installed Codex
or Claude CLIs and their sandbox facilities; see [limits](LIMITS.md).

```sh
./berman version
./berman verify
./berman init /path/to/study --project /path/to/project --connection example
./berman --root /path/to/study status
```

`verify` in this source checkout checks software only. To check a configured
study, use `./berman --root /path/to/study verify`. Those are different claims:
passing software tests does not establish native access or capture.

`init` creates separate harness homes inside the study, an empty inventory and a
connection with no assigned plan. It installs no hooks, changes no project files
and starts no session. Read [the fieldwork guide](BERMAN.md) before assigning a
plan or qualifying an installation.

A study can invoke this executable by absolute path, or `berman install` from a
selected study can link it into `~/.local/bin`. Workspace selection uses `--root`
or the nearest `berman.json` in the current repository; it stops at Git boundaries.

## Three independent records

| Repository | Responsibility |
| --- | --- |
| Core software | Capture, workflow, access controls, preservation, rendering and tests. |
| Fieldwork | Exact assignments, connection definitions, operational records, originals, inventories and native qualification. |
| Analysis | Questions, excerpts, comparisons and findings, with references to fieldwork collections. |

New operations record the software version and source digests. Existing studies
can retain a pinned runtime until an explicitly qualified upgrade. A separate
analysis repository must remain within a denied research root or be covered by
an equivalent qualified access policy.

The [maintenance guide](MAINTENANCE.md) describes source checks and compatibility.
[ORIGIN.json](ORIGIN.json) records the extraction revision and original source
hashes. The original research ledger remains separate with its history intact.

## Publication

This is an independent Git repository, ready to be named `berman` on GitHub.
From this checkout, publication can later use:

```sh
gh repo create berman --public --source=. --remote=origin --push
```

No remote is configured by Berman. Software publication does not publish the
fieldwork or analysis repositories and does not upgrade a working installation.
