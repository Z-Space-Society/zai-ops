#!/usr/bin/env bash
#
# bootstrap.sh — Create the ZAI Ansible control node (defaults to CT 100).
# Run on a freshly-flashed Proxmox host, as root (use sudo).
# Pass a container ID to override the default.
#
# This is the one host-level script in the repo. Everything after the
# control node exists is driven by Ansible from inside it.
#
# sudo ./bootstrap.sh		#  Will create CT 100
# sudo ./bootstrap.sh 199	#  Will create CT 199
set -euo pipefail

# --- Must be root (sudo) ---
if [[ $EUID -ne 0 ]]; then
  echo "This script must run as root. Re-run with: sudo $0 $*" >&2
  exit 1
fi

# --- Fix Proxmox apt repos (disable enterprise, add no-subscription) ---
echo 'Enabled: false' | tee -a /etc/apt/sources.list.d/pve-enterprise.sources
echo 'Enabled: false' | tee -a /etc/apt/sources.list.d/ceph.sources
if [ ! -f /etc/apt/sources.list.d/proxmox.sources ]; then
  cat > /etc/apt/sources.list.d/proxmox.sources <<'EOF'
Types: deb
URIs: http://download.proxmox.com/debian/pve
Suites: trixie
Components: pve-no-subscription
Signed-By: /usr/share/keyrings/proxmox-archive-keyring.gpg
EOF
fi
apt-get update
apt-get -y full-upgrade

# --- Config ---
CTID="${1:-100}"
HOSTNAME=ansible-control
TEMPLATE=debian-13-standard_13.1-2_amd64.tar.zst   # confirm: pveam available | grep debian-13
TEMPLATE_STORAGE=local
ROOTFS_STORAGE=local-lvm
BRIDGE=vmbr0
REPO_URL=https://github.com/Z-Space-Society/zai-ops.git

echo "Using CT ID: $CTID"

# --- Ensure the template is downloaded ---
if ! pveam list "$TEMPLATE_STORAGE" | grep -q "$TEMPLATE"; then
  pveam update
  pveam download "$TEMPLATE_STORAGE" "$TEMPLATE"
fi

# --- Create the container (idempotent: skip if it exists) ---
if pct status "$CTID" &>/dev/null; then
  echo "CT $CTID already exists; skipping create."
else
  pct create "$CTID" "$TEMPLATE_STORAGE:vztmpl/$TEMPLATE" \
    --hostname "$HOSTNAME" --cores 2 --memory 2048 --swap 512 \
    --rootfs "$ROOTFS_STORAGE:8" \
    --net0 name=eth0,bridge="$BRIDGE",ip=dhcp \
    --unprivileged 1 --onboot 1
  pct start "$CTID"
fi

# --- Wait for the container network to come up ---
sleep 5

# --- Provision the control node: locale, Ansible, and the repo ---
# The locale MUST be fixed here, before Ansible is ever run. On a fresh
# container Ansible refuses to start ("could not initialize the preferred
# locale: unsupported locale setting") until en_US.UTF-8 is compiled, so this
# cannot be deferred to an Ansible play. localedef is the reliable fix
# (locale-gen / dpkg-reconfigure are not). Set LANG only — no LANGUAGE/LC_ALL.
pct exec "$CTID" -- bash -c "
  apt-get update
  localedef -i en_US -f UTF-8 en_US.UTF-8
  grep -q '^LANG=' /etc/environment || echo 'LANG=en_US.UTF-8' >> /etc/environment
  apt-get install -y ansible git
  [ -d /opt/zai-ops ] || git clone $REPO_URL /opt/zai-ops
"

echo "CT $CTID ($HOSTNAME) ready."
echo "Next: pct enter $CTID, then run the Ansible bootstrap from /opt/zai-ops."
