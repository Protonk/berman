# Capture

Live capture belongs to the executor. Its hook routing, per-launch Claude
settings, offline staging and closeout are described in
[native execution](executor.md). One exclusive router serves a Codex home, and
launch refuses a `hooks.json` that holds any other Berman handler.

The executor recognizes obsolete hook commands during its exclusivity check.
Replace any conflicting handler and review the installed router in the actual
CLI before launch. [Hook setup](executor.md#hooks-and-launch)

The native Codex and Claude transcript renderers live in `berman.capture`.
A committed generation includes the transcript, derived reader, metadata and
receipt history. [Capture generations](usage.md#capture-generations)
