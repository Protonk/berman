# Berman

Berman supports intermittent participant observation of software development.
A researcher commissions an engagement. A designated fieldworker uses Codex or
Claude to do ordinary work in the project's checkout and keeps the notes the
assignment asks for. Development between engagements continues independently
and is not recorded.

It has three parts.

| Part | Runs | Does |
| --- | --- | --- |
| Native executor | On macOS, beside the project | Prepares the worker's packet outside the checkout, launches the harness and stages what its hooks observe. |
| Connector | In the operator's macOS terminal, over SSH | Carries each command to the research guest and the collected evidence after it. |
| Controller and custody | In a Linux guest | Holds the study's recipes and plans, commissions assignments, records the researcher's ending, preserves a verifiable recording and publishes a reading edition. |

The worker receives the assignment and its recording instructions. Plans,
earlier recordings and analysis stay on the research side.

## Start with the cheatsheet

```sh
berman cheatsheet
```

The cheatsheet reads the study's recorded situation and prints the commands
that apply now, written out so they can be pasted. Use it to begin an
engagement, to find out where one stands, and to pick up after an
interruption. It changes nothing.

```text
Engagements
  run-001 (example, claude): prepared
    Packet: /Users/me/.local/share/berman/spool/operations/…/packet
    After the packet is reviewed, the researcher starts the first sitting from an ordinary terminal.
      berman-research launch run-001
```

`berman cheatsheet --config FILE` selects a study other than the default.
With no study connected it says how to check the installation and connect one.

## Install

Use Python 3.14 and Git. From this checkout on macOS:

```sh
python3.14 -m venv .venv
. .venv/bin/activate
python -m pip install .
berman verify
berman cheatsheet
```

Install the same build in the Linux guest and bind a macOS checkout and harness
to a study. [Install the research guest](docs/guest-deployment.md) ·
[Example study](examples/minimal-study/README.md)

## Read more

| For | Read |
| --- | --- |
| Running an engagement from commission to closeout, and study inputs | [Operating a study](docs/operating.md) |
| What a recording establishes, and reading editions | [Recordings and editions](docs/custody.md) |
| Packets, hooks, launch, staging and native qualification | [Native executor](docs/executor.md) |
| What the software cannot do or has not established | [Current limits](docs/limitations.md) |
| Changing Berman, its checks and what each one establishes | [Developing Berman](docs/development.md) |

`berman verify` checks the installed software. It says nothing about a
harness on this machine: that takes a
[native qualification](docs/executor.md#qualification-evidence).
[MIT License](LICENSE)
