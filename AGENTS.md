# Working on Berman core

This repository owns reusable software. Read README.md, docs/usage.md and
docs/development.md. Keep studies, local settings, native qualification and
analysis outside this repository. Tests use synthetic inputs in tests/fixtures/.

- Fieldwork has one lifecycle. The controller and executor (`controller.py`,
  `custody.py`, `executor/`, `connector.py`, `protocol.py`) are the only code
  that commissions, prepares, launches, captures, ends, collects, preserves,
  or supplements fieldwork. Add no second path.
- Python 3.14, managed by uv. `uv sync`, then all five before reporting a
  change: `uv run ruff check`, `uv run ruff format --check`,
  `uv run lint-imports`, `uv run python -m unittest discover -s tests -t .` and
  `uv run berman verify`. Report their scope and omissions.
- Use this checkout's environment explicitly: `uv run berman`, `.venv/bin/berman`
  or `python -m berman` with the environment active. A workstation that runs a
  study may have another program named `berman` on PATH, such as a pinned older
  installation. If `berman version` fails or names other source, it is not
  this package. Never modify a study's pinned runtime from here.
- Each edition pins the reader source that produced it. Keep the archive builder
  out of formatting-only changes. The executor records every module's digest in
  each preparation and launch receipt
  (docs/development.md#byte-sensitive-files).
- `jsonschema` is imported only by `berman.schemas` and `packaging` only by `berman.environment`.
  The executor's hook routing and the archive
  builder import the standard library only. The Import Linter contracts in
  pyproject.toml hold this; add a violation case to
  tests/unit/test_architecture.py with any new contract, and give any new
  module a layer.
- Versions are `0.YY.N`: the release year and a serial within it. They name
  released artifacts and say nothing about compatibility or severity. Never
  choose or increment a version because of what a change does; increment the
  serial only when explicitly asked to prepare a release, and never move or
  reuse a released tag (docs/development.md#release-numbering).
- GitHub is the backup of released artifacts: every release from 0.4.4 on stays
  there with its assets, and none is deleted. Locally, `dist/` holds only the
  current version's build (docs/development.md#keeping-release-artifacts).
- Any release may deliberately change behavior and record requirements. Prefer
  the simplest robust implementation over compatibility machinery, and add no
  pre-release stage, migration guarantee or compatibility accommodation unless
  asked. Document changes and brief the researcher when adoption needs
  repair, a user response or changes to analysis. Preserve original bytes and
  the meaning of historical evidence; never invent a missing ending or silently
  rewrite old records. Validation never changes the value it checks.
- CLI behavior may change as part of an authorized corrective task. Describe
  the change and test its consequences rather than retaining a known defect.
- Property tests are derandomized by default. Pin a failure found with
  `BERMAN_HYPOTHESIS_PROFILE=explore` as a plain regression test.
- Capture hooks enter through the executor's exclusive router, never a direct collector.
- A software change does not qualify a native installation, and neither does a
  passing suite: simulated callbacks and synthetic records establish no hook
  trust, native capture or native access. Real hook changes need closed
  captures, an actual CLI trust review where applicable and native evidence.
- Read guest recordings from verified packages. Keep original bytes and custody receipts intact.
- Use explicit Git repository targets. Keep independent study repositories out of
  commits, submodules and release payloads.
- Commit at useful checkpoints. Publish only when requested.

`CLAUDE.md` is a symlink to this file.
