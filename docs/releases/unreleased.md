# Unreleased

The latest release is [Berman 0.26.8](0.26.8.md).

`berman version` is retired. `berman verify` without a record prints the same identity of the installed build and checks its files against the wheel.

An episode’s `episode.json` lists the additional jobs under `jobs` only. The joined `job` field is gone. If `opening.md` no longer matches the opening recorded in `episode.json`, `start` and `resume` now refuse in those terms.
