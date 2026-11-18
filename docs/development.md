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
in editable form and adds the development tools. `uv python install --no-bin 3.14`
supplies the interpreter without putting a `python3.14` on `PATH`. Commit
`uv.lock` whenever `pyproject.toml`'s dependencies change.

uv is a development tool only. The wheel installs and runs with pip, and a
checkout can be developed with pip alone: `python -m pip install -e . --group dev`
in a Python 3.14 environment installs the same tools, unlocked.

Tests create disposable repositories, synthetic studies, harness doubles and
simulated callbacks. They do not spend model tokens or establish native capture.
The installed `berman verify` checks package resources, dependencies and schemas.

## Byte-sensitive files

Each reading edition records the digests of the modules that built it. Keep the
archive modules out of formatting-only changes. The executor records the installed
module digests in preparation and launch receipts. A native qualification applies
to the installation and launch it observed.

## Architecture

Import Linter checks the package layers, the standard-library boundaries for
native hooks and archive readers, and the confinement of third-party imports.
Every contract in `pyproject.toml` has a deliberate violation in
`tests/unit/test_architecture.py`. Give each new module a layer. Only `schemas`
imports jsonschema, and only `environment` imports packaging.

## Record schemas

The controller validates connection, recipe and plan documents when commissioning.
The JSON Schemas describe their operational fields. Studies can add annotations.
Validation returns the supplied document unchanged. The portable readers check
the recording and edition contracts against the retained members and receipts.

## Property tests

Hypothesis exercises plan sequences, file preservation, deterministic packaging. Tests use deterministic examples by default. Run exploratory
cases with `BERMAN_HYPOTHESIS_PROFILE=explore` and retain a reproducible failure
as an ordinary regression test.

## Guest fixtures

`tests/fixtures/split-0.26.4/` holds a synthetic published recording, edition and
selection pointer. Its parent transcript generation is identified by the retained
pointer. The custody tests reopen the original bytes and replay publication.
Connector and executor integration tests use disposable studies and native harness
doubles for commissioning, capture, transfer, closeout and supplements.

## Software verification

`berman verify` reports the installed source digest, active runtime requirements
and ability to load the commission schemas. It exits 1 if a check fails and
prints the diagnostic next to that check. `berman --json verify` returns
`berman.software-verification/1` with a boolean `passed` and the result of
each check. `berman version` returns the full software identity.

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

The host connector calls the guest controller over the protocol in `protocol.py`.
The executor prepares and launches the worker and stages native evidence.
`config.py` and `recipes.py` handle study inputs. Guest `custody.py` uses
`evidence.py` and `archive/` to seal and verify recordings and editions.
`verify.py` reports software identity and checks the installed package.
`tests/support/wheel_smoke.py` exercises the installed public interface.

## Compatibility and qualification

Keep release notes with the release they describe. The
[0.26.5 release notes](releases/0.26.5.md) describe that release’s evidence formats and adoption consequences.

### Adopting a release in a study

Keep the wheel and its dependency wheels with the adoption record. Close active
captures before replacing the runtime. Record `berman version` on the host and
guest. A changed executor or hook command needs review in the actual CLI and
native evidence from the adopting installation. Use the guest verifier on
previously published guest artifacts. [Native qualification](executor.md#qualification-evidence)

## Preserved material

Keep original recordings and custody receipts as issued. Read editions and
recordings from verified copies. [Current limits](limitations.md)
