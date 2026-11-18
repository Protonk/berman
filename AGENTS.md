# Working on Berman core

This repository is the reusable software. Studies, connector settings, native
qualification evidence and analysis live outside it, and tests use the
synthetic inputs under `tests/fixtures/`. Start from README.md and the code map
in docs/development.md.

## Who owns what

- The guest controller (`controller.py`, with `custody.py`) owns every
  authoritative research fact: run numbers, commissions, the researcher's
  ending, acceptance, recordings and editions. Nothing on the host decides any
  of them, and a native exit or `SessionEnd` never ends an engagement.
- The executor (`executor/`) owns native staging on macOS: the packet, hooks,
  sittings and the frozen collection.
- The connector (`connector.py`) carries. It turns operator commands into
  `protocol.py` requests and calls the local executor.
- That is the one lifecycle. Add no second path that commissions, prepares,
  launches, captures, ends, collects, preserves or supplements fieldwork.
- The controller and the executor never import each other, and the executor
  imports only the standard library because its hook runs on every turn of a
  worker's session. `pyproject.toml` holds the contracts. A new module needs a
  layer and a new contract needs a violation case in
  `tests/unit/test_architecture.py`.

## Bytes that are evidence

- Every edition records the digests of `archive/builder.py`,
  `archive/package.py` and `archive/streams.py`. Make no formatting-only or
  cosmetic change to them.
- Every installed module's digest goes into each preparation and launch
  receipt. Any change here gives an installation new bytes, so no earlier
  native qualification covers it.
- Recordings, editions and custody receipts are never rewritten. Read them from
  verified packages. Never invent a missing ending or repair an old record in
  place. Validation never changes the value it checks.
- Any release may change behavior and record formats on purpose. Prefer the
  simplest robust implementation over compatibility machinery, and write what
  an adopting study must do in docs/releases/unreleased.md.

## Evidence for a change

- Before reporting a change run all five: `uv run ruff check`,
  `uv run ruff format --check`, `uv run lint-imports`,
  `uv run python -m unittest discover -s tests -t .` and `uv run berman verify`
  (after `uv sync --locked`). Say which ran and which did not.
- Those are synthetic. For the path from the Mac to the guest use the
  mini-CI (docs/ci.md). Keep its assignment and three questions as they are
  unless the integration contract itself is meant to change.
- Only a native session qualifies a harness installation, and hook trust is a
  review in the actual CLI. Claim neither from tests
  (docs/development.md#what-each-check-establishes).
- Use `uv run berman` or `.venv/bin/berman`. A workstation that runs a study
  can have another `berman` on `PATH`. Never modify a study's installed
  runtime from here.

## Releases and Git

- Versions are `0.YY.N` and change only when a release is explicitly
  requested. Never move or reuse a tag or delete a GitHub release
  (docs/development.md#releases).
- Commit at useful checkpoints. Push only when asked.
- This checkout usually sits inside a study's ledger as an ignored directory.
  Name the repository explicitly (`git -C`), and keep study repositories out of
  commits, submodules and release payloads.

`CLAUDE.md` is a symlink to this file.
