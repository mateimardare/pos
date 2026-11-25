#!/bin/sh
# The public key is mounted read-only at /keys/id_ed25519.pub
set -e
install -o maintenance -g maintenance -m 600 /keys/id_ed25519.pub /home/maintenance/.ssh/authorized_keys
ssh-keygen -A >/dev/null
exec /usr/sbin/sshd -D -e
