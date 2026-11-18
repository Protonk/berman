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
stdout. AsyncSSH offers command channels and supports a subset of OpenSSH
configuration. A single OpenSSH client invocation here also uses the same
host-key and connection settings as the operator. [Lima SSH](https://lima-vm.io/docs/usage/ssh/) · [AsyncSSH](https://asyncssh.readthedocs.io/en/latest/)

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

## Historical studies

1. Retain the host runtime and verify the source on the host. A study with
   ongoing work needs a researcher decision about that work before authority
   can move. Create a distinct guest collection for qualification.
2. Copy each existing recording/2 ZIP unchanged with its independently recorded
   SHA-256. For an older preserved run, the released 0.26.3
   `recording export --historical --run RUN --output NEW_PATH` reads the
   original inventory and emits a portable companion. Keep the old packages,
   journals, inventories, research declarations and edition ZIPs.
3. Transfer the study originals as a separate archive that retains subsecond
   mtimes, modes and symlink targets, including metadata in any nested Git
   trees. Transfer the analysis repository separately. Git checkout rewrites
   mtimes. Linux ext4 also exposes ordinary symlinks as mode 0777. A PAX
   archive preserves a macOS source mode even when the working copy differs.
   Compare SHA-256 at both ends and inspect archive contents independently.
4. In a guest directory for historical readings, copy the original
   `collection.json`. The custody API accepts the portable recording under
   its original UUID and run number. For example, with absolute guest paths
   `root`, `source` and the observed `digest`:

```python
from berman import custody
from berman.archive import package

receipt = custody.import_recording(root, source, expected_sha256=digest)
custody.verify_recording(root, receipt)
edition = custody.publish_edition(root, receipt)
package.verify_edition(root / edition['archive_path'], edition['archive_sha256'])
```

A researcher can keep this historical directory and continue to read it
without starting a controller there. Before a future cutover reconcile
every previously allocated number, including cancelled or unpublished work,
set the new controller's allocation floor in `collection.json` using
`reserved_through`, retire the old writer and record which installation
owns the next commission. Keep earlier missing sources and coverage
limitations attached to their occurrences. Qualification under a new UUID
does not transfer authority for an existing study.

The VM and its SSH configuration remain visible to a participant with host
access. Ordinary project searches can stay within the checkout while a
cooperating participant can still recognize fieldwork or inspect the VM.
Record a material exposure with its circumstances and the researcher's
response. Guest storage on this machine shares the machine's failure domain.
