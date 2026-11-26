# The Linux build on a Mac

Lima hosts a Debian Linux environment on this Apple silicon Mac for building and testing Berman’s Linux program. Berman’s checkout, commits and Git credentials stay on the Mac. The guest receives a source archive of one commit at a time and keeps no Berman clone and no repository credentials. Project checkouts, the actor CLIs and a producer’s library for native sittings live on the guest disk. The administrator provisions the guest. The unprivileged development login builds, runs Berman and runs the actor.

From the software checkout on the Mac:

```sh
limactl start --name=berman development/lima/berman.yaml
ssh -F "$HOME/.lima/berman/ssh.config" -o ControlMaster=no -o ControlPath=none -l berman-dev lima-berman
```

On later boots use `limactl start berman`. For guest administration use `limactl shell --workdir / berman`. In the development login, install rustup once. The toolchain itself is installed on first use from the `rust-toolchain.toml` in each archive.

```sh
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal --default-toolchain none
podman info --format '{{.Host.Security.Rootless}}'
```

## Check a revision

From the checkout on the Mac, `development/lima/check` archives a commit, passes the archive and the commit ID to a temporary directory in the guest, and runs the formatting, lint and test checks there. `--container` adds the packaging check in rootless Podman. The commit must exist in the Mac’s checkout. It does not need to be pushed.

```sh
development/lima/check
development/lima/check --container COMMIT
```

The guest keeps compiled dependencies between checks under `~/.cache/berman/target`. The archive and everything built from it are removed when the check ends. The guest’s `/tmp` is held in memory, so the check gives the suite a temporary directory on disk.

## Native sittings in the guest

The actor signs in to its own CLI in Linux. Over SSH, `codex login --device-auth` prints a code to enter in the Mac’s browser. To try a build with real actors, install it in the development login from its release archive or from an unpacked source archive, and create the login’s library with a key file, since the guest’s keyring is locked after a reboot. [Actor setup](../../docs/operating.md#set-up-the-actor)

```sh
berman library init --key file
```

[Installation and first episode](../../README.md) · [Development checks](../../docs/development.md)
