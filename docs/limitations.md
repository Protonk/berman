# Current limits

The standalone software is experimental and supports Python 3.14 only. Its
portable tests establish simulated workflow, preservation and rendering
behavior. A fresh installation still needs
native access and capture qualification for its actual CLI versions and platform.
The separated source has not inherited another installation's trust.

Native sandbox access is primarily developed for macOS. Ordinary development
launchers remain a Stewart-specific compatibility adapter, described in
[their guide](development-launchers.md). Named fieldwork connections
are configurable independently of those launchers.

A connected target must be an existing Git checkout with a commit. Linked
worktrees, external Git metadata, containers and external services need explicit
access and evidence arrangements. The current Codex fieldworker profile protects
the project's .git directory; the known fieldworker commit-grant repair remains
outside this source-separation change.

Blinding currently denies the fieldwork workspace and live packet root. Keep
analysis and any historical research ledger inside the denied workspace, or
qualify an expanded access policy before using a different physical arrangement.
Separating Git ownership alone grants no additional access protection.

Claude uses per-launch hooks. Qualification is based on native hook events; the
CLI's hook browser has not demonstrated a review of flag-supplied hooks. The
workspace's CLAUDE_CONFIG_DIR must match its selected configuration home.

Context snapshots contain selected project documents and observed Git state,
not a full backup of every dependency or service. A stored symlink or Git pointer
does not preserve its destination. Preserved originals keep their historical
paths; do not repair them into runnable checkouts.

Installation needs the runtime dependencies, one of which (`rpds-py`, required
by `jsonschema`) is a compiled wheel for one platform and Python version. Their
installed versions are recorded beside the software identity as context. Nothing
fingerprints them; a study that needs the environment's bytes keeps the wheels.

Repeating an interrupted command completes it, with one exception. Once
`berman cancel` has moved an unused packet aside, repeating `cancel` is refused;
`berman status` lists the run as being retired and the next `berman new`
finishes its retirement. The property tests interrupt commands by raising an
error at a durable write. A process killed outright is not modelled.
