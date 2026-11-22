# Berman development on a Mac

Lima hosts a Debian Linux environment for this Apple silicon Mac. The source checkout, rootless Podman storage, project checkouts and episode files live on the guest disk. The administrator provisions the guest. The unprivileged development login runs Git, Berman and the actor.

From the software checkout on the Mac:

```sh
limactl start --name=berman apparatus/lima/berman.yaml
ssh -F "$HOME/.lima/berman/ssh.config" -o ControlMaster=no -o ControlPath=none -l berman-dev lima-berman
```

On later boots use `limactl start berman`. For guest administration use `limactl shell --workdir / berman`. In the development login:

```sh
git clone https://github.com/Protonk/berman.git "$HOME/berman"
podman info --format '{{.Host.Security.Rootless}}'
```

For commits still local to the Mac, transfer a Git bundle into the guest, clone it there and set the software repository as origin. The public source can be read without a token. When write access is wanted, use a fine-grained token scoped to the software repository. Enter it in the development login’s private terminal after unlocking the user’s credential store. Check that GitHub CLI reports keyring storage before relying on it. The actor’s own sign-in takes place in the Linux environment.

[Linux installation and first episode](../../README.md) · [Development checks](../../docs/development.md)
