# Current limits

The standalone software is experimental and supports Python 3.14 only. Its
portable tests establish simulated commissioning, staging, custody and rendering
behavior. A fresh installation still needs native qualification for its actual
CLI versions and platform: run a designated one-turn assignment and evaluate it
with `berman remote qualify RUN`. No installation inherits another's trust.

An engagement in a guest study has these limits:

- Observation follows the bound native session, not Berman's process. Codex
  hooks live in the harness home, so a session resumed by hand is still staged.
  Claude receives its hooks per launch: a Claude session resumed outside
  `berman remote resume` sends no callbacks, and only the next closeout read of
  its transcript sees that work.
- The guest learns of a sitting when it ends. While one runs, `status` shows the
  facts recorded before it started.
- Callbacks after a frozen collection wait on the host under `late/`. They reach
  custody only when a supplement is declared and collected. A supplement is a
  second recording with its own edition; no edition joins it to the first.
- A collection never fails for one member, and so it can be incomplete in ways
  it names: a link, a special or unreadable entry, a file over a bound. Those
  are recorded by name, not kept. Read `omitted` and the limitations before
  relying on a collection.
- Acceptance with `--missing-reason` puts a named gap in the recording for good.
  It closes that collection; the same evidence arriving later needs a
  supplement. If any file of the parent transcript generation is among the
  missing, the recording verifies without making any claim about the parent.
- No command cancels a commission. An unused run stays in the ledger.
- Host staging is never pruned. Every operation's packet, callbacks and
  transcripts remain under the spool, beside later packets, until someone
  removes them. Remove an operation's folder only after the guest has preserved
  it and any supplement.
- Custody builds a recording and its edition in memory. A collection near the
  512 MiB bound can need more memory than a small guest has.
- An interactive Claude launch shows Claude's own trust prompts for the checkout
  and for each new packet directory before the first turn.

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
