# Developing Berman

Use a Linux login with Python 3.13 and Git. Berman is developed and checked on Python 3.13, the `python3` of Debian 13 that the Lima guest runs. For the locked development environment use uv. From the checkout run:

```sh
uv sync --locked
uv run ruff check
uv run ruff format --check
uv run lint-imports
uv run python -m unittest discover -s tests -t .
uv run berman verify
```

The CLI calls `episodes.py` for an opening, the interactive sittings and the manager’s ending. `claude.py` and `codex.py` each hold what Berman knows about one actor’s CLI: its home, callbacks, launch arguments, session store and transcript format. Start there when a harness changes. `capture.py` attributes native events and `snapshots.py` publishes transcript generations. Installed Codex hooks run `python -m berman.capture`, so moving or renaming that module breaks ordinary Codex work until the hooks are reinstalled and reviewed. `records.py` collects a closed episode, verifies copies, and assembles selected sources for a question. `installation.py` checks the installed files against the wheel. The wheel carries the editable job and both examples in `src/berman/templates/`.

The suite uses real temporary Git checkouts, subprocesses, and scripted actor executables to exercise ordinary work, episodes with one or more jobs, resume, closure, detached verification and review. Capture tests check what happens to bytes across interrupted and non-prefix transcript updates. A real actor login, reviewed Codex hooks, a manager-chosen project and a native sitting are separate evidence.

For a second check, install the committed sources in a Debian 13 container as the README describes. The container runs `berman verify` against that installation and then the suite. Run these commands from the checkout in the Linux development login:

```sh
context=$(mktemp -d)
git -C . archive HEAD | tar -x -C "$context"
podman build -t localhost/berman-check -f "$context/development/container/Containerfile" "$context"
podman run --rm --network=none localhost/berman-check
```

Use the native development login for interactive actors. [Lima setup on macOS](../development/lima/README.md)

Versions use `0.YY.N` and advance only for a requested release. The installed-source digest distinguishes development builds with the same package version. Keep public tags and releases intact.
