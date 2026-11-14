# A minimal study

These files hold the baseline recipe and a one-step research plan for a
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
--connection example`. Copy the `recipes/` and `plans/` directories beside its
berman.json. Select `plans/example/plan.json` as the `plan` value of the `example`
connection in that file. Set its documents and project checks to actual inputs.

The researcher decides which engagements belong in the plan. Configure and
qualify the installation as described in the [fieldwork guide](../../docs/usage.md)
before preparing or launching a fieldworker.
