# Install the research guest

The researcher operates the controller from a macOS terminal. The Linux account
holds the study, recordings, editions and a separate analysis repository. The
native executor needs its assignment, a checkout and a spool outside the
checkout. Keep plans, analysis and prior recordings off the executor's ordinary
search path. Keep harness credentials and native source discovery on macOS.

## Provision once

On Apple silicon with Lima installed, start a small headless guest with
guest-owned storage. Use the existing instance on subsequent starts.

```sh
limactl start -y --name=berman-research --vm-type=vz --mount-none \
  --containerd=none --memory=2 --cpus=2 --disk=24 template:ubuntu-26.04
limactl list berman-research
ssh_config="${LIMA_HOME:-$HOME/.lima}/berman-research/ssh.config"
ssh -F "$ssh_config" lima-berman-research \
  'sudo apt-get update && sudo apt-get install -y python3.14-venv'
ssh -F "$ssh_config" lima-berman-research \
  'set -e; python3.14 --version; findmnt -T "$HOME"; findmnt -t virtiofs,9p,fuse.sshfs || test "$?" -eq 1'
```

Confirm Python 3.14, a guest block device for the home directory and no host
share. The generated SSH configuration supplies the local port, identity,
host-key file and connection settings. Keep using that file after a guest
restart. OpenSSH can use it directly for a command on stdin and a receipt on
stdout. The connector uses the system OpenSSH client and Lima's generated
configuration. [Lima SSH](https://lima-vm.io/docs/usage/ssh/)

## Install a chosen wheel

From the core checkout or source distribution, pass the wheel selected for
this guest to the installer.

```sh
scripts/install-research-wheel /path/to/berman-VERSION-py3-none-any.whl
ssh -F "$ssh_config" lima-berman-research \
  '~/.local/berman/bin/python -I -m berman.controller --help'
```

The installer compares SHA-256 across the transfer, installs dependencies,
reinstalls the chosen wheel even for a same-version development build and
checks the environment. Coordinate replacements with anyone exercising this
guest. A second venv can verify older packages while a qualification session
uses the active application. Provisioning and Python package installation
require access to the distribution or package mirrors. Research evidence
travels over the local SSH connection.

## The study in the guest

Create `~/research/STUDY` as a private directory in the guest. Select a new UUID
once for a new collection. For a qualification collection the initial files are

```text
collection.json                         {"schema":"berman.collection/1","id":"<UUID>","name":"Qualification"}
connections/example/connection.json     {"schema":"berman.connection/1","id":"example","documents":["README.md"],"default_recipe":"baseline"}
recipes/baseline/recipe.json
recipes/baseline/INSTRUCTIONS.md
```

Copy the two recipe files from `examples/minimal-study/recipes/baseline`.
[Study inputs](operating.md#study-inputs) describes connections, recipes and
plans. The guest controller alone allocates run numbers and records ending
declarations. Only designated assignments and execution requirements travel to
the host. Keep analysis outside the worker packet and the connected checkout.

## Connector settings on macOS

Install the same build in a macOS Python 3.14 environment. The operator's
settings name the guest, the study and, for each connection, the checkout and
harness on this host. The default file is `~/.config/berman/connector.json`;
`--config FILE` or `BERMAN_CONNECTOR` selects another, which is how one
workstation keeps several studies apart.

```json
{
  "host": "lima-berman-research",
  "ssh_config": "~/.lima/berman-research/ssh.config",
  "study": "example-study",
  "collection_id": "29525f31-3e9d-4c3d-9078-cedd71216be9",
  "spool": "~/.local/share/berman/spool",
  "bindings": {
    "example": {
      "codex": {"project": "/Users/me/project", "home": "~/.codex", "executable": "~/.local/bin/codex"},
      "claude": {"project": "/Users/me/project", "home": "~/.claude", "executable": "~/.local/bin/claude"}
    }
  }
}
```

Replace the UUID with the one in the guest's `collection.json`. `spool` is the
executor's staging directory and must lie outside the checkout and the harness
home. Each project must be a Git checkout with a commit. `home` and
`executable` identify the ordinary native installation. A binding may also set
a harness `policy` and a `model`
([binding fields](executor.md#preparing-a-checkout)).

Codex needs the executor's hook definition for this spool installed in its
home and reviewed in the actual CLI before the first launch
([hooks and launch](executor.md#hooks-and-launch)). Claude receives its capture
settings at each launch. `berman cheatsheet --config FILE` then shows the
commission command for every binding.

A new installation has no native evidence of its own. Run one designated
one-turn assignment and evaluate it before relying on capture
([qualification](executor.md#qualification-evidence)).

A stopped guest interrupts commissioning and transfers. Local hooks can keep
staging evidence on macOS. After `limactl start berman-research`, retry the same
operation using its existing identity and spool. Keep the native staging until
the guest has accepted the inventory and verified its hashes. A transfer
receipt describes delivered bytes. Capture coverage and the researcher's
declaration each have their own evidence.

## Continuing a collection

Keep the collection UUID and set `reserved_through` in `collection.json` to
the highest run number previously allocated, including cancellations and gaps.
The controller also checks its ledger and occupied custody numbers. When a plan
continues from the earlier study, account for the steps already consumed or
start a separately identified plan. A fresh study starts with its own UUID.
[Importing an earlier recording](custody.md#importing-a-guest-recording)

The VM and its SSH configuration remain visible to a participant with host
access. Ordinary project searches can stay within the checkout while a
cooperating participant can still recognize fieldwork or inspect the VM.
Record a material exposure with its circumstances and the researcher's
response. Guest storage on this machine shares the machine's failure domain.
