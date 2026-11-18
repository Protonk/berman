# Changes since 0.26.5

Not a release. This collects what the next release's notes need to say; it is
renamed for that release when one is prepared.

## For operators

- `berman cheatsheet` (also `berman-research cheatsheet`) prints the commands
  that apply to a study now. It sends one `status` to the guest and writes
  nothing. An earlier guest controller's overview has no supplement facts;
  the cheatsheet then says so and reports the rest.
- `prepare` prints the packet path and the launch command on stderr, beside the
  guest's receipt on stdout.
- The mini-CI builds its candidate wheel through a source distribution, so a
  stale `build/` directory can no longer put removed modules into it.
- `status` without a run adds `harness`, `sittings` and `supplements` to each
  row.
- `berman verify` reports `passed` and `checks` in its JSON. The `--software`
  flag and the terminal formatting settings of 0.26.5 are gone.
- New recordings are `berman.recording/3` and new editions
  `berman.reading-edition/3`. 0.26.5 cannot read them. This software still
  verifies guest `recording/2` and single-recording `reading-edition/2`.
- A recording's coverage names the callbacks that had no transcript and the
  ones that obtained a verified snapshot. A failed sitting is preserved when
  associated-session discovery was unavailable. An interrupted sitting stays
  open while its process identity is uncertain.
- Project checks take string arguments and the placeholders listed in the
  executor guide. Each plan step needs a string identity and a recipe.

## For an adopting installation

Executor module paths and bytes changed, so the module digests in its
preparation and launch receipts differ from 0.26.5. An adopting host installs
the build in its own environment, reviews its Codex hooks in the actual CLI
and takes native evidence again. The form of the hook command is unchanged:
`python -I -B -m berman.executor --spool SPOOL hook --home HOME`.

The guest entry points are unchanged: `python -I -m berman.controller` and
`python -I -m berman.custody`.

## For developers

- `berman.capture`, `berman.sources`, `berman.snapshots`, `berman.checks`,
  `berman.recipes` and `berman.config` are gone. Executor-only code is under
  `berman.executor` (`sources`, `snapshots`, `checkout`, `claude`, `codex`);
  recipes and plans are in `berman.study`.
- The layers contract refuses imports between the controller and the executor,
  and the whole executor is held to the standard library.
- `berman ci` qualifies one candidate wheel through the native executor and the
  SSH controller, with an optional live harness.
- `docs/usage.md` and the operator half of `docs/connector.md` are now
  `docs/operating.md`; the wire contract is `docs/protocol.md`.
