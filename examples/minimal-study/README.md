# A minimal study

These files hold the baseline recipe and an optional one-step research plan for a
connection named `example`. They contain no project recordings or qualification
receipts.

The baseline recipe gives a fieldworker one task: plain accounts of work in
progress. Its [instructions](recipes/baseline/INSTRUCTIONS.md) are everything a
recipe says to a fieldworker; the opening message that points to them is the
same for every recipe and is shown in the
[fieldwork guide](../../docs/usage.md#what-a-fieldworker-receives). Start a
study's own recipe from the baseline and change what the study needs: the
focus, what to attend to, how the project work begins. Keep exactly one
`{{notes_directory}}` placeholder.

The baseline does not tell the fieldworker that the session is being recorded.

Initialize a workspace with `berman init /path/to/study --project /path/to/project
--connection example`. Copy the `recipes/` directory beside its berman.json.
Set its documents and project checks to actual inputs. After qualifying the
installation, prepare a direct assignment with:

```sh
berman --root /path/to/study new --connection example --recipe baseline
```

To use an ordered plan instead, also copy `plans/` and select
`plans/example/plan.json` as the `plan` value of the `example` connection in
berman.json. Bare `new --connection example` then selects its next step. A
direct assignment does not consume that step. The example's `commitment` and
`relationships` are optional annotations, not enforced promises.

The researcher chooses direct assignments and any plan. Configure and
qualify the installation as described in the [fieldwork guide](../../docs/usage.md)
before preparing or launching a fieldworker.
