# Working on Berman core

This repository owns reusable software. Read README.md, docs/usage.md and
docs/development.md. Keep studies, local settings, native qualification and
analysis outside this repository. Tests use synthetic inputs in tests/fixtures/.

- Python 3.14, managed by uv. `uv sync`, then all five before reporting a
  change: `uv run ruff check`, `uv run ruff format --check`,
  `uv run lint-imports`, `uv run python -m unittest discover -s tests -t .` and
  `uv run berman verify --software`. Report their scope and omissions.
- Use this checkout's environment explicitly: `uv run berman`, `.venv/bin/berman`
  or `python -m berman` with the environment active. A workstation that runs a
  study may have another program named `berman` on PATH, such as a pinned older
  installation. If `berman version` fails or names other source, it is not
  this package. Never modify a study's pinned runtime from here.
- Some files are identity as well as source: the frozen collector, everything
  an adapter's `implementation` list fingerprints, the archive builder, and the
  execution probes. Do not reformat them or fix style in them; a
  byte change there costs renewed qualification or a rebuilt edition
  (docs/development.md#byte-sensitive-files).
- `jsonschema` is imported only by `berman.schemas` and `rich` only by
  `berman.presentation.terminal`; `packaging` only by `berman.environment`.
  Hooks, probes and the archive
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
  `berman.presentation.plain` is what a pipe, a file and `BERMAN_OUTPUT=plain`
  receive, pinned byte for byte by tests/fixtures/presentation; an interactive
  terminal gets the Rich rendering of the same result. `--json`, exit codes and
  redirected text must not depend on whether a terminal is attached.
- `berman verify` includes a read-only `recordings` audit. Missing historical
  authority remains unknown coverage; an integrity pass makes no completion assertion. Interrupted writer-owned
  material goes into `.state/recovery` before inventory; nothing already
  inventoried is cleaned retrospectively. Notes and selected project documents
  can have temporary-looking filenames and must remain intact.
- Property tests are derandomized by default. Pin a failure found with
  `BERMAN_HYPOTHESIS_PROFILE=explore` as a plain regression test.
- Workspace selection is silent. Run inside a study's repository, or with
  `--root` naming it, this package operates on that study. `berman status` and a
  plain `berman verify` only read. Do not run commands that write there unless adopting
  this version in that study is the task; that needs the study's own procedure.
- Never edit src/berman/_legacy/capture_session.py. Its bytes are frozen for historical compatibility.
- Capture hooks enter through the exclusive router, never direct collectors.
- A software change does not qualify a native installation, and neither does a
  passing suite: simulated callbacks and synthetic records establish no hook
  trust, native capture or native access. Real hook changes need closed
  captures, an actual CLI trust review where applicable and native evidence.
- Respect preserved records in any selected workspace; do not run Git inside runs/.
- Use explicit Git repository targets. Keep independent study repositories out of
  commits, submodules and release payloads.
- Commit at useful checkpoints. Publish only when requested.

`CLAUDE.md` is a symlink to this file.
