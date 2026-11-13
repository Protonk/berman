# Capture and recovery

Use [the operator workflow](usage.md) for preparation, endings, supplements and
migration. Inspect low-level recovery options with
`python -I -m berman.recovery --help`. Recovery requires explicit workspace and
packet roots; preserve the reason and resulting receipts.

Live Codex hooks enter through `python -I -B -m berman.capture.router`.
Claude uses `berman.capture.claude_router` with per-launch settings. Generated
commands select the workspace's `runtime_python`, the workspace and the harness home explicitly.
Do not install direct collector handlers alongside the exclusive router.

The historical `src/berman/_legacy/capture_session.py` is frozen at SHA-256 `f87241a6b9506590fffa4e2a964f37bb7f7c719e508f4024661b45c57f983a99`.
It remains an internal compatibility implementation. Its original default paths
are not the package's workspace discovery mechanism; adapters pass explicit roots.

A finalized capture and a declared engagement ending are different facts.
A copied verification receipt does not qualify a new installation.
