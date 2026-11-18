# Capture

Live capture belongs to the executor. Its hook routing, per-launch Claude
settings, offline staging and closeout are described in
[native execution](executor.md). One exclusive router serves a Codex home, and
launch refuses a `hooks.json` that holds any other Berman handler.

Releases up to 0.26.3 captured through a second, co-located path: a router
installed from a workspace, per-launch Claude routing, capture adapters with
recorded hook-trust reviews, native support trials and a low-level recovery
module. This version has none of them. A Codex home that such a release
operated can still name its router or a directly installed collector in
`hooks.json`; the executor recognizes those commands and refuses to launch
beside them. Replace them at the installation transition, with captures closed,
as [native execution](executor.md#hooks-and-launch) describes. A capture that
release opened is closed with that release.

The historical `src/berman/_legacy/capture_session.py` is frozen at SHA-256 `f87241a6b9506590fffa4e2a964f37bb7f7c719e508f4024661b45c57f983a99`.
Its bytes are preserved evidence and are never edited. Nothing installs it or
runs it as a hook. Berman loads it once for two things: the reader that derives
the reading copy of a Codex transcript, which the executor uses each time it
stages a capture, and the run and session identity patterns.

Captured packets, old and new, hold committed generations: `session/current.json`
points at one complete generation of transcript, derived reader, metadata and
receipt history. [The fieldwork guide](usage.md#capture-generations) describes
the format.

A frozen capture and a declared engagement ending are different facts.
A copied verification receipt does not qualify a new installation.
