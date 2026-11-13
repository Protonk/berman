# Python 3.14 and dependency integration

An outline of the move to a `uv`-managed Python 3.14 environment and the
integration of five dependencies. It records goals, boundaries and decisions,
not procedure. The [decision log](#decision-log) at the end is added to as the
work proceeds; where it disagrees with the outline above it, the log is right.

Commissioned 13 November 2026. Starting point: version 0.4.1, commit `018fec4`.

## Goals

Berman's commitments are mostly conventions today: record shapes are checked
by scattered `.get()` calls, architectural boundaries by a few hand-written
tests, behavior by examples, and operator output by `print`. The integration
makes the important ones explicit and mechanically checked.

| Dependency | Kind | What it is for |
| --- | --- | --- |
| Ruff | development | Error detection and a fixed syntax baseline; formatting of new code. |
| Import Linter | development | Architectural boundaries as executable contracts. |
| JSON Schema (`jsonschema`) | runtime | Structural contracts for persisted and supplied records. |
| Hypothesis | development | Behavioral invariants over generated inputs and operation sequences. |
| Rich | runtime | Operator-facing presentation on a terminal. |

The expected benefit is not shorter code. It is that a malformed record is
reported as invalid data with its location, that a boundary violation fails a
check rather than a review, that retry and interruption guarantees are tested
beyond the cases someone thought of, and that an operator can tell a failed
check from an unestablished fact at a glance.

## Baseline

Taken before any change, from commit `018fec4`:

- Python 3.14.9: 289 tests pass; `berman verify --software` passes. The
  checkout's source digest equals the published 0.4.1 identity
  (`759ff80a…9227`).
- Python 3.11.17: two launcher tests fail (five outcomes). `argparse` before
  CPython 3.12 consumes the launcher's optional prompt too early, so
  `launch.py codex --model M 'a prompt'` is refused. The declared
  `requires-python = ">=3.11"` was therefore untested and already untrue.
- No runtime dependencies. The wheel installs with pip alone.

## Compatibility commitments

**Python 3.14 is the one supported and tested version.** The commission asked
to retain 3.11 support; the researcher withdrew that on 13 November 2026 in
favor of a single test suite on a single version. `requires-python` becomes
`>=3.14` and the syntax baseline is 3.14. No 3.11 or intermediate runs are kept.

Unchanged commitments:

- The wheel installs and runs with pip. `uv` is a development tool only.
- Command names, options, exit codes and `--json` output keep their meaning.
- Persisted formats stay readable. Reading never rewrites a record.
- `_legacy/capture_session.py` keeps its frozen bytes.
- An active study's pinned runtime is not touched by this work.

## Byte-sensitive files

Some files are identity, not just source. Changing their bytes has a cost
outside this repository, so the integration leaves them byte-identical to
0.4.1 unless a defect requires otherwise:

| Files | What their bytes identify |
| --- | --- |
| `_legacy/capture_session.py` | Frozen historical collector. |
| The adapters' `implementation` lists in `capture/adapters.py` (hook entry points and what they import, `installation.py`, `files.py`, `harnesses/claude.py`) and four launcher helpers fingerprinted by source | Hook trust and native capture. A change means renewed qualification in every adopting study. |
| `archive/*.py` | Reading-edition identity (`review/EDITION.json`). A change means a rebuilt edition. |
| `harnesses/codex.py`, `harnesses/probe.py` | Per-run access receipts, rechecked automatically. |

This shapes every phase: style-only fixes are not applied to these files,
validation and presentation are attached elsewhere, and the result is checked
at the end by comparing them with `018fec4`.

## Boundaries

Established by reading the import graph, not assumed. Real today:

- `archive/` imports nothing else in the package.
- `files.py`, `installation.py`, the sandbox probes and the development
  launcher scripts import no other Berman module; probes and launchers run as
  copied files under another interpreter.
- Only `capture/collector.py` imports the frozen collector.
- Hook entry points (`capture/router.py`, `capture/claude_router.py`) reach
  only the fingerprinted capture modules.
- `lifecycle.py` reads no files; discovery passes evidence in.
- `config` and `lifecycle` sit below `records`, which sits below the
  operator-side modules, which sit below `cli`. The operator-side modules
  (`workflow`, `verify`, `support`, the harnesses, `capture/adapters.py`) import
  each other, including cyclically through deferred imports. That cluster is
  recorded as one layer, not untangled.

Intended, and created by this integration:

- Third-party packages never load in a hook, a probe, a launcher script or the
  archive builder.
- Only one module imports `jsonschema`, and only one imports `rich`.
- Presentation receives finished results and computes no state; nothing but
  the command line imports it.

Import Linter states these as contracts. The existing tests in
`tests/unit/test_boundaries.py` stay where they prove what a contract cannot:
that every module a hook imports is fingerprinted, that the frozen collector is
loaded once, and that the frozen bytes and serialization helpers are unchanged.

## Phases

Each phase ends with the full suite passing and a commit.

1. **Environment.** `uv`, `.python-version`, `uv.lock`, dependency groups.
   Nothing else depends on the tools being integrated yet.
2. **Ruff and Import Linter.** Lint findings are fixed where they are defects
   and configured away, with a stated reason, where they are style in a
   byte-sensitive file. Contracts follow the boundaries above. Each contract is
   paired with a test that injects a violation and expects that contract to fail.
3. **JSON Schema.** Depends on phase 2: the contract confining `jsonschema`
   exists before the import does.
4. **Hypothesis.** Depends on phase 3: several properties are about validated
   records, and the stateful test exercises the workflow as phase 3 leaves it.
5. **Rich.** Depends on phase 2 for its confinement contract; otherwise
   independent. Last because it changes the least that matters.
6. **Close.** Guidance, agent instructions, a wheel built and smoke-tested
   outside the checkout, and the byte-sensitive comparison.

## Decisions that need care

**Formatting.** Ruff's formatter would rewrite 51 of 73 files (about 2,700
lines removed and 3,600 to 6,800 added of 15,000, depending on line length),
including every byte-sensitive file. That is widespread churn with a
qualification cost and no behavioral gain. Intended resolution: the formatter
governs new files, existing files are listed as exempt, and lint applies to
everything except the frozen collector.

