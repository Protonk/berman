# Developing Berman

Development uses Python 3.14 in a repository-local environment managed by
[uv](https://docs.astral.sh/uv/). Python 3.14 is the one supported and tested
version. From the repository root:

```sh
uv sync
uv run ruff check
uv run ruff format --check
uv run lint-imports
uv run python -m unittest discover -s tests -t .
uv run berman verify --software
```

`uv sync` creates `.venv` from `.python-version` and `uv.lock`, installs Berman
in editable form and adds the development tools. `uv python install --no-bin 3.14`
supplies the interpreter without putting a `python3.14` on `PATH`. Commit
`uv.lock` whenever `pyproject.toml`'s dependencies change.

uv is a development tool only. The wheel installs and runs with pip, and a
checkout can be developed with pip alone: `python -m pip install -e . --group dev`
in a Python 3.14 environment installs the same tools, unlocked.

Tests create disposable repositories, synthetic packets and simulated callbacks.
They do not spend model tokens or establish native capture. The installed
`berman verify` checks package integrity and, when selected, a fieldwork workspace.
It never runs the developer tests or requires this checkout.

## What each tool is for

| Tool | Kind | What it enforces | Where it is configured |
| --- | --- | --- | --- |
| Ruff | development | Lint on everything but the frozen collector; formatting of files written since it was adopted. | `[tool.ruff]` in `pyproject.toml` |
| Import Linter | development | Eight architectural contracts over the import graph. | `[tool.importlinter]` in `pyproject.toml` |
| JSON Schema (`jsonschema`) | runtime | The structure of nine record families, where they are read and written. | `src/berman/schemas/` |
| Hypothesis | development | Behavioral invariants over generated inputs and operation sequences. | `tests/properties/`, `tests/support/properties.py` |
| Rich | runtime | Operator-facing output on an interactive terminal. | `src/berman/presentation/terminal.py` |
| Packaging | runtime | Python requirements, versions and environment markers for installation verification. | `src/berman/environment.py` |

The [integration plan](plans/python-314-dependencies.md) records why each is
used as it is, and the decisions taken on the way.

## Byte-sensitive files

Some files are identity as well as source. Leave their bytes alone unless a
defect requires a change, and then say what the change costs.

| Files | What their bytes identify | Cost of a change |
| --- | --- | --- |
| `src/berman/_legacy/capture_session.py` | The frozen historical collector. | Never changed. |
| Each adapter's `implementation` list in `capture/adapters.py`: the hook entry points and what they import, `installation.py`, `files.py`, `harnesses/claude.py`; and four launcher helpers fingerprinted by their source | Hook trust and native capture. | Renewed qualification in every adopting study. |
| `src/berman/archive/*.py` | A reading edition's identity. | A rebuilt edition. |
| `harnesses/codex.py`, `harnesses/probe.py`, `harnesses/development/launch.py`, `harnesses/development/probe.py` | Access receipts and installed launcher copies. | Rechecked automatically, or on reinstallation. |

Ruff never formats these files, and a test keeps them on the formatter's
exemption list. Lint findings in them that are only style are ignored per file
and per rule in `pyproject.toml`; a new kind of finding still surfaces and gets
a decision. Schema validation and presentation are attached elsewhere, so none
of these files imports `jsonschema` or `rich`.

## Formatting and lint

Ruff's formatter governs files written since it was adopted. Files written
before are listed under `[tool.ruff.format] exclude`: formatting them all would
have rewritten most of the code for no change in behavior, and would have
changed every byte-sensitive file. Remove an ordinary file from that list when
you next rewrite it; leave the byte-sensitive ones.

The syntax baseline is Python 3.14. Lint selects pyflakes, the pycodestyle error
families and bugbear.

## Architecture

`uv run lint-imports` checks eight contracts, and the suite checks them again:

| Contract | What it holds |
| --- | --- |
| `layers` | Command line, then orchestration (`workflow`, `verify`, `support`, `harnesses.claude_support`), then operations, then `records`, then `config` and `lifecycle`, then the capture path, then foundations that import nothing else in the package. |
| `hooks` | Hook entry points, and everything they import, load no third-party package. |
| `archive` | The archive builder uses the standard library only. |
| `scripts` | Sandbox probes and development launcher scripts are self-contained. |
| `lifecycle` | Lifecycle facts are evaluated without filesystem or process access. |
| `frozen` | Only `capture/collector.py` loads the frozen collector. |
| `presentation` | Only the command line imports `berman.presentation`. |
| `confined` | `jsonschema` is imported only by `berman.schemas`, `rich` only by `berman.presentation.terminal`, and `packaging` only by `berman.environment`. |

Every contract has at least one case in `tests/unit/test_architecture.py` that
appends an import to a copy of the package and expects that contract to break.
Add a case when you add a contract. A new module needs a place in `layers`; a
test fails until it has one.

Some properties have no form as a contract and stay as tests: that every module
a hook imports is fingerprinted, that hooks and copied scripts import nothing
outside the standard library, that every third-party import is a declared
dependency, that the frozen collector is loaded once, and that the frozen bytes
and serialization helpers are unchanged.

## Record schemas

`src/berman/schemas/` holds one JSON Schema (Draft 2020-12) per record family,
named for it: `berman.recipe/2` is `recipe-2.json`. They are packaged with the
wheel, covered by the software identity and checked by
`berman verify --software`.

| Schema | Record | Validated |
| --- | --- | --- |
| `berman.workspace/1` | `berman.json` | when a workspace loads |
| `berman.connection/1` | `connection.json` | when a workspace loads |
| `berman.research-plan/1` | `plan.json` | when a workspace loads |
| `berman.run-catalogue/1` | the run catalogue | when a workspace loads |
| `berman.recipe/2` | `recipe.json` | when a recipe is selected |
| `berman.operation/1` | `operations/RUN/operation.json` | on every read and before every write |
| `berman.new-request/1` | a combined request | on every read and before every write |
| `berman.software/1` | `berman version`, `software.json` | by tests |
| `berman.verification/1` | `berman --json verify` and kept results | by tests |

The rule for what a schema refuses:

- Only structure Berman's code would crash on or silently misread. A field the
  code reads as a path, iterates or indexes must have the JSON type that reading
  needs, and a record must have the fields that loading it reads.
- A field the code converts is accepted in every form the conversion takes: a
  check's timeout may be text, a plan's step identities may be numbers.
- A field Berman only stores or prints is not constrained, null is read as
  absent, and every object stays open to fields the schema does not name.
- A field only a later command reads is not required at load. A connection
  without `documents`, `commission` and `revision` loads and serves
  development; preparing fieldwork for it fails then, as before.

So a workspace or record that version 0.4.1 loaded still loads, with one class
of exception: a list given as text, which was read character by character
without complaint. `documents`, `readers`, `checks` and a check's `argv` in a
connection, and `connections` in a recipe, must be lists. A recipe's
`connections` given as text used to match any connection whose name occurred
inside that text.

The division of labor:

- Phases, purposes, adapter names, run and session identities, agreement
  between records and filesystem safety stay in the Python that decides them.
- For operation records, combined requests and recipes, the Python rules run
  first and in the same words, and the schema then refuses structure nothing
  looked at before. For `berman.json`, connections, plans and the catalogue, the
  schema runs once the file's family is recognized, so a wrong type that used to
  surface as a bare key name, a traceback or a later rule's consequence is
  reported by the schema with its place.
- Validation returns the value it was given. It fills no default and repairs
  nothing, so reading a preserved record cannot change it.
- A record that fails is invalid data. That stays distinct from a record that
  is missing, from an incomplete operation, whose record is valid at every
  step, and from a fact nobody recorded.
- Hooks and the archive builder read their records directly and do not validate.
- If `jsonschema` does not import, every record is refused with that reason;
  nothing is read unchecked.

A released schema keeps accepting what it accepted. Adding an optional field is
compatible within a version. A change that would refuse a record the released
schema took, or give a field another meaning, is a new name and a new file
beside the old one. `tests/fixtures/records/` holds valid and invalid fixtures
for every schema, with the exact problems each invalid one must report; the
valid operation records include the shapes earlier workflows wrote.

## Property tests

`tests/properties/` runs with the suite. By default Hypothesis is derandomized
and keeps no example database, so every run tests the same examples and a
failure reproduces by running the test again. To look for new failures:

```sh
BERMAN_HYPOTHESIS_PROFILE=explore uv run python -m unittest discover -s tests/properties -t .
```

That profile randomizes, multiplies the example counts by ten and keeps a local
`.hypothesis` database. When it finds something, fix the defect and pin the case
as a plain test beside the code it concerns, as the two regressions in
`tests/integration/test_records.py` are.

`test_workflow_sequences.py` is a state machine over the real workflow in a
disposable workspace: preparation, sessions, callbacks, finalization, endings,
cancellation, lock contention and an interruption at any durable write, followed
by a retry. Its docstring lists what it holds and what it does not model.

## Presentation

`berman.presentation` turns finished results into text and computes nothing.
`plain` is the established text, and scripts depend on its bytes;
`tests/fixtures/presentation/` pins it against what version 0.4.1 printed.
`terminal` is a Rich rendering of the same results, used when standard output is
an interactive terminal. Redirected output is always plain,
`BERMAN_OUTPUT=plain` keeps the plain text on a terminal, and if `rich` does not
import the plain text is printed. `--json` output never passes through either.

A change to `plain` is a change to Berman's output and needs the deliberateness
of a change to a command's options. A change to `terminal` must keep three
rules its tests hold: every word of the plain text is shown, every distinction
survives without color, and recorded text is shown literally.

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

Version metadata lives in `pyproject.toml` and is read from the installed
distribution. A version ending in `.dev0` marks a tree between releases.

## Code map

| Location | Responsibility |
| --- | --- |
| `src/berman/cli.py`, `workspace.py` | Command interface, workspace discovery and initialization, software identity. |
| `presentation/` | Plain and terminal renderings of finished results. |
| `workflow.py`, `records.py`, `lifecycle.py` | Engagement decisions, journals and resumable operations. |
| `schemas/` | Packaged record schemas and their validation. |
| `recipes.py`, `preserve.py`, `recovery.py` | Preparation, retained records and explicit low-level recovery. |
| `capture/` | Exclusive session routing, collector adapters and locking. |
| `harnesses/` | Access policies, native checks, Claude settings and optional development launchers. |
| `archive/` | Selection, reading aids and deterministic packaging, including historical readers. |
| `installation.py`, `verify.py` | Installed resources, diagnostics and qualification receipts. |
| `tests/` | Unit, integration, compatibility and property tests with synthetic fixtures and shared helpers. |

Workspace paths come from the selected Store. Installed resources come from the
package. Recorded launch commands and hooks name the installed interpreter and
use isolated module execution. The operator's cheatsheet uses `berman` on PATH
with an explicit workspace root. Probes are copied into disposable sandboxes.

## Compatibility and qualification

`src/berman/_legacy/capture_session.py` is frozen at SHA-256 `f87241a6b9506590fffa4e2a964f37bb7f7c719e508f4024661b45c57f983a99`.
Never edit it or install it directly. The exclusive router owns session locking
and chooses the collector. Historical markers and schema names remain readable.

New operation records carry the package version, runtime source digests and
the environment they ran in. A workspace's software.json records its initial
selection. Adopting another installation is explicit maintenance: changed
capture bytes or hook commands require closed captures and renewed
installation-specific qualification, including actual CLI trust review where
applicable. Never reuse another study's receipts.

### Version 0.4.2

Version 0.4.2 requires Python 3.14 and adds two runtime dependencies,
`jsonschema` and `rich`. Earlier versions declared Python 3.11 or later; a
launcher defect made that untrue, and it is no longer claimed.

What a command accepts and returns is as in 0.4.1. A workspace that worked
still works, and redirected output, `--json` and exit codes are unchanged. The
differences are these:

- **Additions to output.** `berman version`, `software.json` and each new
  operation record's `software` gain `environment`: the interpreter version and
  the installed version of every distribution the declared dependencies require.
  `files_sha256` also covers the packaged schema files. `berman verify --software`
  gains the checks `dependencies` and `schemas`, two more lines in its text and
  two more entries in its JSON.
- **Clearer refusals of what already failed.** A missing or mistyped field that
  loading reads is reported with its file and place, where 0.4.1 printed a bare
  key name or a traceback.
- **New refusals, of what was misread.** A list given as text in a connection
  or recipe, and an operation record whose structure no workflow wrote, are
  refused. [Record schemas](#record-schemas) lists them.
- **Two recovery fixes.** A reservation whose first write failed no longer
  leaves a folder that blocks later preparations. And `berman finish --run RUN`,
  interrupted after its packet had migrated, can be retried while another
  engagement is open.
- **A terminal rendering.** On an interactive terminal, `status`, `verify`,
  `cheatsheet` and the result of `new` are laid out with Rich: check statuses
  and lifecycle facts are told apart by word and mark, `status` shows the
  evidence for each fact or why it is unknown, and `cheatsheet` groups commands
  by whether they can be typed now. `BERMAN_OUTPUT=plain` keeps the 0.4.1 text.

The fingerprinted capture implementation, the archive builder and the frozen
collector are byte for byte what 0.4.1 shipped. A basis recorded for those bytes
still names them.

### What adopting 0.4.2 in a study needs

The independent integration review prompted corrections on the development
branch after the original 0.4.2 revision. These changes do not adopt that branch
in a study:

- Software verification now checks applicable transitive requirements, version
  constraints and `Requires-Python`, using the additional `packaging` dependency.
  Markers are evaluated for this interpreter and requested extras. Missing
  package files and the APIs Berman imports are checked explicitly; surviving
  distribution metadata alone cannot establish a pass. This is a compatibility
  and import check, not a hash attestation of dependency bytes or their origin.
- Import-time exceptions from a damaged `jsonschema` installation refuse records
  with an explanation. Import-time exceptions from Rich fall back to plain text,
  preserving the result and exit code already computed.
- Boolean plan-step identities and false catalogue values (`0`, `[]`, `{}`)
  accepted by 0.4.1 are accepted again, without rewriting historical records.
- Narrow terminals show lifecycle facts vertically and fold long evidence
  instead of truncating it. Commands containing control characters use shell
  escapes that preserve the argument bytes; ordinary commands, redirected text
  and JSON retain their previous forms. The renderer tests specify both console
  dimensions so their outcome does not depend on the runner's `TERM`.

The Import Linter contracts cover static imports, including deferred imports.
They do not detect arbitrary dynamic loading or filesystem calls through an
otherwise allowed module; code review and runtime import checks remain necessary.
The installed-wheel smoke test checks hooks in an isolated interpreter. Neither
those checks nor dependency compatibility establish native qualification.

None of the development checks qualifies a native installation. A study that
adopts this version still decides, and does, the following under its own
procedure:

- **An environment with dependencies.** Installing one wheel with `--no-index`
  no longer works. Keep the dependency wheels with the adoption record so the
  environment can be rebuilt from recorded bytes:
  `python -m pip download WHEEL -d wheelhouse`, then
  `python -m pip install --no-index --find-links wheelhouse berman==VERSION`.
  One dependency, `rpds-py`, is a compiled wheel for one platform and Python.
- **A recorded identity that includes the environment.** `berman version`
  differs from a `software.json` written by 0.4.1 in its version, its digests
  and the new `environment`. A later change to an installed dependency changes
  `berman version` and no source digest.
- **A read of existing records before any write.** `berman status` and a plain
  `berman verify` only read. Run them with the new version first: a record this
  version refuses is reported there, with its place.
- **Hook trust, native capture and native access.** Unchanged capture bytes can
  leave a recorded basis mechanically valid. Whether that is sufficient, and
  whether to renew it, is the study's decision. The hook command changes if the
  runtime interpreter's path does.

### Earlier versions

The 0.2 package removes the checkout launcher and symlink installer. Install with
pip instead. Reading editions use `berman --root PATH archive`; this command and
archive verification use the active package interpreter. The workspace's
`runtime_python` runs native harness callbacks and must have the chosen package
installed; `python` runs sandbox probes and project checks. `init` records both
explicitly, and disposable access checks retain the distinction.
`berman verify --skip archive` remains available;
the former test-suite skip options are removed.

Version 0.4 removes the `legacy-codex` adapter: its routing, its packet layout
with `narrative.md` and `scratch/` placeholders, its registry handling, the
`berman.configuration/1` input format and the low-level `prepare` command that
produced its packets. The frozen collector file stays, because both current
collectors are built on it; `tests/support/frozen_packets.py` builds its own
packet layout so that its capture, finalization, supplement and migration
behavior is still exercised directly. The router, runtime and collector bytes
changed, so an installation that adopts 0.4 renews hook trust and native
capture.

Version 0.3 added what adopting the package in an existing study needed. An
installation maintains one adapter per harness. berman.json
gains `runtime_python` (the interpreter generated hook commands name),
`qualified_adapters` (adapters verification requires whether or not a connection
selects them) and `edition` (the reading edition the workspace maintains).
`berman capture definition` prints or writes the workspace hook template.
Operation records name `runtime_python` when it differs from `python`, and a
later change to it is refused like any other installation change.

## Preserved material

Never edit preserved packets, inventories or recordings in place or run ordinary
Git commands inside their nested repositories. Archive verification checks
recorded bytes, modes and modification times where recorded. Keep maintenance
evidence with the installation it checked.

`berman --root PATH verify --native-access` adds a native access check. Use
`--record experiments/NAME` to retain receipts in that workspace. Every report
distinguishes failures, checks not run, facts not established and work never
performed. Rebuilding an archive, native support trials and actual CLI trust
review are separate actions. See [current limits](limitations.md).
