# Ordinary development on macOS

Run Codex or Claude from the project checkout using the normal harness and
credentials. Fieldwork packets and capture registration belong to designated
assignments. An ordinary developer can select the harness's usual permissions.

For a local workspace configuration, `berman dev --harness codex` or
`berman dev --harness claude` starts its configured native binary in the
selected checkout with the terminal's environment. Pass native options after
`--`. Use `--check` alone to run the same binary with `--version`.

Prior releases could install `stewart-dev-*` commands, a Codex permissions
profile and Claude settings. Those files and their receipts describe that
installation. The current dev command bypasses the old per-connection
`development` wrapper paths. A shell alias or project script that invokes
an old wrapper directly will still use its old policy until its owner changes
the invocation.
