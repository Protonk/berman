# Developing Berman

Use a Linux login with Python 3.13 or later and Git. For the locked development environment use uv; the project’s default development interpreter is Python 3.14. From the checkout run:

```sh
uv sync --locked
uv run ruff check
uv run ruff format --check
uv run lint-imports
uv run python -m unittest discover -s tests -t .
uv run berman verify
```

The CLI calls `episodes.py` for an opening, the interactive sittings and the manager’s ending. `capture.py` attributes native events and `snapshots.py` publishes transcript generations. `records.py` collects a closed episode, verifies copies, and assembles selected sources for a question. The wheel carries the editable job and both examples in `src/berman/templates/`.

The suite uses real temporary Git checkouts, subprocesses, and scripted actor executables to exercise ordinary work, commissioned work, resume, closure, detached verification and review. Capture tests check what happens to bytes across interrupted and non-prefix transcript updates. A real actor login, reviewed Codex hooks, a manager-chosen project and a native sitting are separate evidence.

For a second check, build an installed wheel in a Linux container using the committed sources. Run these commands from the checkout in the Linux development login:

```sh
context=$(mktemp -d)
git -C . archive HEAD | tar -x -C "$context"
podman build -t localhost/berman-check -f "$context/apparatus/container/Containerfile" "$context"
podman run --rm --network=none localhost/berman-check
```

Use the native development login for interactive actors. [Lima setup on macOS](../apparatus/lima/README.md)

Versions use `0.YY.N` and advance only for a requested release. The installed-source digest distinguishes development builds with the same package version. Keep public tags and releases intact.
