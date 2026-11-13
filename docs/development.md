# Maintaining Berman core

`berman verify` from this checkout runs the main and launcher test suites and
checks the frozen collector. It loads no study configuration. The suites create
disposable repositories, synthetic packets and simulated callbacks; they do not
spend model tokens or establish native capture.

`berman --root PATH verify` checks a selected fieldwork installation, including
its records, capture qualification and generated archive. The report lists
failures, checks not run, facts not established and work it never performs.
`berman --root PATH verify --native-access` adds the native access check. Keep
receipts with `--record experiments/NAME` in that workspace.

## Compatibility and source identity

`tools/capture_session.py` is frozen at SHA-256 `f87241a6b9506590fffa4e2a964f37bb7f7c719e508f4024661b45c57f983a99`.
Never edit it or install it directly. The exclusive router owns session locking
and chooses the appropriate collector. Historical marker and schema names stay
readable even when they name Stewart.

VERSION and the executable/module hashes identify this source. New operation
records carry that identity. A workspace's software.json records its initial
selection; adopting another version is an explicit maintenance operation.

Run both suites after implementation changes. Changes to capture bytes or hook
commands require installation-specific qualification before a real study adopts
them. Closed captures, CLI trust where applicable and native capture are separate
facts. Never copy another workspace's trust receipt as evidence of your installation.

## Preserved material and Git

Preserved packets, inventories, preparation snapshots, recordings and linked
qualification records belong to the fieldwork workspace. Never edit them in
place or run ordinary Git commands inside their nested repositories. Archive
verification checks recorded bytes, modes and modification times where recorded.

Keep maintenance evidence with the installation it checked. General software
regressions should use small synthetic fixtures in this repository. No actual
study is required for software development.

## Source layout

The executable loads tools/ in isolated Python mode. berman_cli selects a
workspace; berman_source provides source identity, initialization and discovery.
The workflow, records and preservation modules operate on the selected Store.
Capture implementation paths resolve against installed code, while generated
router arguments identify the selected workspace and harness home explicitly.

The archive modules use only the standard library and each other. The import
and frozen-helper rules in tools/test_boundaries.py apply to added modules too.
