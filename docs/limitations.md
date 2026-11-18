# Current limits

Each entry is a property of the present software or a stated gap in what has
been established. An installation's own history belongs with that
installation, not here.

## What is established where

The source suite and `berman verify` establish simulated commissioning,
staging, custody and rendering, and an intact installed package. They start no
harness. A fresh installation needs native qualification for its actual CLI
versions and platform ([qualifying an installation](operating.md#qualifying-an-installation)).
No installation inherits another's qualification or hook trust.
[What each check establishes](development.md#what-each-check-establishes)

Only Python 3.14 is supported and tested. The executor is written for macOS
harness homes; the controller runs in a Linux guest reached over OpenSSH.

## An engagement

- Berman starts nothing by itself. Its commands run when someone runs them and
  its hooks when a harness calls them. It schedules nothing, assigns no work
  and ends no engagement. Launching a sitting waits in the terminal that ran
  it.
- A recipe's `recording_protocol` must be `work-accounts/1` or
  `participant-inquiry/1`, and a recipe's `coverage` must be `["notes"]`. The
  schema admits nothing else, and Berman does nothing with either value beyond
  keeping the recipe's fingerprint with the commission.
- Observation follows the bound native session, not Berman's process. Codex
  hooks live in the harness home, so a session resumed by hand is still staged.
  Claude receives its hooks per launch: a Claude session resumed outside
  `berman-research resume` sends no callbacks, and only the next closeout read
  of its transcript sees that work.
- The guest learns of a sitting when it ends. While one runs, `status` shows
  the facts recorded before it started.
- Codex gives a SessionEnd or Interrupt hook at most three seconds. The hook
  writes the raw callback before it reads the transcript, so one that is cut
  off leaves a callback with no transcript generation, which the collection
  names. Staging a 2 MB transcript takes a small fraction of that time.
- Callbacks after a frozen collection wait on the host under `late/`. They
  reach custody only when a supplement is declared and collected. A supplement
  is a second recording with its own edition; no edition joins it to the first.
- A collection never fails for one member, and so it can be incomplete in ways
  it names: a link, a special or unreadable entry, a file over a bound. Those
  are recorded by name, not kept. Read `omitted` and the limitations before
  relying on a collection.
- Acceptance with `--missing-reason` puts a named gap in the recording for
  good. It closes that collection; the same evidence arriving later needs a
  supplement. If any file of the parent transcript generation is among the
  missing, the recording verifies without making any claim about the parent.
- No command cancels a commission. An unused run stays in the ledger. A plan
  gives out its next step only when every earlier commission from that plan
  has started its first sitting, so a plan commission that is never launched
  holds the plan.
- An exposure account stays in the guest's ledger. No recording or edition
  carries it, and `status RUN` reports only how many there are.
- Study inputs are files in the guest. Adding or changing a connection, recipe
  or plan takes a shell there; no connector command edits them.
- No connector command copies a recording or edition out of the guest.
  Closeout prints their guest paths.

## Host staging

- Host staging is never pruned. Every operation's packet, callbacks and
  transcripts remain under the spool until someone removes them. Remove an
  operation's folder only after the guest has preserved it and any supplement.
- A worker's packet lies inside its operation folder, beside that operation's
  callbacks and transcripts, and every operation of a study shares one spool.
  The harness is given the packet as a working directory, not the spool, but
  nothing prevents a worker from reading neighbouring operations there.
- An interactive Claude launch shows Claude's own trust prompts for the
  checkout and for each new packet directory before the first turn.

## Projects

The project must be a Git checkout with a commit. Project checks run as host
subprocesses in the checkout; a linked worktree or an external service can need
its own declared checks. Context snapshots contain selected documents and the
observed Git state, not a backup of every dependency or service. A stored
symlink or Git pointer does not preserve its destination. Preserved originals
keep their historical paths; do not repair them into runnable checkouts.

## Separation

Plans and recordings are kept in the guest and are not sent to the host. The
executor needs the assigned packet and space for working notes. A study that
keeps earlier engagements from later workers keeps its trees outside project
searches, including configured search roots and symlinks followed by project
tools. A guest on the same machine is visible to
anyone with the host account, through the VM and its SSH configuration, and
shares the machine's failure domain. A worker who inspects installed hooks can
recognize that sessions are staged. This separation is an arrangement of
directories, not containment. Where a discovery matters to how the record will
be read, add an [exposure account](operating.md#exposure); discovery alone
leaves the engagement's status intact.

## Custody

- Custody builds a recording and its edition in memory. A collection near the
  512 MiB bound can need more memory than a small guest has.
- Installation needs the runtime dependencies, one of which (`rpds-py`,
  required by `jsonschema`) is a compiled wheel for one platform and Python
  version. Their installed versions are recorded beside the software identity
  as context. Nothing fingerprints them; a study that needs the environment's
  bytes keeps the wheels.

## Not yet established

These are open observations, not properties to rely on in either direction.

- Claude's `/hooks` browser has not been seen to list hooks supplied through a
  per-launch settings file. Qualification of Claude capture therefore rests on
  native hook events, not on a review in that browser.
- An interactive Codex `resume` sitting and commissioning through a plan have
  synthetic coverage only.
- A forced power-off of the guest or the host, and a hook that times out while
  a collection holds the operation's lock, have not been exercised.
