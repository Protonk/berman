#!/bin/sh
# Lima runs this as the administrator when the instance boots.
set -eu
export DEBIAN_FRONTEND=noninteractive
if ! dpkg-query -W -f=' ${db:Status-Status}\n' podman git ca-certificates curl gcc libc6-dev make perl uidmap passt slirp4netns dbus-user-session gnome-keyring libsecret-tools ripgrep | awk 'BEGIN {ok=1} $1!="installed"{ok=0} END{exit !(ok && NR==15)}'; then
    apt-get update
    apt-get install -y --no-install-recommends podman git ca-certificates curl gcc libc6-dev make perl uidmap passt slirp4netns dbus-user-session gnome-keyring libsecret-tools ripgrep
fi
id berman-dev >/dev/null 2>&1 || useradd --create-home --user-group --shell /bin/bash berman-dev
loginctl enable-linger berman-dev
install -d -m 0755 /etc/ssh/berman
admin_directory=$(getent passwd berman-admin | cut -d: -f6)
install -m 0644 "$admin_directory/.ssh/authorized_keys" /etc/ssh/berman/development.pub
cat > /etc/ssh/sshd_config.d/60-berman-development.conf <<'SSH'
Match User berman-dev
    AuthorizedKeysFile /etc/ssh/berman/development.pub
Match all
SSH
sshd -t
systemctl reload ssh
