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

Tests create disposable repositories, synthetic studies, harness doubles and
simulated callbacks. They do not spend model tokens or establish native capture.
The installed `berman verify` checks package integrity and, when one is selected,
a co-located workspace made by an earlier release. It never runs the developer
tests or requires this checkout.

## What each tool is for

| Tool | Kind | What it enforces | Where it is configured |
| --- | --- | --- | --- |
| Ruff | development | Lint on everything but the frozen collector; formatting of files written since it was adopted. | `[tool.ruff]` in `pyproject.toml` |
| Import Linter | development | Seven architectural contracts over the import graph. | `[tool.importlinter]` in `pyproject.toml` |
| JSON Schema (`jsonschema`) | runtime | Packaged structural contracts; study inputs and co-located records checked when read, standalone evidence contracts also checked by dependency-free readers. | `src/berman/schemas/` |
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
| `src/berman/archive/*.py` | A reading edition's reader identity. | New editions name new reader bytes; published editions verify independently. |

Ruff never formats these files, and a test keeps the archive modules on the
formatter's exemption list. Lint findings in them that are only style are ignored
per file and per rule in `pyproject.toml`; a new kind of finding still surfaces
and gets a decision. Schema validation and presentation are attached elsewhere,
so none of these files imports `jsonschema` or `rich`.

Every preparation and launch receipt the executor writes records the digest of
each installed module. No file is singled out by that: any source change shows
in the next receipt, and a qualification describes the launch that produced it.

Three helpers keep the results earlier receipts recorded, wherever they now
live: `berman.checks.project_checks`, `berman.capture.hooks.capture_entry` and
`berman.capture.claude.render`, with the frozen collector's `render`.
`tests/unit/test_capture_readers.py` pins them.

## Formatting and lint

Ruff's formatter governs files written since it was adopted. Files written
before are listed under `[tool.ruff.format] exclude`: formatting them all would
have rewritten most of the code for no change in behavior, and would have
changed every byte-sensitive file. Remove an ordinary file from that list when
you next rewrite it; leave the byte-sensitive ones.

The syntax baseline is Python 3.14. Lint selects pyflakes, the pycodestyle error
families and bugbear.

## Architecture

`uv run lint-imports` checks seven contracts, and the suite checks them again:

| Contract | What it holds |
| --- | --- |
| `layers` | Command line, then the connector, then orchestration (`controller`, `executor`, `verify`), then operations (`custody`, `sources`, `recordings`, `recipes`, `workspace`, `checks`), then `protocol` and `records`, then `config` and `lifecycle`, then `capture` and the frozen collector, then evidence, snapshots and the archive, then foundations that import nothing else in the package. |
| `hooks` | The executor's hook routing, and everything it imports, load no third-party package. |
| `archive` | The archive builder uses the standard library only. |
| `lifecycle` | Lifecycle facts are evaluated without filesystem or process access. |
| `frozen` | Only `capture/collector.py` loads the frozen collector. |
| `presentation` | Only the command line imports `berman.presentation`. |
| `confined` | `jsonschema` is imported only by `berman.schemas`, `rich` only by `berman.presentation.terminal`, and `packaging` only by `berman.environment`. |

Every contract has at least one case in `tests/unit/test_architecture.py` that
appends an import to a copy of the package and expects that contract to break.
Add a case when you add a contract. A new module needs a place in `layers`; a
test fails until it has one.

Some properties have no form as a contract and stay as tests: that every module
the hook routing can import is named by the `hooks` contract, that those modules
import nothing outside the standard library, that the hook entry point loads no
third-party package when it starts, that every third-party import is a declared
dependency, that the frozen collector is loaded once, and that the frozen bytes
and serialization helpers are unchanged.

## Record schemas

`src/berman/schemas/` holds one JSON Schema (Draft 2020-12) per record family,
named for it: `berman.recipe/2` is `recipe-2.json`. They are packaged with the
wheel, covered by the software identity and checked by
`berman verify --software`.

