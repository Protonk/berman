# A minimal study

These files hold two recipes and an optional one-step plan for a connection
named `example`. They contain no project recordings or qualification receipts.

A recipe's instructions are everything it says to a worker; the
[operating guide](../../docs/operating.md#what-a-fieldworker-receives) says what
else reaches them.

| Recipe | Asks the worker to |
| --- | --- |
| [baseline](recipes/baseline/INSTRUCTIONS.md) | Do the project's work and keep plain accounts of it. It leaves the work itself open. |
| [project-review](recipes/project-review/INSTRUCTIONS.md) | Review where the project stands for someone who has been away from it, correct what is small and plainly wrong, and report the rest. |

Start a study's own recipe from either and change what the study needs: what
the work is, how it begins, what to attend to and what account to keep. Keep
exactly one `{{notes_directory}}` placeholder. Neither recipe says anything
about the session being recorded; a study that wants that said writes it into
its instructions.

These recipes show an engagement that arranges project work. A research study
is written the same way, with instructions that say what its workers attend to
and record
([using Berman for research](../../docs/operating.md#using-berman-for-research)).

A study lives in the guest. Create it as the
[deployment guide](../../docs/guest-deployment.md#the-study-in-the-guest)
describes: a `collection.json` with the study's UUID, a
`connections/example/connection.json`, and this `recipes/` directory copied
beside them. Set the connection's documents and project checks to actual inputs.
After binding the connection on the host and qualifying the native
installation, `berman cheatsheet` prints the commission command for it. A
direct assignment with a recipe is:

```sh
berman-research commission --request review-after-release --connection example --harness claude --recipe project-review
```

To use an ordered plan instead, also copy `plans/` and commission with
`--plan example` in place of `--recipe NAME`. The plan's next unstarted step
supplies the recipe. A direct assignment does not consume that step.

The commissioner chooses direct assignments and any plan. The
[operating guide](../../docs/operating.md) follows an engagement from its
commission to its verified reading edition.
