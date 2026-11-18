# A minimal study

These files hold the baseline recipe and an optional one-step research plan for a
connection named `example`. They contain no project recordings or qualification
receipts.

The baseline recipe gives a fieldworker one task: plain accounts of work in
progress. Its [instructions](recipes/baseline/INSTRUCTIONS.md) are everything a
recipe says to a fieldworker; the
[operating guide](../../docs/operating.md#what-a-fieldworker-receives) says what
else reaches them. Start a study's own recipe from the baseline and change what
the study needs: the focus, what to attend to, how the project work begins.
Keep exactly one `{{notes_directory}}` placeholder.

The baseline does not tell the fieldworker that the session is being recorded.

A study lives in the research guest. Create it as the
[deployment guide](../../docs/guest-deployment.md#the-study-in-the-guest)
describes: a `collection.json` with the study's UUID, a
`connections/example/connection.json`, and this `recipes/` directory copied
beside them. Set the connection's documents and project checks to actual inputs.
After binding the connection on the host and qualifying the native
installation, `berman cheatsheet` prints the commission command for it. A
direct assignment with this recipe is:

```sh
berman-research commission --request first-account --connection example --harness claude --recipe baseline
```

To use an ordered plan instead, also copy `plans/` and commission with
`--plan example` in place of `--recipe baseline`. The plan's next unstarted
step supplies the recipe. A direct assignment does not consume that step.

The researcher chooses direct assignments and any plan. The
[operating guide](../../docs/operating.md) follows an engagement from this
commission to its verified reading edition.
