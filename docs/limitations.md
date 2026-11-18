# Current limits

The standalone software is experimental and supports Python 3.14 only. Its
portable tests establish simulated commissioning, staging, custody and rendering
behavior. A fresh installation still needs native qualification for its actual
CLI versions and platform: run a designated one-turn assignment and evaluate it
with `berman remote qualify RUN`. No installation inherits another's trust.

Fieldwork runs only through the controller and executor. This version carries
no other way to prepare, launch, capture, end, preserve or supplement an
engagement, and it creates no co-located workspace.

A co-located workspace made by release 0.26.3 or earlier is read-only here:

- Anything unsettled in it has to be closed with the release that opened it.
  This version reports the work and cannot finish, cancel, migrate or package
  it. A study with outstanding work keeps that released runtime installed.
- Authority does not move by itself. Moving a study to guest custody is the
  researcher's procedure in the [deployment guide](guest-deployment.md#historical-studies):
  export, import, and reserve every run number already used.
- Hook trust, native capture and native access of a co-located installation are
  not checked. Its recorded reviews, support trials and access receipts remain
  evidence of their own dates and say nothing about this version.
- `status` reads without locks and never compares an open capture with its
  native source. A workspace its own release is operating at the same moment can
  be read mid-operation; read it again when that operation has finished.
- A run whose records cannot be read is reported as a problem. What it awaits is
  unknown, so a clean `settled` check requires a clean `workspace` check.

Research plans are optional ordered assignments. Their `commitment` and
`relationships` fields are annotations; Berman does not enforce cross-project
promises. A study can retain such promises in its own practice.

The project must be a Git checkout with a commit. Project checks run as host
subprocesses in the checkout; a linked worktree or external service can need its
own declared checks. A model executing tools under hooks requires a native
session, which the portable tests do not start.

Research plans, prior analysis and fieldwork history belong in the research
environment. The executor needs the assigned packet and space for working
notes. Keep the research trees outside project searches, including configured
search roots or symlinks followed by project tools. A participant who inspects
installed hooks may recognize the study. Record consequential exposure and
the researcher's response alongside the engagement; discovery alone leaves
its status intact.

Claude receives its capture hooks at launch. Qualification is based on native
hook events; the CLI's hook browser has not demonstrated a review of
flag-supplied hooks.

Context snapshots contain selected project documents and observed Git state,
not a full backup of every dependency or service. A stored symlink or Git pointer
does not preserve its destination. Preserved originals keep their historical
paths; do not repair them into runnable checkouts.

Installation needs the runtime dependencies, one of which (`rpds-py`, required
by `jsonschema`) is a compiled wheel for one platform and Python version. Their
installed versions are recorded beside the software identity as context. Nothing
fingerprints them; a study that needs the environment's bytes keeps the wheels.

The tests of the co-located reader build their workspaces from synthetic
records written as release 0.26.3 wrote them. They show that this version reads
those shapes; a real workspace is the only evidence of what a real one holds.
