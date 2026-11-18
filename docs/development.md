# Developing Berman

Development uses Python 3.14 in a repository-local environment managed by
[uv](https://docs.astral.sh/uv/). Python 3.14 is the one supported and tested
version. From the repository root:

```sh
uv sync --locked
uv run ruff check
uv run ruff format --check
uv run lint-imports
uv run python -m unittest discover -s tests -t .
uv run berman verify
```

`uv sync` creates `.venv` from `.python-version` and `uv.lock`, installs Berman
in editable form and adds the development tools. Commit `uv.lock` whenever
`pyproject.toml`'s dependencies change. uv is a development tool only: the
wheel installs and runs with pip, and `python -m pip install -e . --group dev`
in a Python 3.14 environment installs the same tools, unlocked.

Use this checkout's environment explicitly (`uv run berman`, `.venv/bin/berman`).
A workstation that runs a study can have another `berman` on `PATH`;
`berman version` names the source it runs.

## What each check establishes

| Check | Exercises | Does not establish |
| --- | --- | --- |
| `berman verify` | The installed files' digests, the dependency closure and the packaged schemas. Exits 1 on a failure; `berman --json verify` returns `berman.software-verification/1`. | Anything about a study, a guest or a harness. |
| The five source checks above | Commissioning, staging, transfer, custody and rendering against disposable repositories, synthetic studies, harness doubles and simulated callbacks. | Native capture, hook trust or native access. No model is called. |
| [Mini-CI](ci.md), `uv run berman ci --config scripts/ci/qualification.json` | The source checks, one built wheel installed on the Mac and the Linux guest, one assignment over real SSH with a scripted worker, and detached verification of its recording and edition. | Anything a real harness does. |
| Mini-CI with `--live codex` or `--live claude`; `berman-research qualify RUN` | One native session of the installed harness on this machine: attributed callbacks, a staged transcript, the permission mode the harness reported. | Another machine, harness version or build. Hook trust is the operator's review in the actual CLI. |

Report a change with the checks that were run and the ones that were not. A
change to the executor, the connector or custody is not natively qualified by
any number of passing synthetic tests.

## Code map

The package follows the three parts of the application. The table groups
modules by concern; the import order between them is under
[Boundaries](#boundaries).

| Concern | Code | Notes |
| --- | --- | --- |
| Command line | `cli.py` (`berman`), `cheatsheet.py`, `__main__.py` | `cli.py` defines the standalone commands and hands `remote` and `executor` to their own parsers. `cheatsheet.py` builds the sheet as data. |
| Connection to the guest | `connector.py` (`berman-research`, `berman remote`), `protocol.py` | The connector turns each operator command into requests and calls the local executor. `protocol.py` is the envelope and inventory both sides share. [Protocol](protocol.md) |
| Native capture and execution, macOS | `executor/` | `__init__.py` prepares, launches, collects and qualifies, and holds the `berman executor` parser. `capture.py` routes hooks and stages transcripts. `checkout.py` runs project checks and snapshots documents. `sources.py` finds associated sessions. `snapshots.py` commits capture generations. `claude.py` and `codex.py` render reading copies. [Executor](executor.md) |
| Research side, Linux guest | `controller.py`, `study.py`, `custody.py`, `evidence.py`, `archive/` | The controller owns the ledger and every lifecycle decision; its `main` is what the connector starts over SSH. `study.py` reads recipes and plans. `custody.py` seals recordings and publishes editions, using the recording contract in `evidence.py`. In `archive/`, `builder.py`, `package.py` and `streams.py` build and verify editions, and `edition_v2.py` reads the earlier edition format. [Custody](custody.md) |
| Software identity | `verify.py`, `installation.py`, `environment.py` | What `berman verify` and `berman version` report. |
| Foundations | `files.py`, `schemas/` | File and digest helpers, and JSON Schema validation. |

### Adding or changing a command

- A standalone command of `berman` is declared in `cli.arguments` and handled
  by its own branch in `cli.main`.
- An operator command of `berman-research` is declared in
  `connector.arguments` and handled in `Operator.perform`. A new guest command
  also needs a branch in `Controller.execute` or `Controller.advance` and a row
  in the [protocol table](protocol.md#requests-and-receipts).
- `cheatsheet` is the one command in both. `cli.main` forwards
  `berman cheatsheet` to `connector.main`, which answers it before reading any
  settings, because it has to work with no study configured. A command that
  must run without a study follows that pattern, not `Operator.perform`.
- Add a line to `cheatsheet.py` if an operator would look for the command
  there.

Commands that return a record print JSON (`version`, `recording`, `archive`,
and every operator command except `closeout`). `verify`, `cheatsheet` and
`closeout` print text for a person and JSON with `--json`. `--config` comes
before an operator command; `cheatsheet` also takes it afterwards.

### What an installation contains

The wheel carries the package's `.py` files and `schemas/*.json`, nothing
else. `docs/`, `examples/` and `tests/` are in the source distribution only,
so installed code cannot read them: text a command prints lives in a Python
module. `berman verify` and `berman version` digest the installed `.py` and
`.json` files, and the executor's receipts digest the `.py` files. A data file
of any other type would be outside both identities.

## Boundaries

Import Linter holds four contracts. `pyproject.toml` is their authority; this
is the layer order it declares, where a module may import only from lines
below its own:

```text
cli
connector
cheatsheet
controller | executor | verify
custody | study
protocol
evidence : archive
files | installation | environment | schemas
```

Modules joined by `|` never import each other. So the controller and the
executor never do: they run on different machines and meet only through
`protocol.py`. The other three contracts:

- The executor and everything it imports use the standard library only. Its
  hook runs on every turn of a fieldworker's session.
- The archive builder uses the standard library only.
- `jsonschema` is imported only by `schemas` and `packaging` only by
  `environment`.

Every contract has a deliberate violation in `tests/unit/test_architecture.py`.
Give each new module a line in the layers and each new contract a violation.

### Byte-sensitive files

Each reading edition records the digests of `archive/builder.py`,
`archive/package.py` and `archive/streams.py`, the modules that built it. Keep
them out of formatting-only changes; the formatter is configured never to touch
them. The other files in the formatter's exclusion list in `pyproject.toml`
were written before the formatter was adopted. Format one, and remove it from
the list, when changing it.

The executor records the digest of every installed module in each preparation
and launch receipt. Any change to the package therefore gives an installation
new bytes, and a native qualification applies only to the bytes it observed.

## Record schemas

The controller validates connection, recipe and plan documents when
commissioning. The JSON Schemas describe their operational fields; studies can
add annotations. Validation returns the supplied document unchanged. The
portable readers check the recording and edition contracts against the retained
members and receipts.

## Tests

Tests use disposable repositories and studies, harness doubles and simulated
callbacks. Each module below drives the layer it is named for.

| Tests | Cover |
| --- | --- |
| `integration/test_cli.py` | The standalone commands as subprocesses in an empty home, including the cheatsheet with no study and with an unreachable guest. |
| `integration/test_connector.py` | Operator commands end to end against an in-process guest and a harness double. It also parses every command the cheatsheet prints with the real parsers. |
| `integration/test_controller.py`, `test_custody.py`, `test_portable_recordings.py` | The guest: ledger and lifecycle, recordings and editions, and verifying and materializing copies. |
| `integration/test_executor.py`, `test_bounded_collection.py` | The executor: preparation, sittings, hooks and collection; document snapshots and associated sessions. |
| `integration/test_installation.py`, `support/wheel_smoke.py` | The example study through the installed entry points. |
| `unit/test_architecture.py`, `test_boundaries.py` | The import contracts, each with a violation, and what they cannot express. |
| `unit/test_guidance.py` | Every link and every command written in the README, AGENTS.md, the example and `docs/*.md` exists. It does not check that a command is documented. |
| `unit/test_capture_readers.py`, `test_snapshot_retention.py`, `test_reading_editions.py` | Reading copies and hook recognition, capture generations, and edition contents. |
| `unit/test_schemas.py`, `test_environment.py`, `test_ci.py`, `test_release_artifacts.py` | Record schemas, the dependency check, the mini-CI's own logic, and the contents of `dist/`. |
| `properties/` | Hypothesis over plan sequences, file preservation and deterministic packaging. Examples are fixed by default; run exploratory cases with `BERMAN_HYPOTHESIS_PROFILE=explore` and keep a failure as an ordinary regression test. |

`tests/fixtures/split-0.26.4/` holds a synthetic recording, edition and
selection pointer in the `recording/2` and `reading-edition/2` formats, which
the custody tests reopen and republish. `tests/fixtures/records/` has one
folder of valid and invalid documents per schema. `tests/fixtures/ci/` is the
mini-CI's study, project and scripted worker.

## Building

`uv build` writes a source distribution and a wheel to `dist/`. Install the
wheel with pip in a fresh virtual environment and run it from outside this
repository:

```sh
python3.14 -m venv /tmp/berman-check
/tmp/berman-check/bin/python -m pip install dist/berman-*.whl
cp tests/support/wheel_smoke.py /tmp/berman-check/
cp -R examples/minimal-study /tmp/berman-check/example
cd /tmp && /tmp/berman-check/bin/python /tmp/berman-check/wheel_smoke.py --example /tmp/berman-check/example
```

`wheel_smoke.py` commissions and closes a synthetic guest study through the
installed controller's entry point, with a hand-made collection receipt
standing in for the executor, then verifies the recording and edition from
copies.

## Releases

Versions are `0.YY.N`: the two-digit release year and a serial within it,
beginning at 1. A version names a released artifact. It does not encode
severity, compatibility, maturity or progress.

- Any `0.*` release may make breaking changes. Never choose a number by what a
  change does, and never increment it because source changed.
- Increment the serial only when explicitly preparing a release. The tree
  between releases keeps the last release's number in `pyproject.toml`;
  `berman version` tells two trees apart by their source digests.
- Released artifacts and tags are immutable. A replacement takes a new number.
- Add no pre-release stage, migration guarantee or compatibility accommodation
  without explicit authorization.
- Version `1.0.0` is reserved for a decision to establish a stable contract.
  Numbers below 0.26 predate this scheme and carry no meaning under it.

Notes for each release stay with it under `releases/`; the latest are the
[0.26.6 release notes](releases/0.26.6.md). Between releases, collect what the
next one will need to say in `releases/unreleased.md`, and rename that file
when the release is prepared.

GitHub is the backup of released artifacts: every release from 0.4.4 on stays
there with its wheel, source distribution and `SHA256SUMS`, and none is
deleted. Locally `dist/` is ignored by Git and holds only the build of the
version in `pyproject.toml`; clear it before `uv build`. The suite fails while
`dist/` holds another version's files.

### Adopting a release in a study

Adoption is the study's decision, not a consequence of a commit here. Keep the
wheel and its dependency wheels with the adoption record. Close active captures
before replacing a runtime. Install the same build on the guest and the host
and record `berman version` on both. A changed executor gives the host new hook
commands and new bytes: review the hooks in the actual CLI and take native
evidence from the adopting installation
([qualification](executor.md#qualification-evidence)). Use the guest verifier
on previously published artifacts.
