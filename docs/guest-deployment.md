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

## Study and executor bindings

Create `~/research/STUDY` as a private directory in the guest. Select a new UUID
once for a new collection. For a qualification collection the initial files are

```text
collection.json                         {"schema":"berman.collection/1","id":"<UUID>","name":"Qualification"}
connections/example/connection.json     {"schema":"berman.connection/1","id":"example","documents":["README.md"],"default_recipe":"baseline"}
recipes/baseline/recipe.json
recipes/baseline/INSTRUCTIONS.md
```

Copy the two recipe files from `examples/minimal-study/recipes/baseline`.
Keep the matching collection ID and study name in the operator's connection
settings on macOS, with host `lima-berman-research` and `ssh_config` pointing
to the generated file. The connector invokes
`.local/berman/bin/python -I -m berman.controller --study STUDY` in the guest
login directory. Its versioned commands carry collection and run identity.
Machine paths and VM names belong to installation settings. The guest
controller alone allocates run numbers and records ending declarations.
Only designated assignments and execution requirements travel to the host.
Keep analysis outside the worker packet and the connected checkout.

A stopped guest interrupts commissioning and transfers. Local hooks can keep
staging evidence on macOS. After `limactl start berman-research`, retry the same
operation using its existing identity and spool. Keep the native staging until
the guest has accepted the inventory and verified its hashes. A transfer
receipt describes delivered bytes. Capture coverage and the researcher's
declaration each have their own evidence.

## Continuing a collection

Keep the collection UUID and set `reserved_through` in `collection.json` to
the highest run number previously allocated, including cancellations and gaps.
The controller also checks its ledger and occupied custody numbers. A fresh
study starts with its own UUID. [Guest recording custody](custody.md)

The VM and its SSH configuration remain visible to a participant with host
access. Ordinary project searches can stay within the checkout while a
cooperating participant can still recognize fieldwork or inspect the VM.
Record a material exposure with its circumstances and the researcher's
response. Guest storage on this machine shares the machine's failure domain.
