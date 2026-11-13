# Developing Berman

Use Python 3.11 or later. From the repository root:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
python -m unittest discover -s tests -t .
berman verify --software
```

Tests create disposable repositories, synthetic packets and simulated callbacks.
They do not spend model tokens or establish native capture. The installed
`berman verify` checks package integrity and, when selected, a fieldwork workspace.
It never runs the developer tests or requires this checkout.

Build a distribution with `python -m pip install build` followed by
`python -m build`. Install the wheel in a fresh virtual environment and run it
from outside this repository. Runtime dependencies are all in the Python standard
library; setuptools is a build dependency only. Version metadata lives in
pyproject.toml and is read from the installed distribution.

## Code map

| Location | Responsibility |
| --- | --- |
| `src/berman/cli.py`, `workspace.py` | Command interface, workspace discovery and initialization. |
| `workflow.py`, `records.py`, `lifecycle.py` | Engagement decisions, journals and resumable operations. |
| `recipes.py`, `preserve.py`, `recovery.py` | Preparation, retained records and explicit low-level recovery. |
| `capture/` | Exclusive session routing, collector adapters and locking. |
| `harnesses/` | Access policies, native checks, Claude settings and optional development launchers. |
| `archive/` | Selection, reading aids and deterministic packaging, including historical readers. |
| `installation.py`, `verify.py` | Installed resources, diagnostics and qualification receipts. |
| `tests/` | Unit, integration and compatibility tests with synthetic fixtures and shared helpers. |

Workspace paths come from the selected Store. Installed resources come from the
package. Generated commands name the installed interpreter and use isolated
module execution; they do not depend on the current directory or PATH finding a
particular Berman installation. Probes are copied into disposable sandboxes.

## Compatibility and qualification

`src/berman/_legacy/capture_session.py` is frozen at SHA-256 `f87241a6b9506590fffa4e2a964f37bb7f7c719e508f4024661b45c57f983a99`.
Never edit it or install it directly. The exclusive router owns session locking
and chooses the collector. Historical markers and schema names remain readable.

New operation records carry the package version and runtime source digests.
A workspace's software.json records its initial selection. Adopting another
installation is explicit maintenance: changed capture bytes or hook commands
require closed captures and renewed installation-specific qualification, including
actual CLI trust review where applicable. Never reuse another study's receipts.

The 0.2 package removes the checkout launcher and symlink installer. Install with
pip instead. Reading editions use `berman --root PATH archive`; this command and
archive verification use the active package interpreter. The workspace's python
setting remains the interpreter for native harness callbacks and must have the
chosen package installed. `berman verify --skip archive` remains available;
the former test-suite skip options are removed.

Version 0.3 adds what adopting the package in an existing study needed. An
installation maintains one adapter per harness; `legacy-codex` is compatibility
code, reported and renewed only where a connection still selects it. berman.json
gains `runtime_python` (the interpreter generated hook commands name),
`qualified_adapters` (adapters verification requires whether or not a connection
selects them) and `edition` (the reading edition the workspace maintains).
`berman capture definition` prints or writes the workspace hook template.
Operation records name `runtime_python` when it differs from `python`, and a
later change to it is refused like any other installation change.

## Preserved material

Never edit preserved packets, inventories or recordings in place or run ordinary
Git commands inside their nested repositories. Archive verification checks
recorded bytes, modes and modification times where recorded. Keep maintenance
evidence with the installation it checked.

`berman --root PATH verify --native-access` adds a native access check. Use
`--record experiments/NAME` to retain receipts in that workspace. Every report
distinguishes failures, checks not run, facts not established and work never
performed. Rebuilding an archive, native support trials and actual CLI trust
review are separate actions. See [current limits](limitations.md).