**Runtime dependencies and software identity.** `berman version` digests
Berman's own files. A dependency resolved at install time is not covered by
that digest, and "the same wheel installs the same bytes" stops being the whole
story. Intended resolution: keep dependencies out of everything qualification
fingerprints (enforced by contract), record their installed versions beside the
source identity as environment facts, and say plainly that a version string
names a release, not bytes. A study that needs byte-level reproducibility keeps
the dependency wheels with its adoption record.

**Capture isolation.** Hooks run on every turn of a fieldworker's session.
They load only the standard library and fingerprinted Berman modules. Schema
validation is not added to the hook path; the hook keeps reading its operation
record directly.

**Historical records.** Schemas describe the shapes the workflow has written,
including older ones, and stay permissive about unknown fields. Vocabulary that
drives lifecycle decisions (phases, purposes) stays in Python, where its
existing messages and tolerances live. Validation never fills defaults,
normalizes or rewrites. Invalid data, a missing record, an incomplete operation
and an unknown fact remain four different reports.

**Command compatibility.** Rich output is for a terminal. Redirected output
and `--json` keep their current bytes, so anything that parses them is
unaffected.

## Verification

- One suite: `python -m unittest discover -s tests -t .` on Python 3.14,
  including the contract, schema, property and presentation tests.
- `ruff check`, `ruff format --check` and `lint-imports` as documented commands.
- `berman verify --software` from the development environment.
- A built wheel installed with pip into a fresh environment and smoke-tested
  from outside the checkout.
- Byte-sensitive files compared with `018fec4`.

Accepted when the commission's criteria hold, read with the Python decision
above: a reproducible 3.14 environment, all five dependencies doing work in a
maintained workflow, contracts that can be shown to fail, tested schemas for the
chosen formats, properties covering interruption, retry and sequences, clearer
operator output with unchanged machine output, and updated guidance.

None of this qualifies a native installation. Passing tests establish
simulated behavior only. What an adopting study must still do is recorded
separately, in the development guide.

## Open questions and risks

- `jsonschema` brings a compiled dependency (`rpds-py`). Installation now
  depends on a binary wheel existing for the platform.
- A schema stricter than a study's real historical records would turn readable
  history into reported damage. Mitigation: check the schemas, read-only,
  against an existing study's records before settling them.