| Schema | Record | Validated |
| --- | --- | --- |
| `berman.connection/1` | `connection.json` | when the controller commissions, and when a co-located workspace loads |
| `berman.research-plan/1` | `plan.json` | when a commission selects a plan, and when a co-located workspace's selected plan is described |
| `berman.recipe/2` | `recipe.json` | when a recipe is selected |
| `berman.recording/2` | a recording's `recording.json` | by tests; the dependency-free reader validates every package it opens |
| `berman.capture-current/1` | `session/current.json` | by tests; the dependency-free reader validates every capture it reads |
| `berman.reading-edition/2` | an edition's manifest | by tests; the dependency-free verifier checks every edition it opens |
| `berman.software/1` | `berman version` | by tests |
| `berman.verification/1` | `berman --json verify` and kept results | by tests |
| `berman.workspace/1` | a co-located workspace's `berman.json` | when the workspace loads |
| `berman.run-catalogue/1` | a co-located workspace's run catalogue | when the workspace loads |
| `berman.operation/1` | a co-located workspace's `operations/RUN/operation.json` | on every read |

The last three families were written only by releases up to 0.26.3. This version
reads them and writes none. The combined-request and recovered-write schemas
went with the writer that used them: a pending combined request is now read only
for its recorded state and reported as unsettled.

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
- A field only a later step reads is not required at load. A connection
  without `documents` loads; an assignment made from it selects none.

So a workspace or record that version 0.4.1 loaded still loads, with one class
of exception: a list given as text, which was read character by character
without complaint. `documents`, `readers`, `checks` and a check's `argv` in a
connection, and `connections` in a recipe, must be lists. A recipe's
`connections` given as text used to match any connection whose name occurred
inside that text.

The division of labor:

- Phases, purposes, adapter names, run and session identities, agreement
  between records and filesystem safety stay in the Python that decides them.
- For operation records and recipes, the Python rules run
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
- The executor's hook routing and the archive builder use explicit standard-library validators for committed captures and portable evidence. They never import jsonschema.
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
as a plain test beside the code it concerns.

`test_lifecycle_facts.py` ranges over what a co-located run's records can
establish: a fact is stated with evidence or unknown with a reason, only an
operator declaration is an ending, and a run at rest awaits nothing whatever
history left out. `test_sequence_rules.py` covers the plan step a commission
takes next.

## Co-located fixtures

No product code writes a co-located workspace any more, so the tests of its
reader build one. `tests/support/legacy.py` writes the records release 0.26.3
wrote: a prepared packet and its operation record, a bound session with
committed capture generations, a frozen capture, a declared ending, the
migration inventory and journal, a recording/2 manifest with its package, and
the closeout receipt with its edition. Each builder stops where a study could
have stopped, so the unsettled states are reachable. They are fixtures: they
take no lock, run no harness and are never installed. When a reader's
understanding of a record changes, check the builder against a workspace the
released writer made, not against the reader.

## Presentation

`berman.presentation` turns finished results into text and computes nothing. It
renders two results, `status` and `verify`.
`plain` is the established text, and scripts depend on its bytes;
`tests/fixtures/presentation/` pins it.
`terminal` is a Rich rendering of the same results, used when standard output is
an interactive terminal. Redirected output is always plain,
`BERMAN_OUTPUT=plain` keeps the plain text on a terminal, and if `rich` does not
import the plain text is printed. `--json` output never passes through either.

A change to `plain` is a change to Berman's output and needs the deliberateness
of a change to a command's options. A change to `terminal` must keep three
rules its tests hold: every word of the plain text is shown, every distinction
survives without color, and recorded text is shown literally.

