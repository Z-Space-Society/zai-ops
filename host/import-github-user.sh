#!/usr/bin/env bash
#
# import-github-user.sh: create a sudo account on this Proxmox host from a
# GitHub user's public keys. Run as root, from the host's clone of zai-ops:
#
#   /root/zai-ops/host/import-github-user.sh jsayles
#   /root/zai-ops/host/import-github-user.sh jsayles bmann
#
# The host-side counterpart to add-github-user.yml, which reaches CT 100 and the
# inference nodes but never the host: Ansible has no inventory entry for it and
# CT 100 has no SSH path to it (ADR-0008). Same shape as that role: a same-named
# account in the sudo group, keys from https://github.com/<user>.keys, and a
# temporary password the user must change on first login.
#
# Import only. A re-run adds any keys that are new on GitHub and changes nothing
# else; a key deleted on GitHub stays here until someone removes it by hand.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "This script must run as root." >&2
  exit 1
fi
if [[ $# -lt 1 ]]; then
  echo "usage: import-github-user.sh <github-user> [<github-user>...]" >&2
  exit 64
fi

# Proxmox doesn't ship sudo, and the account is pointless without it.
if ! command -v sudo >/dev/null 2>&1; then
  DEBIAN_FRONTEND=noninteractive apt-get -qq -y install sudo
fi

failed=0
for user in "$@"; do
  # GitHub usernames are case-insensitive, Linux account names are not. Holding
  # to lowercase keeps the account name predictable, and the name is safe to put
  # in a URL and in chpasswd's input.
  if [[ ! "$user" =~ ^[a-z0-9][a-z0-9-]*$ ]]; then
    echo "$user: not a lowercase GitHub username; skipped" >&2
    failed=1
    continue
  fi

  # Fetch before touching the system. A typo'd username, or a GitHub account
  # with no keys, would otherwise leave a sudo account nobody can log in to.
  if ! keys=$(curl -fsSL "https://github.com/$user.keys") || [[ -z "$keys" ]]; then
    echo "$user: no public keys at https://github.com/$user.keys; skipped" >&2
    failed=1
    continue
  fi

  if ! id "$user" >/dev/null 2>&1; then
    useradd -m -s /bin/bash "$user"
    # A real temp password, not a locked account: over key auth, PAM still asks
    # for the current password before it accepts a new one. Expired at once, so
    # it only has to survive until the first login. printf is a builtin, so the
    # password never lands in a process's argv.
    password=$(openssl rand -base64 12)
    printf '%s:%s\n' "$user" "$password" | chpasswd
    chage -d 0 "$user"
    echo "$user: created. Temporary password: $password"
    echo "$user: share it out-of-band; it must be changed on first login."
  fi

  usermod -aG sudo "$user"

  # The account's own home, not /home/$user, so an existing account whose home
  # lives elsewhere still gets its keys in the right place.
  home=$(getent passwd "$user" | cut -d: -f6)
  auth_keys="$home/.ssh/authorized_keys"
  install -d "$home/.ssh"
  touch "$auth_keys"
  # A hand-edited file may lack a trailing newline, and appending to it would
  # glue the first new key onto the last old one.
  if [[ -s "$auth_keys" && -n "$(tail -c 1 "$auth_keys")" ]]; then
    echo >> "$auth_keys"
  fi

  added=0
  while IFS= read -r key; do
    if [[ -n "$key" ]] && ! grep -qxF "$key" "$auth_keys"; then
      printf '%s\n' "$key" >> "$auth_keys"
      added=$((added + 1))
    fi
  done <<< "$keys"

  # sshd refuses keys in a directory or file others can write to.
  chown "$user:" "$home/.ssh" "$auth_keys"
  chmod 700 "$home/.ssh"
  chmod 600 "$auth_keys"
  echo "$user: in the sudo group, $added new key(s) installed"
done

exit "$failed"
