# Capture and recovery

Use [the operator workflow](usage.md) for preparation, endings, supplements and
migration. Inspect low-level recovery options with
`python -I -m berman.recovery --help`. Recovery requires explicit workspace and
packet roots and acts only on a run that has an operation record naming a
current adapter; preserve the reason and resulting receipts.

In the local-workspace workflow, Codex hooks enter through `python -I -B -m berman.capture.router`.
Claude uses `berman.capture.claude_router` with per-launch settings containing capture events only.
The router checks the reserved session identity and leaves file-tool authorization
to Claude. Generated commands select the workspace's `runtime_python`, the workspace and the harness home explicitly.
The local install command refuses a Codex home containing the host executor's
router. The executor tracks active sessions in its own spool. Use one router per
Codex home.

The historical `src/berman/_legacy/capture_session.py` is frozen at SHA-256 `f87241a6b9506590fffa4e2a964f37bb7f7c719e508f4024661b45c57f983a99`.
It remains an internal compatibility implementation. Its original default paths
are not the package's workspace discovery mechanism; adapters pass explicit roots.

A finalized capture and a declared engagement ending are different facts.
A copied verification receipt does not qualify a new installation.
