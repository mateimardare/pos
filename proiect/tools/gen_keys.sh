#!/bin/sh
# Generates the SSH key pair used by the runner to reach the stations (NFR-SEC-01).
set -e
cd "$(dirname "$0")/.."
mkdir -p secrets
[ -f secrets/id_ed25519 ] || ssh-keygen -q -t ed25519 -N "" -C cluster-runner -f secrets/id_ed25519
echo "keys in ./secrets (never commit them)"