- The stateful test drives real preparation and preservation and is slow per
  step. Its limits trade coverage for suite time.
- An adopting study's restore procedure that installs one wheel with
  `--no-index` will not work for a version with dependencies.

How these stand at the close is recorded in the last entries of the log.

## Decision log

- **13 November 2026.** Python 3.14 only, by the researcher's decision; see
  [compatibility commitments](#compatibility-commitments). The 3.11 launcher
  defect found in the baseline is left as it is: it does not occur on 3.14 and
  the file is otherwise untouched.
- **13 November 2026, Ruff.** Lint runs on everything except the frozen
  collector, with the rule families E4, E7, E9, F and B. Of 151 findings none
  was a behavioral defect. Unused imports, shadowed names and unchained
  re-raises were fixed in ordinary files. The eleven findings in byte-sensitive
  files are ignored per file and per rule, so a new kind of finding there still
  surfaces. The formatter governs new files; the 51 files it would rewrite are
  listed as exempt in `pyproject.toml`, and a test keeps every byte-sensitive
  file on that list.
- **13 November 2026, Import Linter.** Seven contracts. One `layers` contract
  carries the internal structure; the operator side is two layers, orchestration
  (`workflow`, `verify`, `support`, `harnesses.claude_support`, which import
  each other) above operations, because that split is real in the import graph
  and a finer one would be asserted rather than observed. Five `forbidden`
  contracts keep third-party packages out of hooks, the archive builder, copied
  scripts and lifecycle evaluation, and confine them elsewhere; one `protected`
  contract keeps the frozen collector to its single importer. Three assertions
  in `test_boundaries.py` that the contracts now state were removed from it.
  Its fingerprint-coverage, single-load and frozen-byte tests stay. New
  specialized tests cover what a contract cannot name: any third-party import in
  a hook or copied script, undeclared dependencies, and unlayered modules.
- **13 November 2026, JSON Schema.** Nine schemas in `src/berman/schemas/`,
  packaged with the wheel and covered by the software identity. Seven are the
  commissioned families (workspace, connection, recipe, research plan, run
  catalogue, operation record, combined request). Two were added because this
  work changes them: the software identity, which gains an optional
  `environment`, and the verification result, whose checks grow.
  - *A schema states JSON types and required fields only.* Phases, purposes,
    adapter names and identity syntax stay in Python. Otherwise the run identity
    pattern, which comes from the frozen collector, would have a second source.
  - *Existing refusals keep their wording.* Where a Python rule already refused
    a record, it still runs first; the schema then refuses structure nothing
    looked at before, which used to surface later as a bare `KeyError` or a
    traceback. This was a change of approach: schema-first would have been
    tidier and would have reworded errors operators already know.
  - *Every object stays open* to fields the schema does not name. Researcher
    inputs in the adjacent study carry explanatory fields the code never reads.
  - *Hooks and the archive builder do not validate.* They read their records
    directly, as before, and their bytes are unchanged.
  - *Checked against real history, read-only:* all 44 records of the adjacent
    study satisfy the schemas (26 operation records in every phase, 11 retained
    verification results, its inputs and its 0.4.1 software identity). No study
    record is copied here; the fixtures are synthetic.
  - *Dependencies and identity.* `berman version` now reports the interpreter
    and the installed version of every distribution the declared dependencies
    require, under `environment`, beside the unchanged source digests.
    `berman verify --software` gains `dependencies` and `schemas` checks.
    Versions are recorded context. Nothing fingerprints them, and the contracts
    keep them out of everything qualification does fingerprint.
- **13 November 2026, Hypothesis.** Properties live in `tests/properties/` and
  run with the suite, under `unittest`. The default profile is derandomized and
  keeps no example database, so every run tests the same examples;
  `BERMAN_HYPOTHESIS_PROFILE=explore` randomizes and multiplies them.
  - *What is covered:* lifecycle facts over generated records and evidence
    (unknown is never a negative, only a declaration is an ending, what a packet
    shows and what a record says never change each other); the next plan step
    and run identities over generated histories; preservation primitives under
    interrupted writes; records with any field replaced by any JSON value; and a
    state machine that drives the real workflow through preparation, sessions,
    callbacks, finalization, endings, cancellation, lock contention and
    interruptions at every durable write, each followed by a retry.
  - *Each property was checked against a deliberately broken implementation*
    (twenty mutations, run by hand; nineteen are caught by the properties they
    target, and the twentieth, an overwriting `keep`, is caught at the
    primitive, not by the state machine).
  - *The state machine found two defects, both fixed here with a plain
    regression test.* A reservation whose first write failed left an empty
    `operations/run-NNN/` folder that discovery reported as a recorded run with
    a missing packet, blocking every later preparation; discovery now passes
    over a folder holding nothing but an interrupted write. And
    `berman finish --run RUN`, interrupted after the packet had migrated, could
    not be retried while another engagement was open; it is now resumed, or
    answered if already archived.
  - *One limitation it found is kept and recorded:* once a cancelled packet has
    been moved aside, repeating `cancel` is refused; `status` lists the run and
    the next `berman new` finishes its retirement.
  - *Not modelled:* a process killed outright, which also skips the cleanup an
    exception still runs. None of this is native qualification.
- **13 November 2026, Rich.** Presentation moved out of `cli.py` and
  `verify.py` into `berman.presentation`, which imports no other part of the
  package and is imported only by the command line (two contracts).
  - *Redirected output is unchanged.* `presentation.plain` holds the 0.4.1
    renderers. Nine synthetic results were rendered by the 0.4.1 code before the
    move, and the moved code must reproduce that text byte for byte. A pipe, a
    file, `TERM=dumb` and `--json` never reach Rich.
  - *Rich is used only on an interactive terminal,* for `status`, `verify`,
    `cheatsheet` and the result of `new`. Every distinction is a word and a mark
    with color on top, so `NO_COLOR` loses nothing. `status` adds a table of the
    six lifecycle facts with the evidence for each, or the reason it is unknown;
    the plain text has never shown that, and `--json` always has. `cheatsheet`
    groups commands by whether they can be typed now or wait on the
    researcher's decision.
  - *Recorded text is literal.* No markup is interpreted, and control and
    format characters are written as escapes, since reasons and paths come from
    records. Commands are printed unwrapped so they copy intact.
  - *Read against the researcher's later instruction that the command's
    behavior for its user stay unchanged:* commands, options, exit codes, JSON
    and redirected text are identical. What differs is the look of four
    commands on a terminal. Selecting the plain text everywhere is a one-line
    change in `presentation.show`.
- **13 November 2026, close.** Verified on Python 3.14.9, macOS arm64:
  - *Development environment (uv, locked):* `ruff check`, `ruff format --check`
    and `lint-imports` (8 contracts kept) pass; 361 tests pass, up from 289;
    `berman verify --software` passes with four checks.
  - *A clean clone with pip alone* (`pip install -e . --group dev` under
    Homebrew's Python): the same checks and the same 361 tests pass.
  - *The built wheel, installed with pip in a fresh environment and run from
    outside the checkout:* the copied smoke test passes. It prepares, captures
    with synthetic callbacks, preserves and archives the example study; software
    verification passes; workspace verification is incomplete, as it should be,
    with native access not run and hook trust and native capture not
    established; and a hook process loads no third-party module. The wheel
    holds the nine schemas and no tests, examples or documents.
  - *Byte-sensitive files:* all 24 are identical to commit `018fec4`. Both
    adapters' implementation digests equal those in the adjacent study's
    recorded 0.4.1 identity.
  - *The open questions:* installation does depend on a compiled wheel
    (`rpds-py`); the limits say so. The schemas were checked against real
    history before they were settled. The state machine costs about nine
    seconds per suite run at its default budget; 1,100 further sequences were
    explored by hand. A single wheel installed with `--no-index` fails, as
    predicted; installing from a kept wheelhouse works and is the documented
    route.
  - *Not done, by design:* no native installation was qualified, no study's
    runtime was touched, nothing was tagged, pushed or published. The version
    was then `0.5.0.dev0`; see the entries below. What a study must still do
    to adopt it is in the
    [development guide](../development.md#what-adopting-042-in-a-study-needs).
- **13 November 2026, independent review.** A reviewer given only the runtime
  diff ran 0.4.1 and this tree side by side on disposable workspaces. It found
  the recovery fixes, plain and JSON output, the terminal renderer and hook
  isolation sound, and three defects, all fixed with regression tests:
  - *The first schemas refused what 0.4.1 worked with.* They typed fields the
    code converts (a timeout given as text), required fields only a later
    command reads (a development-only connection), and constrained fields
    nothing reads. Because validation runs when a workspace loads, one such
    value refused every command. This was a change of approach: a schema now
    refuses only structure the code would crash on or silently misread, and
    requires only what loading reads. Of the reviewer's 23 configuration
    variants and 111 operation-record variants, the tree now treats all as
    0.4.1 did except one, deliberately: a recipe whose `connections` is text,
    which used to match by substring. The earlier log entry's "existing
    refusals keep their wording" holds for operation records, requests and
    recipes; for the workspace files the development guide now says exactly
    where the schema speaks first.
  - *A damaged environment was not reported.* With `jsonschema` or `rich`
    unimportable, commands raised, and whether they did depended on the
    terminal. An unimportable validator is now a refusal like any other, and
    presentation falls back to the plain text.
  - *A refusal could be unprintable.* A lone surrogate quoted from a record made
    the message fail to encode and took plain `status` down. Problems are now
    always encodable.
- **13 November 2026, the researcher on changes to the experience.** "If you
  need to change the experience to make things more sensible, you may bring
  some recommendations to me. We will bump to 0.4.2 later anyway." Accordingly:
  - The tree is labelled `0.4.2.dev0`, not 0.5.
  - The Rich rendering is no longer the default on a terminal. It is selected
    with `BERMAN_OUTPUT=rich`; without it a terminal receives the same bytes as
    a pipe. Making it the default is a recommendation below, not a decision
    taken here. This departs from the commission's acceptance criterion for
    Rich in one respect: the improved output exists, is tested and is one
    setting away, but is not what an operator sees unasked.
  - What remains different from 0.4.1 for a user is listed in the development
    guide under version 0.4.2, so that each item can be accepted or reversed.
- **13 November 2026, release 0.4.2.** The researcher decided each
  recommendation below and asked for the release. The Rich rendering became the
  default on an interactive terminal, which restores the commission's criterion
  for it; the other decisions changed no code. The version is `0.4.2`.
- **13 November 2026, verification of the final tree** (`0.4.2.dev0`, Python
  3.14.9, macOS arm64). It supersedes the numbers in the entry headed "close".
  - *Locked uv environment:* `ruff check`, `ruff format --check` and
    `lint-imports` (8 contracts) pass; 368 tests pass, against 289 at the
    start; `berman verify --software` passes.
  - *A clean clone with pip alone:* the same checks and the same 368 tests.
  - *The wheel, installed with pip in a fresh environment and run outside the
    checkout:* the smoke test passes, with software verification passed,
    workspace verification incomplete as it should be, and no third-party
    module loaded by a hook process.
  - *Property tests under the randomized profile:* all 26 pass with ten times
    the default examples.
  - *Against 0.4.1:* all 24 byte-sensitive files are identical to `018fec4`,
    and the nine plain-text fixtures are reproduced byte for byte.
  - Nothing was tagged, pushed or published, no study's runtime was touched,
    and no native installation was qualified.

## Recommendations, and what the researcher decided

Changes to what Berman accepts, prints or refuses that were brought to the
researcher instead of being settled in the work. Decided 13 November 2026.

1. **Make the Rich rendering the default on an interactive terminal.**
   *Adopted.* Redirected output and `--json` stay as they are, and
   `BERMAN_OUTPUT=plain` keeps the plain text on a terminal.
2. **The two recovery fixes.** *Kept.* An empty `operations/run-NNN/` folder
   no longer blocks preparation, and a named `finish` can be retried while
   another engagement is open.
3. **The refusals of lists given as text.** *Kept.* A connection's `documents`,
   `readers`, `checks` or a check's `argv`, or a recipe's `connections`, given
   as text, is refused where 0.4.1 read it character by character.
4. **Let `berman cancel` finish its own retirement.** *Left as it is.* After an
   interruption once the packet has been moved aside, repeating `cancel` is
   refused and the next `berman new` completes it.
5. **Say what a connection lacks when fieldwork is prepared for it.** *Left as
   it is.* A connection without `commission` loads, and `berman new` then stops
   with the bare word `'commission'`.
6. **The version number.** *0.4.2.* Requiring Python 3.14 and adding runtime
   dependencies changes how a study installs Berman, and the project is
   experimental below 1.0.
