# Berman development on a Mac

Lima hosts a Debian Linux environment for this Apple silicon Mac. The source checkout, rootless Podman storage, project checkouts and episode files live on the guest disk. The administrator provisions the guest. The unprivileged development login runs Git, Berman and the actor.

From the software checkout on the Mac:

```sh
limactl start --name=berman development/lima/berman.yaml
ssh -F "$HOME/.lima/berman/ssh.config" -o ControlMaster=no -o ControlPath=none -l berman-dev lima-berman
```

On later boots use `limactl start berman`. For guest administration use `limactl shell --workdir / berman`. In the development login:

```sh
git clone https://github.com/Protonk/berman.git "$HOME/berman"
podman info --format '{{.Host.Security.Rootless}}'
```

For commits still local to the Mac, transfer a Git bundle into the guest, clone it there and set the software repository as origin. The public source can be read without a token. The actor signs in to its own CLI in Linux.

## Push Berman commits from the guest

For an agent to push at the maintainer’s direction, use an expiring fine-grained token with `Protonk/berman` as the selected repository and **Contents** set to **Read and write**. Git can retrieve it from the development login’s unlocked keyring across SSH sessions. Other processes running as `berman-dev` can also use that unlocked credential. [GitHub token settings](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens)

### Configure this checkout once

Debian ships Git’s Secret Service helper as source. From the Mac, install its build dependencies in the guest.

```sh
limactl shell --workdir / berman sudo apt-get install -y --no-install-recommends gcc libc6-dev make pkg-config libsecret-1-dev
```

In the `berman-dev` login, build the helper and select it for this checkout’s Berman HTTPS URL. The empty first entry clears inherited helpers for that URL, and `useHttpPath` includes the repository path when looking up the token. [Git credentials](https://git-scm.com/docs/gitcredentials)

```bash
helper_build=$(mktemp -d)
cp /usr/share/doc/git/contrib/credential/libsecret/{Makefile,git-credential-libsecret.c} "$helper_build/"
make -C "$helper_build"
install -D -m 0755 "$helper_build/git-credential-libsecret" "$HOME/.local/libexec/git-credential-libsecret"

cd "$HOME/berman"
scope=credential.https://github.com/Protonk/berman.git
git config --local --replace-all "$scope.helper" ''
git config --local --add "$scope.helper" "$HOME/.local/libexec/git-credential-libsecret"
git config --local "$scope.useHttpPath" true
git config --local "$scope.username" Protonk
```

### Unlock and push

In a private `berman-dev` Bash terminal, check the login collection. This check leaves a stopped Secret Service asleep. If it reports `b false`, the keyring is already unlocked and you can go straight to Git.

```sh
busctl --user --auto-start=no get-property org.freedesktop.secrets \
  /org/freedesktop/secrets/collection/login org.freedesktop.Secret.Collection Locked
```

For a fresh guest login with no running service, unlock the keyring before running Git or `secret-tool`. On first use, choose a nonempty passphrase. Use the same passphrase for subsequent unlocks. [GNOME Keyring](https://manpages.debian.org/trixie/gnome-keyring/gnome-keyring-daemon.1.en.html)

```bash
IFS= read -rsp 'Guest keyring passphrase: ' ringpass; echo
if [ -n "$ringpass" ]; then
  printf '%s' "$ringpass" | gnome-keyring-daemon --unlock --components=secrets >/dev/null
fi
unset ringpass
```

Repeat the collection check and look for `b false` before entering a token. If Debian started its systemd daemon before the unlock command, the unlocked daemon can be waiting for the same bus name. Inspect the owners with `busctl --user call org.freedesktop.DBus /org/freedesktop/DBus org.freedesktop.DBus ListQueuedOwners s org.freedesktop.secrets`. If the systemd service owns the name, the passphrase-unlocked daemon is next, and the service has no session credentials in use, `systemctl --user stop gnome-keyring-daemon.service` lets the queued daemon take over. Check the collection again.

On first use, run `git -C "$HOME/berman" push --dry-run origin HEAD:main` from the private terminal. At Git’s password prompt, paste the token. A successful HTTPS authentication lets Git store it through the helper. An agent can check access from a separate guest session with the dry run below. When the maintainer requests a push to `main`, remove `--dry-run`. Git reads the token from the keyring for the push.

```sh
GIT_TERMINAL_PROMPT=0 git -C "$HOME/berman" push --dry-run origin HEAD:main
```

[Linux installation and first episode](../../README.md) · [Development checks](../../docs/development.md)
