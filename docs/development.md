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
| JSON Schema (`jsonschema`) | runtime | Packaged structural contracts; workspace records checked on access, standalone evidence contracts also checked by dependency-free readers. | `src/berman/schemas/` |
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
| Each adapter's `implementation` list in `capture/adapters.py`: the hook entry points and what they import, `installation.py`, `files.py`, `harnesses/claude.py`; and the probe and assessment modules fingerprinted by Claude qualification | Hook trust and native capture. | Renewed qualification in every adopting study. |
| `src/berman/archive/*.py` | A reading edition's reader identity. | New editions name new reader bytes; published editions verify independently. |
| `harnesses/codex.py`, `harnesses/probe.py` | Native checkout and policy receipts. | Rechecked when their fingerprint changes. |

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
| `scripts` | The copied execution probe uses the standard library. |
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
| `berman.research-plan/1` | `plan.json` | when a selected plan is inspected or supplies a fresh assignment |
| `berman.run-catalogue/1` | the run catalogue | when a workspace loads |
| `berman.recipe/2` | `recipe.json` | when a recipe is selected |
| `berman.operation/1` | `operations/RUN/operation.json` | on every read and before every write |
| `berman.new-request/2` | a combined request naming an already reserved target | on every read and before every write |
| `berman.new-request/1` | a historical combined request | completed records read unchanged; pending records require settlement with the original installation |
| `berman.software/1` | `berman version`, `software.json` | by tests |
| `berman.verification/1` | `berman --json verify` and kept results | by tests |
| `berman.recovered-write/1` | retained interrupted-write receipts | before writing and when reading recovery receipts |

The rule for what a schema refuses:

- Only structure Berman's code would crash on or silently misread. A field the
  code reads as a path, iterates or indexes must have the JSON type that reading
  needs, and a record must have the fields that loading it reads.
- A field the code converts is accepted in every form the conversion takes: a
  check's timeout may be text, a plan's step identities may be numbers.
- A field Berman only stores or prints is not constrained, null is read as
  absent, and every object stays open to fields the schema does not name.
- A plan's `commitment` and `relationships` are study annotations, not
  operational preconditions; their shapes do not constrain assignment.
- A field only a later command reads is not required at load. A connection
  without `documents` loads and serves development; preparing fieldwork for it
  fails then, as before.

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
- Hooks and the archive builder use explicit standard-library validators for committed captures and portable evidence. They never import jsonschema.
- If `jsonschema` does not import, every record is refused with that reason;
  nothing is read unchecked.

