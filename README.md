# Berman

Berman supports intermittent participant observation of software development.
A designated fieldworker does real project work and keeps an account of it.
Berman commissions the assignment, checks native execution, captures the
designated session and preserves its records for analysis.

Ordinary development is separate from fieldwork. The research environment holds
private plans, history and analysis outside the ordinary project checkout. A
fieldworker receives an assignment and keeps notes. Recognizing the observation
apparatus is compatible with continued participation. Engagements begin by
research decision and end by an explicit declaration; a session callback alone
never declares the research complete.

This repository contains the reusable software. Each study keeps its own
project connections, optional research plans, instructions, recordings and analysis.
You can develop and test Berman using the included synthetic examples.

## One lifecycle

Fieldwork is commissioned, prepared, launched, captured, ended, collected and
preserved through one path only. The Linux controller owns commissioning,
endings, custody and publication. Its macOS executor runs the designated
harness and stages observations locally. The operator uses `berman-research`
(the same program as `berman remote`) from an ordinary terminal. See the
[command and receipt guide](docs/connector.md),
[native execution](docs/executor.md),
[custody](docs/custody.md) and
[guest deployment and historical import](docs/guest-deployment.md).

Releases up to 0.26.3 also carried a co-located lifecycle, in which the research
records and the worker's packets shared one workspace on one machine account.
This version does not: it creates no such workspace and prepares, launches,
ends, supplements or migrates nothing in one. An existing co-located workspace
is [read-only here](#co-located-workspaces).

## Getting started

Python 3.14 and Git are required. Berman has three runtime dependencies, which
pip installs with it: `jsonschema` checks the structure of the records it reads,
`rich` lays out its output on a terminal, and `packaging` interprets
the dependency requirements checked by software verification. The executor's hook
routing and the archive builder use the standard library only. Native execution
and capture depend on separately installed Codex or Claude CLIs; see
[current limits](docs/limitations.md).

Install from this checkout in a virtual environment:

```sh
python3.14 -m venv .venv
. .venv/bin/activate
python -m pip install .
berman version
berman verify
```

Without a workspace, `berman verify` checks the installed package, as
`berman verify --software` always does. To work on Berman itself, use the locked
environment the [development guide](docs/development.md) describes instead.

For fieldwork, create the study and macOS binding as described in the
[deployment guide](docs/guest-deployment.md), then discover the study from the host.

```sh
berman-research status
```

The [fieldwork guide](docs/usage.md) follows one engagement from commission to
a verified reading edition and says what each remaining `berman` command reads.

## Co-located workspaces

A co-located workspace is a directory with `berman.json` made by release 0.26.3
or earlier. This version reads one as historical evidence and never operates it.

```sh
berman --root /path/to/study status
berman --root /path/to/study verify
berman --root /path/to/study recording export --run run-008 --historical --output /copies/run-008.zip
berman --root /path/to/study archive --verify
```

`status` reports what the workspace holds and what in it is unsettled: a
reserved, prepared or open engagement, an ending whose preservation was never
finished, a pending combined request, an open support trial. Anything unsettled
has to be closed with the release that opened it, and this version says so and
does nothing more. Keep that released runtime installed for the workspace until
nothing is outstanding. No ending, package or completeness is inferred for a
historical record.

`verify` checks recorded hashes, that the workspace's records load, whether
anything is unsettled, the preserved recordings and the published reading
edition. It runs no harness and establishes no hook trust, native capture or
native access. Missing historical authority remains explicit coverage
uncertainty; verification never invents an ending or repairs preserved material.

To continue a study, import its recordings into guest custody as the
[deployment guide](docs/guest-deployment.md#historical-studies) describes, and
commission new work there.

On an interactive terminal, `status` and `verify` are laid out for reading.
Redirected to a file or a pipe they print plain text, and `BERMAN_OUTPUT=plain`
keeps that text on a terminal too. `--json` gives each command's result for
programs.

Both `berman` and `python -m berman` work outside the checkout. Workspace selection
uses `--root` or the nearest `berman.json` in the current repository; it stops at
Git boundaries.

## Software, fieldwork and analysis

Berman keeps the tools, collected material and interpretation independently
manageable:

| Repository | Responsibility |
| --- | --- |
| Core software | Controller, executor, capture, custody, reading editions, readers of historical workspaces and tests. |
| Fieldwork | Assignments, project connections, original recordings, inventories and installation evidence. |
| Analysis | Questions, excerpts, comparisons and findings, linked to their fieldwork sources. |

Each native preparation and launch records the software's source digests and the
interpreter it ran with. Keep analysis in its own research-side repository and
transfer only the materials required for the assignment.

The [development guide](docs/development.md) covers tests, packaging and
compatibility. [Source provenance](docs/provenance/origin.json) records where this code began;
the original research ledger retains its history separately.

Releases are numbered `0.YY.N` by year and serial; the number implies nothing
about compatibility ([release numbering](docs/development.md#release-numbering)).

Berman is released under the [MIT License](LICENSE).