`wheel_smoke.py` (see [building](#building)) commissions and closes a synthetic
guest study through the installed controller's entry point, with a hand-made
collection receipt standing in for the executor, then verifies the recording and
edition from copies.

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
| `src/berman/connector.py`, `protocol.py` | The operator's commands on the host and the version 1 command envelope across SSH. |
| `controller.py`, `custody.py` | The guest's commissions, ledger and endings; portable recordings and editions from accepted evidence. |
| `executor/` | Native preparation, launch, hook routing, staging, closeout and qualification. |
| `capture/`, `_legacy/` | Transcript readers, recognition of Berman's own hook commands, and the frozen collector. |
| `recipes.py`, `checks.py`, `sources.py`, `snapshots.py` | Recipes and document snapshots, project checks, associated sessions, committed capture generations. |
| `evidence.py`, `archive/` | Portable recording verification; selection, reading aids and deterministic packaging, including historical readers. |
| `cli.py`, `workspace.py` | The `berman` command, software identity and selection of a co-located workspace to read. |
| `records.py`, `lifecycle.py`, `config.py`, `recordings.py` | Read-only discovery of a co-located workspace, what its records establish and what is unsettled, and the audit and export of its preserved runs. `config.py` also serves the controller's connections and plans. |
| `verify.py`, `installation.py`, `environment.py` | Verification of the installed software and of a co-located workspace; installed resources. |
| `presentation/` | Plain and terminal renderings of finished results. |
| `schemas/` | Packaged record schemas and their validation. |
| `tests/` | Unit, integration, compatibility and property tests with synthetic fixtures and shared helpers. |

A co-located workspace's paths come from the selected Store, and nothing is
written beneath them except a new edition or a requested verification record.
Installed resources come from the package. Hook commands name the installed
interpreter and use isolated module execution.

## Compatibility and qualification

A release is a useful adoption boundary, not a promise that any earlier
behavior survives ([release numbering](#release-numbering)). Prefer a simple,
robust corrective change over compatibility machinery. Describe changed
refusals, output and verification scope, and any response an adopting study
needs. Preserved evidence is never rewritten to make a new check pass.

### Version 0.26.4 and the guest fieldwork lifecycle

The controller and executor are now the only code that commissions, prepares,
launches, captures, ends, collects, preserves, migrates or supplements
fieldwork. The co-located writer that releases up to 0.26.3 carried is removed.
This is a breaking change, made deliberately: after the research-denial sandbox
left that path, a co-located workspace kept research plans, analysis and
preserved runs where the observed agent could read them, and the `init`
command still created such workspaces.

Removed commands: `init`, `ready`, `new`, `launch`, `finish`, `supplement`,
`cancel`, `dev`, `cheatsheet`, `capture` (with its `readiness`, `install`,
`definition`, `record-trust` and `record-capture`), `support-trial`,
`recording import`, and the `--native-access` option of `verify`. Each of the
eleven removed commands answers with one refusal that names the controller and
executor and exits 2. The modules behind them are gone: the workflow,
preservation, recovery and support-trial modules, the harness launchers and
probe, the capture router, runtime, transactions and adapters, and the writing
halves of the record, lifecycle, recipe and configuration modules. So are the
`hook-trust`, `native-capture` and `native-access` checks of `verify`.

What remains for a co-located workspace is reading:

- `status` reports each connection's selected plan as its records left it, every
  run that is not at rest, and what is unsettled. It takes no lock and never
  consults a native source. Its result drops `next_recipe` at the top level,
  `operations_in_progress`, and each run's `prepared_unstarted`,
  `capture_consistent`, `warnings` and `operation_in_progress`; it adds
  `unsettled`, `pending_requests` and each run's `unsettled` and `opened_with`.
  A connection reports `plan`, `next_step`, `next_recipe`, `plan_note`,
  `plan_error` and `problems`.
- `verify` runs `frozen-collector`, `recorded-hashes`, `workspace`, `settled`,
  `recordings` and `archive`. `settled` is new and required: unsettled work makes
  it not established and the verdict incomplete, with exit status 3.
- `recording export`, `recording verify`, `recording materialize` and `archive`
  are unchanged.

Unsettled work is never closed here. A reserved, prepared or open engagement, a
declared ending whose preservation, migration, packaging or reading publication
never finished, a pending combined request and an open support trial are each
reported with the statement that it has to be closed with the release that
opened it. A run at rest without an ending or a package is reported as it is and
gains nothing by being read. The plain-text fixtures for `status` and `verify`
change accordingly; the `cheatsheet` and preparation fixtures are removed.

Two helpers the executor reuses moved without a change in what they return:
`project_checks` to `berman.checks`, and `capture_entry` to
`berman.capture.hooks`. `berman.capture.claude` keeps `EVENTS` and `render`, and
`berman.capture.collector` keeps the frozen module as `capture`.

A study that still has a co-located workspace:

- keeps its released runtime, and closes with it whatever `status` reports as
  unsettled, before relying on this version for that workspace;
- reads and verifies the workspace with this version whenever it likes: `status`
  and a plain `verify` only read;
- continues in the guest: export each preserved run, import it into custody and
  reserve the run numbers already used, as
  [historical studies](guest-deployment.md#historical-studies) describes.

The [0.26.4 release notes](releases/0.26.4.md) describe the operator's closeout
command. Each installation qualifies its own native executor.

### Version 0.26.3: the last co-located writer

0.26.3 is the last release that prepares, captures and closes fieldwork in a
co-located workspace. [Its release note](releases/0.26.3.md) describes it. A
workspace it or an earlier release operated stays that release's to operate.

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

From 0.4.4 the opening message a fieldworker received was written entirely by
Berman and was the same for every connection and recipe. A connection's
`commission` file and `revision` text and a recipe's `research_commission` file
were retired. Recipes written since carry everything a fieldworker needs in
INSTRUCTIONS.md, and a recipe that still names `research_commission` or
`commission` is refused when it is selected. The
[fieldwork guide](usage.md#what-a-fieldworker-receives) says what reaches a
fieldworker now.

### Adopting a release in a study

Publishing a release adopts it nowhere, and none of the development checks
qualifies a native installation. A study that adopts a release decides, and
does, the following under its own procedure:

- **A read of existing records before any write.** For a co-located workspace,
  `berman status` and a plain `berman verify` only read. Run them with the new
  release first: a record it refuses is reported there, with its place, and so
  is anything unsettled.
- **Closed captures.** An active capture is closed with the runtime that opened
  it before another release replaces that runtime. A co-located engagement is
  closed with the release that opened it; this version cannot close one.
- **An environment with dependencies.** Keep the dependency wheels with the
  adoption record so the environment can be rebuilt from recorded bytes:
  `python -m pip download WHEEL -d wheelhouse`, then
  `python -m pip install --no-index --find-links wheelhouse berman==VERSION`.
  One dependency, `rpds-py`, is a compiled wheel for one platform and Python.
- **A recorded identity.** `berman version` names the version, the digest of
  every source and schema file, and the interpreter and dependency versions. A
  later change to an installed dependency changes `berman version` and no
  source digest.
- **Native qualification.** A changed executor or hook command needs the
  installed Codex router replaced and reviewed in the actual CLI, and a
  designated launch evaluated with `berman remote qualify RUN`, as
  [native execution](executor.md#qualification-evidence) describes. Never reuse
  another study's receipts.

`src/berman/_legacy/capture_session.py` is frozen at SHA-256 `f87241a6b9506590fffa4e2a964f37bb7f7c719e508f4024661b45c57f983a99`.
Never edit it or install it directly. The executor's exclusive router owns
capture. Historical markers and schema names remain readable.

Notes on releases before 0.4.4 were removed from this guide; Git history keeps
them.

## Preserved material

Never edit preserved packets, inventories or recordings in place or run ordinary
Git commands inside their nested repositories. Archive verification checks
recorded bytes, modes and modification times where recorded. Keep maintenance
evidence with the installation it checked.

`berman --root PATH verify --record experiments/NAME` retains the result and its
receipts in that workspace. Every report distinguishes failures, checks not run,
facts not established and work never performed. Rebuilding an archive and
qualifying a native installation are separate actions. See
[current limits](limitations.md).