Any release may deliberately tighten validation or change CLI behavior
([release numbering](#release-numbering)). Such changes need documented
adoption consequences and regression tests. A historical fact must never acquire a different meaning silently, and
validation never rewrites a record. Give a materially different record meaning
a distinct schema name. `tests/fixtures/records/` holds valid and invalid fixtures
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
distribution. The tree between releases keeps the number of the last release;
`berman version` tells two trees apart by their source digests.

## Release numbering

This project uses experimental calendar-serial versions: `0.YY.N`, where `YY` is the two-digit release year and `N` is the sequential release number within that year, beginning at 1.

Versions identify released artifacts. They do not encode change severity, compatibility, maturity, or development progress.

- Any `0.*` release may make arbitrary breaking changes, including within the same calendar year.
- Never select a version number based on whether a change is breaking, additive, or corrective.
- Never increment the version merely because source code, tests, or an interface changed.
- Increment the serial only when explicitly preparing a new release artifact.
- Do not introduce pre-release stages, migration guarantees, or compatibility accommodations without explicit authorization.
- Released artifacts and Git tags are immutable. A replacement requires a new release number.
- At the first release of a new calendar year, advance `YY` and reset `N` to 1.

A release is a deliberate artifact boundary, not a declaration that the software has become more stable. Do not infer compatibility between releases.

Version `1.0.0` is reserved for an explicit future decision to establish a stable public contract.

Releases through 0.4.4 were numbered before this scheme, and their numbers
carry no meaning under it. The first release numbered this way is 0.26.1.

## Keeping release artifacts

Every release from 0.4.4 on stays on GitHub with its wheel, source distribution
and `SHA256SUMS`. That is the backup of released artifacts: do not delete a
release or its tag. Releases before 0.4.4 were removed on 14 November 2026;
their tags remain.

Keep less locally. `dist/` is ignored by Git and holds only the artifacts of the
version in `pyproject.toml`: clear it before `uv build`. The test suite fails
while it holds another version's files, so a stale build cannot be mistaken for
the one being released. A study keeps the dependency wheelhouse of the release
it runs and of the one it ran before, and fetches anything older from GitHub by
its tag.

## Code map

| Location | Responsibility |
| --- | --- |
| `src/berman/cli.py`, `workspace.py` | Command interface, workspace discovery and initialization, software identity. |
| `presentation/` | Plain and terminal renderings of finished results. |
| `workflow.py`, `records.py`, `lifecycle.py` | Engagement decisions, journals and resumable operations. |
| `schemas/` | Packaged record schemas and their validation. |
| `recipes.py`, `preserve.py`, `recovery.py` | Preparation, retained records and explicit low-level recovery. |
| `capture/` | Exclusive session routing, collector adapters and locking. |
| `harnesses/` | Native checkout checks, fieldwork launch arguments and Claude capture settings. |
| `archive/` | Selection, reading aids and deterministic packaging, including historical readers. |
| `installation.py`, `verify.py` | Installed resources, diagnostics and qualification receipts. |
| `tests/` | Unit, integration, compatibility and property tests with synthetic fixtures and shared helpers. |

Workspace paths come from the selected Store. Installed resources come from the
package. Recorded launch commands and hooks name the installed interpreter and
use isolated module execution. The operator's cheatsheet uses `berman` on PATH
with an explicit workspace root. The probe runs in a disposable project fixture.

## Compatibility and qualification

A release is a useful adoption boundary, not a promise that any earlier
behavior survives ([release numbering](#release-numbering)). Prefer a simple,
robust corrective change over compatibility machinery. Describe changed
refusals, output and verification scope, and any response an adopting study
needs. Preserved evidence is never rewritten to make a new check pass.

### Unreleased: optional study plans

`new --recipe NAME` reserves a direct assignment. Bare `new` uses an optional
plan for a fresh assignment or retries retained work. New operations record
assignment origin explicitly; missing historical origins retain their meaning.
Direct encounters do not advance a plan, and only that plan's own recorded
assignments participate in its sequence checks. Plan resolution is deferred to
selection or inspection, so a missing or invalid selected plan remains a visible
problem without blocking otherwise valid direct work or retained retries.

The cross-project commitment gate is removed. `commitment` and `relationships`
remain readable study annotations. This deliberately changes operational
restrictions; studies that use those promises must manage them in their own
practice. Connecting a project alone still assigns no fieldworker.

New combined requests use `berman.new-request/2`: the exact target assignment
is reserved before its run is named in the request and before the previous
ending is declared. Interruption tests cover reservation, request publication,
ending, preservation, preparation and cancellation. Cancelling an unused target
cancels further preparation by a pending request without undoing an ending.
Completed historical `/1` requests stay readable unchanged. Pending `/1`
requests must be settled with their original installation before adoption;
the new software reports this recovery requirement rather than guessing a
target. Do not rewrite historical requests to satisfy the new contract.

Native access verification now checks every configured Codex connection,
including plan-free connections, with a reserved diagnostic recipe in disposable
roots. It retains actual checkout/document/access checks without copying a study
plan or choosing research instructions. Reserved-name collisions are refused.
Receipts identify diagnostic instructions. This launches neither a fieldworker
nor a model and establishes no hook trust or native capture; a later adopting
study renews only arrangements changed by the new bytes under its own procedure.

Plain and terminal output now identify assignment recipe and origin, show
selected-plan errors separately, and label direct-assignment commands containing
`RECIPE` as templates requiring a choice. These are deliberate changes to the
redirected-text fixtures; machine output carries the corresponding result fields.
No release number, package, adoption or native qualification follows from this
checkout change.

### Version 0.26.2: keeping release artifacts

States where released artifacts are kept and removes this guide's notes on
releases before 0.4.4. It adds two checks to the test suite, on the version's
form and on what `dist/` holds. No source file differs from 0.26.1, so nothing
in how Berman behaves differs from 0.4.4.

### Version 0.26.1: release numbering

The first release under [calendar-serial numbering](#release-numbering). It
changes the version number and this guidance and nothing in how Berman behaves:
every source file is byte for byte what 0.4.4 shipped, except one sentence of
a docstring in `schemas/__init__.py`. A study on 0.4.4 has no reason in this
release to adopt it, and a basis recorded for 0.4.4's fingerprinted bytes still
names them.

### Version 0.4.4: one opening message

The opening message a fieldworker receives is now written entirely by Berman
and is the same for every connection and recipe; the
[fieldwork guide](usage.md#what-a-fieldworker-receives) shows it. It names the
working directory, the connection's name and the commit observed at
preparation, the run directory and its access, and the instructions. The access
sentence used to be supplied by individual recipes.

This is a breaking change. Three researcher inputs are retired: a connection's
`commission` file and `revision` text, and a recipe's `research_commission`
file. None of them reaches a fieldworker any more.

- `berman new` refuses a connection that still names `commission` or `revision`
  and a recipe that still names `research_commission` or `commission`, saying
  which file to change. It does not ignore them: text a researcher wrote for a
  fieldworker must not silently go undelivered. `status`, `verify` and
  development still load such a connection.
- New operation records carry no `commission`, their retained connection no
  `commission` or `revision_template`, and no `preparation/COMMISSION.md` is
  written. A packet's run.json records `manager.project` and `manager.head` in
  place of `manager.revision`, and no `assignment_sha256`. Earlier records and
  packets keep what they have and stay readable.
- A packet prepared by an earlier version keeps its opening message and
  launches unchanged. A reservation made by an earlier version whose packet was
  never created is refused: cancel it and run `berman new` again.
- Support trials use the same message, without the commit sentence.
- `berman init` writes a connection with no commission.

The minimal study example now holds the baseline recipe: plain accounts of work
in progress. The baseline does not tell the fieldworker that the session is
being recorded.

The fingerprinted capture implementation, the archive builder, the probes, the
launcher scripts and the frozen collector are byte for byte what 0.4.3 shipped.

A study that adopts this version removes `commission` and `revision` from each
connection.json and writes its recipes without a research commission, moving
any text a fieldworker still needs into INSTRUCTIONS.md. Recipes kept only as a
record of earlier runs can stay as they are; they are refused only if a plan
selects them.

### Adopting a release in a study

Publishing a release adopts it nowhere, and none of the development checks
qualifies a native installation. A study that adopts a release decides, and
does, the following under its own procedure:

- **A read of existing records before any write.** `berman status` and a plain
  `berman verify` only read. Run them with the new release first: a record it
  refuses is reported there, with its place.
- **Closed captures.** An active capture is closed with the runtime that opened
  it before another release replaces that runtime.
- **An environment with dependencies.** Keep the dependency wheels with the
  adoption record so the environment can be rebuilt from recorded bytes:
  `python -m pip download WHEEL -d wheelhouse`, then
  `python -m pip install --no-index --find-links wheelhouse berman==VERSION`.
  One dependency, `rpds-py`, is a compiled wheel for one platform and Python.
- **A recorded identity.** `berman version` and a workspace's software.json name
  the version, the digest of every source and schema file, and the interpreter
  and dependency versions. A later change to an installed dependency changes
  `berman version` and no source digest.
- **Hook trust, native capture and native access.** Changed fingerprinted bytes
  or hook commands need renewed qualification in that installation, including
  an actual CLI trust review where applicable. Unchanged bytes can leave a
  recorded basis mechanically valid; whether that is sufficient is the study's
  decision. Never reuse another study's receipts.

`src/berman/_legacy/capture_session.py` is frozen at SHA-256 `f87241a6b9506590fffa4e2a964f37bb7f7c719e508f4024661b45c57f983a99`.
Never edit it or install it directly. The exclusive router owns session locking
and chooses the collector. Historical markers and schema names remain readable.

Notes on releases before 0.4.4 were removed from this guide; Git history keeps
them.

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
