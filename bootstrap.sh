#!/usr/bin/env bash
#
# bootstrap.sh — Create the ZAI Ansible control node (defaults to CT 100).
# Run on a freshly-flashed Proxmox host as root (the default first-boot login).
# Pass a container ID to override the default.
#
# This is the one host-level script in the repo. Everything after the
# control node exists is driven by Ansible from inside it.
#
# bash bootstrap.sh		#  Will create CT 100
# bash bootstrap.sh 199	#  Will create CT 199
set -euo pipefail

# --- Must be root ---
if [[ $EUID -ne 0 ]]; then
  echo "This script must run as root." >&2
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
# locale: unsupported locale setting") until en_US.UTF-8 exists, so this
# cannot be deferred to an Ansible play.
#
# Register the locale in /etc/locale.gen and generate it from there — NOT a
# bare localedef. A glibc/locales upgrade (e.g. the full-upgrade in the
# control_node role) re-runs locale-gen from this file; anything not listed
# here gets wiped. Set LANG only — no LANGUAGE/LC_ALL.
pct exec "$CTID" -- bash -c "
  apt-get update
  sed -i 's/^# *en_US.UTF-8 UTF-8/en_US.UTF-8 UTF-8/' /etc/locale.gen
  locale-gen
  grep -q '^LANG=' /etc/environment || echo 'LANG=en_US.UTF-8' >> /etc/environment
  apt-get install -y ansible git
  [ -d /opt/zai-ops ] || git clone $REPO_URL /opt/zai-ops
"

# --- Mint the Proxmox API token + Ansible Vault for the control node ---
# This is host-side because it must be: an API token can only be created with
# pveum (a host-only binary) or the API, and calling the API would already
# require credentials. So the first credential is minted here, then handed to
# CT 100 as part of equipping it — there is still only one host run.
PROVISIONED=0
VAULT_PASS=""
if ! pct exec "$CTID" -- test -f /opt/zai-ops/ansible/group_vars/all/vault.yml; then
  ROLE=ZaiProvision
  TOKEN_USER=ansible@pve
  TOKEN_ID=provision
  HOST_IP=$(hostname -I | awk '{print $1}')

  # Dedicated role — starting privilege set for LXC lifecycle + storage.
  # Widen with `pveum role modify` if verify or CT creation hits a 403.
  pveum role add "$ROLE" --privs \
    "VM.Allocate,VM.Clone,VM.Config.Disk,VM.Config.CPU,VM.Config.Memory,VM.Config.Network,VM.Config.Options,VM.PowerMgmt,VM.Audit,Datastore.AllocateSpace,Datastore.AllocateTemplate,Datastore.Audit,Sys.Audit,Sys.Modify,SDN.Use,Pool.Allocate" 2>/dev/null || true
  pveum user add "$TOKEN_USER" 2>/dev/null || true
  pveum aclmod / --users "$TOKEN_USER" --roles "$ROLE" --propagate 1

  # Re-mint cleanly. The pveum user/token live on the host and survive a
  # `pct destroy`, so on a rebuild the token already exists; drop it first.
  pveum user token remove "$TOKEN_USER" "$TOKEN_ID" 2>/dev/null || true

  # privsep 0: the token inherits the user's privileges (the role above).
  TOKEN_OUT=$(pveum user token add "$TOKEN_USER" "$TOKEN_ID" --privsep 0 --output-format json)
  TOKEN_SECRET=$(printf '%s' "$TOKEN_OUT" | sed -n 's/.*"value":"\([^"]*\)".*/\1/p')

  # Generate the vault password; stored on the CT so Ansible auto-decrypts.
  VAULT_PASS=$(openssl rand -base64 24)

  pct exec "$CTID" -- bash -c "umask 077; printf '%s\n' '$VAULT_PASS' > /root/.vault_pass"
  pct exec "$CTID" -- bash -c "
    set -e
    umask 077
    cd /opt/zai-ops/ansible
    mkdir -p group_vars/all
    # Write plaintext to a temp file and encrypt it INTO the final path with
    # --output, so vault.yml only exists if encryption succeeded (a half-done
    # plaintext file would otherwise poison the guard above on re-run).
    # No --vault-password-file here: ansible.cfg already sets vault_password_file,
    # and passing it again creates two 'default' vault-ids that ambiguate encrypt.
    cat > /tmp/zai-vault.yml <<EOF
proxmox_api_host: $HOST_IP
proxmox_api_user: $TOKEN_USER
proxmox_api_token_id: $TOKEN_ID
proxmox_api_token_secret: $TOKEN_SECRET
EOF
    ansible-vault encrypt /tmp/zai-vault.yml --output group_vars/all/vault.yml
    rm -f /tmp/zai-vault.yml
  "
  PROVISIONED=1
fi

echo
echo "CT $CTID ($HOSTNAME) ready."
echo "Next, configure the control node with Ansible:"
echo
echo "  pct enter $CTID"
echo "  cd /opt/zai-ops/ansible"
echo "  ansible-playbook site.yml"

# --- Vault password — printed LAST so it isn't scrolled away ---
if [[ "$PROVISIONED" -eq 1 ]]; then
  echo
  echo "=================================================================="
  echo " VAULT PASSWORD — back this up off-box (e.g. a password manager)."
  echo " Stored on $HOSTNAME at /root/.vault_pass so Ansible auto-decrypts"
  echo " (recoverable there as root). Needed to view/edit secrets:"
  echo "   ansible-vault edit group_vars/all/vault.yml"
  echo
  echo "     $VAULT_PASS"
  echo "=================================================================="
fi
