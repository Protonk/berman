# Berman mini-CI

From a macOS core checkout, run one qualification against a provisioned Linux
guest. The [Lima example config](../scripts/ci/qualification.json) works with
the guest from the [deployment guide](guest-deployment.md#provision-once).

```sh
uv run berman ci --config scripts/ci/qualification.json
```

For another guest, supply a JSON file with its SSH `host` alias and optional
`ssh_config` path. The guest needs batch SSH access, Python 3.14 with venv,
and access to the runtime dependency wheels through pip. The Mac needs the
locked development environment, Git, and Python 3 on PATH for the fixture.

## The integration contract

One [assignment](../tests/fixtures/ci/study/recipes/access/INSTRUCTIONS.md)
and three questions govern integration.

1. **Did the intended controller answer using the candidate?** Run the five
   source checks, build one wheel, compare its SHA-256 on each machine, verify
   each installation against the wheel and the checkout, and get a bounded
   controller receipt for a new qualification collection over SSH.
2. **Could the worker do the assigned work?** The checkout contains a fresh
   value. The worker reads it, edits a source file, runs the fixture test and
   puts the value and the passing test's result code into a packet note. Check
   the launch, attributed capture, initial and final observations, digest of
   the tested edit, and note.
3. **Can a reader recover that account?** Submit the fixture's explicit ending
   and close out through the connector. Verify detached copies of the recording
   and edition against the guest's digests. Both must identify this collection
   and run, with the same recording manifest and original note bytes.

The ordinary source suite owns regression coverage. Change these three questions
or the fixture assignment deliberately when the integration contract changes.
CI is a development command. Study adoption and release publication are separate
researcher decisions.

## Worker selection

The default executable performs the assignment on macOS. It supplies a synthetic
Claude transcript and hook events to the executor. Its receipt identifies the
worker as `deterministic`. SSH, native file access by that process, transfer,
custody, publication and detached verification are exercised.

For access and capture evidence from an installed harness, select it explicitly
in an ordinary interactive terminal. Codex uses `--model gpt-6-luna` for
hook review and the assignment. Claude uses `--model haiku` for the assignment.
The CI receipt records the requested model.

```sh
uv run berman ci --config scripts/ci/qualification.json --live codex
uv run berman ci --config scripts/ci/qualification.json --live claude
```

The executable comes from PATH or the config's optional `executables` map
(e.g. `{"claude": "/absolute/path/to/claude"}`). CI binds it to a fresh
checkout, a private packet and a dedicated persistent home under
`~/.local/share/berman-ci/`. Sign in there and review the actual harness's
trust and access prompts in the terminal. Exit the sitting after the worker
has written its note. CI then supplies the ending specified by this fixture.

Claude receives its hooks per launch. Its opening callbacks can arrive before
the transcript file exists. The recording names those callbacks and the ones
that obtained a verified snapshot. The original event receipts retain the
observation at each point.

For Codex, CI verifies the installed router against the candidate. It installs
the initial hook definition in a new home. If the registration needs updating,
CI prints the proposed file and destination. Preserve unrelated hooks and
close active captures before updating a registration.

Start with the `--live codex` command above, including on first use. In the
first Codex window, sign in if prompted, open `/hooks`, inspect and trust the
candidate's Berman hooks, and exit. CI checks the saved sign-in with
`codex login status` before commissioning. It then opens the assignment using
the same executable, checkout and persistent Codex home. This binding uses
Codex's native `workspace-write` mode and adds the packet as a writable
directory. Exit the sitting to start collection. A live pass requires
attributed opening and stop callbacks, a captured generation, an intact
parent transcript and an observed successful exit.

## Results and retries

The command prints the result and the path to `receipt.json`, and exits nonzero
on failure. The receipt identifies the worker, target host, candidate wheel
digest, collection and run, capture checks, closeout and detached evidence.
The native exit and session attribution are recorded before closeout. A
failed sitting still goes through custody. The receipt reports both outcomes
and retains any detached evidence that verified. Checks still to be established
have a null result. Build and installation
diagnostics are in `build.log` and `install.log` alongside it.

On each invocation the Mac creates `~/.local/share/berman-ci/trials/UUID/` for
the checkout, receipt, wheel and detached ZIPs. The guest creates a new
`~/research/ci-UUID/` study. Both machines cache verified candidate runtimes
under `~/.local/share/berman-ci/wheels/SHA256/`; the executor spool belongs to
CI. A retry starts a fresh trial and preserves the earlier run for inspection.
