# Berman

Berman is a tool for intermittent management of work in a software project.
The operator uses it to commission a bounded engagement in a project's Git 
checkout. A worker enters the project's ordinary development environment 
through Codex or Claude, does the assigned work and keeps whatever additional 
account the assignment asks for. 

The operator does not have to be continuously present. They arrange
a piece of work, an investigation or a period of participation when they
choose, and read afterwards what was asked, what the worker reported, what
was captured and what the record does not cover. Development outside
engagements continues independently: it needs no Berman command and is not
recorded.

## What does what

The software has three parts. The executor and the connector run on the
macOS host where the project is; the controller runs in a Linux virtual
machine, the guest. A study is what the guest keeps together: its inputs, its
engagements and their recordings ([words used here](docs/operating.md#words-used-here)).

| Part | Runs | Does |
| --- | --- | --- |
| Native executor | On macOS, beside the project | Prepares the worker's packet outside the checkout, launches the harness and stages what its hooks observe. |
| Connector | In the operator's macOS terminal, over SSH | Carries each command to the guest and the collected evidence after it. |
| Controller and custody | In a Linux guest | Holds the study's recipes and plans, issues each assignment, records the commissioner's ending, preserves a verifiable recording and publishes a reading edition. |

The worker receives the assignment and its instructions. Plans and earlier
recordings are kept in the guest and are not sent to the host.

## Start with the cheatsheet

```sh
berman cheatsheet
```

The cheatsheet reads the study's recorded situation and prints the commands
that apply now, written out so they can be pasted. Use it to begin an
engagement, to find out where one stands, and to pick up after an
interruption. It changes nothing. An excerpt:

```text
Engagements
  run-001 (example, claude): prepared
    Packet: /Users/me/.local/share/berman/spool/operations/…/packet
    After the packet is reviewed, the commissioner starts the first sitting from an ordinary terminal. Repeating this never starts a second process.
      berman-research launch run-001
```

`berman cheatsheet --config FILE` selects a study other than the default.
With no study connected it says how to check the installation and connect one.
Its first line names the version that answered. A workstation can hold more
than one installation, and the cheatsheet prints a full path instead of a bare
`berman` whenever `PATH` would lead to a different one.

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
to a study. [Install the guest](docs/guest-deployment.md) ·
[Example study](examples/minimal-study/README.md)

## Read more

| For | Read |
| --- | --- |
| Running an engagement from commission to closeout, the words Berman uses, and study inputs | [Operating a study](docs/operating.md) |
| What is recorded, what a recording establishes and does not, and reading editions | [Recordings and editions](docs/custody.md) |
| Packets, hooks, launch, staging and native qualification | [Native executor](docs/executor.md) |
| What the software cannot do or has not established | [Current limits](docs/limitations.md) |
| Changing Berman, its checks and what each one establishes | [Developing Berman](docs/development.md) |

`berman verify` checks the installed software. It says nothing about a
harness on this machine: that takes a
[native qualification](docs/executor.md#qualification-evidence).
[MIT License](LICENSE)
