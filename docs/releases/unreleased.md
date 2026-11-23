# Unreleased

The latest release is [Berman 0.26.9](0.26.9.md).

The person who opens and closes episodes is now the producer throughout the help, refusals, templates and guides. This changes the `close` and `review` help, the refusals for a closed episode and a missing ending reason, and the placeholder in the editable `job.md`.

## Unattended sittings

`berman start` and `berman resume` take `--unattended` with a `--prompt` message. The actor runs through `claude --print` or `codex exec` without its interactive interface, and Berman prints the sitting with the response the CLI reported, the denials Claude Code reported, and the limitations Berman found. The native output stays beside the sitting record and in the episode record. Attendance belongs to each sitting, so attended and unattended sittings can alternate in one native session. Berman adds no permissions to an unattended actor. [Unattended sittings](../operating.md#unattended-sittings)

`--job` is now optional, and the opening no longer tells the actor to continue its ordinary work. It names the episode, Berman’s record and the return directory, followed by any additional jobs. `berman start --prompt` follows the opening with a first message in either attendance.

Repeating an unattended request after the launcher stopped reads what the actor’s output reports without starting it again. Because the Codex CLI drops the episode lock it inherits, a sitting now records when its actor process started, and later sittings, retries and closeouts refuse while that process is still running. This also covers attended Codex sittings. A sitting refuses while the checkout is missing. If no reviewed hook bound the first unattended Codex sitting, Berman binds the session that `codex exec` names in its output. The record’s coverage lists each sitting’s limitations and states that the Git observations show the checkout at two times, not who changed it. Both Git observations now carry the time they were made.

When the first sitting ends before the actor’s CLI starts a session, `berman start EPISODE --request LABEL` delivers the opening again. Berman refuses another opening once it knows of a native session, and `resume` now refuses while it knows of none, instead of blaming the Codex hook or launching a Claude Code resume that cannot succeed.

A callback that arrives before the actor’s CLI has written its transcript, as Claude Code’s first callbacks do, now notes that instead of appearing in the record’s coverage as a gap. A transcript missing after one has been captured is still a gap.
