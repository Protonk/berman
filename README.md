# Berman

Berman supports intermittent participant observation of software development.
A researcher commissions an engagement. A designated fieldworker uses Codex or
Claude to do ordinary work in the project's checkout and keeps the notes the
assignment asks for. Development between engagements continues independently.

The study lives in a Linux guest. Its controller holds the research plans,
commissions assignments and preserves the evidence. A macOS executor prepares
the worker's packet outside the checkout, launches the harness and stages its
observations. The worker gets the assignment and its recording instructions.
Plans, prior recordings and analysis belong on the research side.

## Run an engagement

Use Python 3.14 and Git. To install the operator tools from this checkout on
macOS and check the installation, run

```sh
python3.14 -m venv .venv
. .venv/bin/activate
python -m pip install .
berman verify --software
```

Install the same build in the Linux guest and bind a macOS checkout and harness
to a study connection. The operator selects the study through its connector
configuration. Use `berman-research --config FILE` before the command when
selecting a study other than the default. [Guest setup](docs/guest-deployment.md) · [Example study](examples/minimal-study/README.md)

From a macOS terminal, commission the work and prepare the run number returned
by the controller. This example uses the first run of a new study.

```sh
berman-research commission --request first-account --connection example --harness claude --recipe baseline
berman-research prepare run-001
```

The researcher reviews the packet and launches the fieldworker from an ordinary
terminal. An engagement can span several sittings.

```sh
berman-research launch run-001
```

When the researcher declares the engagement ended, record their reason.
Closeout transfers the staged material, preserves a recording, publishes a
reading edition and verifies both in the guest. An interrupted closeout can
resume from its receipts.

```sh
berman-research closeout run-001 --reason 'The researcher ended fieldwork after the project review'
```

## Read the fieldwork

The recording holds the returned files, their hashes, the researcher's ending
and any gaps in collection. The reading edition brings the assignment, notes
and session material together for analysis. Both can be verified from copies.
This repository holds the reusable software. Each study keeps its fieldwork
and analytical work in its own research environment. [Recordings and editions](docs/custody.md)

Use `berman-research verify run-001` to recheck the recording and edition in
the example study. The operator guide covers status, resuming a sitting and
closeout. [Operator guide](docs/connector.md) · [Native execution](docs/executor.md) · [Current limits](docs/limitations.md)

For development, use the locked Python environment and the synthetic test
studies. [Development guide](docs/development.md) · [MIT License](LICENSE)
