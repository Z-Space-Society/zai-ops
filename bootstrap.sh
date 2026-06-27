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

# --- Pretty output ---------------------------------------------------------
# Phase banners bracket the noisy package/clone output so it's always clear
# which step is running. Package managers are quieted (see $APT below); the
# "this can take a few minutes" notes cover the resulting silent stretches.
# Colors degrade to plain text when stdout isn't a terminal.
if [ -t 1 ] && command -v tput >/dev/null 2>&1; then
  BOLD=$(tput bold); CYAN=$(tput setaf 6); GREEN=$(tput setaf 2)
  YELLOW=$(tput setaf 3); RED=$(tput setaf 1); RESET=$(tput sgr0)
else
  BOLD=""; CYAN=""; GREEN=""; YELLOW=""; RED=""; RESET=""
fi
STEP=0
step()    { STEP=$((STEP + 1)); printf '\n%s==> [%d] %s%s\n' "$BOLD$CYAN" "$STEP" "$*" "$RESET"; }
info()    { printf '    %s\n' "$*"; }
done_ok() { printf '%s    ✓ %s%s\n' "$GREEN" "$*" "$RESET"; }

# Quiet, non-interactive apt. -qq silences routine chatter but still prints
# errors, and combined with set -e a real failure still aborts loudly.
export DEBIAN_FRONTEND=noninteractive
APT="apt-get -qq -y"

# --- Must be root ---
if [[ $EUID -ne 0 ]]; then
  printf '%sThis script must run as root.%s\n' "$RED" "$RESET" >&2
  exit 1
fi

# --- Fix Proxmox apt repos (disable enterprise, add no-subscription) ---
step "Configuring Proxmox apt repositories"
# Append silently (>> rather than `tee -a`, which also echoed to the console).
# NOTE: this still appends on every run — see security review Issue 1 for the
# deb822-safe idempotency fix (tracked separately).
echo 'Enabled: false' >> /etc/apt/sources.list.d/pve-enterprise.sources
echo 'Enabled: false' >> /etc/apt/sources.list.d/ceph.sources
if [ ! -f /etc/apt/sources.list.d/proxmox.sources ]; then
  cat > /etc/apt/sources.list.d/proxmox.sources <<'EOF'
Types: deb
URIs: http://download.proxmox.com/debian/pve
Suites: trixie
Components: pve-no-subscription
Signed-By: /usr/share/keyrings/proxmox-archive-keyring.gpg
EOF
fi
$APT update
done_ok "repositories configured"

step "Upgrading host packages"
info "this can take a minute…"
$APT full-upgrade
done_ok "host up to date"

# --- Suppress the "No valid subscription" web-UI nag -------------------------
# The popup is a client-side check in proxmox-widget-toolkit; on the
# no-subscription repo it's pure noise. We patch the toolkit's checked_command
# to run the original command and return before the dialog fires, and install a
# dpkg post-invoke hook so the patch survives every upgrade that ships a fresh
# proxmoxlib.js (which would otherwise restore the nag — see Known gotchas).
# Both the patcher and the hook key off a marker comment: absent on a
# freshly-shipped file (so the hook re-patches), present on an already-patched
# one (so re-runs are no-ops). pveproxy is restarted only when a patch is
# actually applied, so firing on every apt run is cheap.
step "Suppressing the subscription nag"
cat > /usr/local/sbin/pve-no-nag <<'EOF'
#!/bin/sh
# Managed by zai-ops (bootstrap.sh). Re-applied after every apt run via
# /etc/apt/apt.conf.d/00-zai-no-nag. Idempotent via the marker comment.
JS=/usr/share/javascript/proxmox-widget-toolkit/proxmoxlib.js
[ -f "$JS" ] || exit 0
grep -q 'zai-ops: nag suppressed' "$JS" && exit 0
# PVE 9 renders this as `function (orig_cmd)` (with a space), PVE 8 without it;
# the optional-space match + \1 backreference patches either without hardcoding.
sed -i -E "s|(checked_command: function ?\(orig_cmd\) \{)|\1\n\t    orig_cmd(); return; // zai-ops: nag suppressed|" "$JS"
systemctl restart pveproxy.service 2>/dev/null || true
EOF
chmod 0755 /usr/local/sbin/pve-no-nag
cat > /etc/apt/apt.conf.d/00-zai-no-nag <<'EOF'
// Managed by zai-ops (bootstrap.sh): re-suppress the subscription nag after any
// package operation that may have replaced proxmoxlib.js.
DPkg::Post-Invoke { "/usr/local/sbin/pve-no-nag || true"; };
EOF
/usr/local/sbin/pve-no-nag
done_ok "subscription nag suppressed (survives upgrades)"

# --- Internal network: vmbr1 (no uplink) + NAT for outbound ---
# The service containers (102-104) live only on this isolated bridge with no
# LAN NIC, so the host masquerades their traffic out via vmbr0 — otherwise
# their first `apt-get install` would stall with no route to the internet.
# The masquerade rule rides on the vmbr1 stanza as post-up/post-down so it is
# both reboot-safe (re-applied on ifup) and rebuild-safe (written here).
step "Creating internal network (vmbr1 + NAT)"
INTERNAL_NET=10.1.1.0/24
if ! grep -q '^iface vmbr1 ' /etc/network/interfaces; then
  cat >> /etc/network/interfaces <<EOF

auto vmbr1
iface vmbr1 inet static
	address 10.1.1.1/24
	bridge-ports none
	bridge-stp off
	bridge-fd 0
	post-up   iptables -t nat -A POSTROUTING -s $INTERNAL_NET -o vmbr0 -j MASQUERADE
	post-down iptables -t nat -D POSTROUTING -s $INTERNAL_NET -o vmbr0 -j MASQUERADE
EOF
  ifreload -a
  done_ok "vmbr1 up (host 10.1.1.1, NAT out via vmbr0)"
else
  info "vmbr1 already present; skipping"
fi

# Enable IPv4 forwarding persistently so the masquerade above actually routes.
if [ ! -f /etc/sysctl.d/99-zai-forward.conf ]; then
  echo 'net.ipv4.ip_forward=1' > /etc/sysctl.d/99-zai-forward.conf
  sysctl -q -p /etc/sysctl.d/99-zai-forward.conf
  done_ok "IPv4 forwarding enabled"
fi

# --- Config ---
CTID="${1:-100}"
HOSTNAME=ansible-control
TEMPLATE=debian-13-standard_13.1-2_amd64.tar.zst   # confirm: pveam available | grep debian-13
TEMPLATE_STORAGE=local
ROOTFS_STORAGE=local-lvm
BRIDGE=vmbr0
REPO_URL=https://github.com/Z-Space-Society/zai-ops.git

# --- Ensure the template is downloaded ---
step "Preparing container template"
if ! pveam list "$TEMPLATE_STORAGE" | grep -q "$TEMPLATE"; then
  info "downloading $TEMPLATE …"
  pveam update >/dev/null
  pveam download "$TEMPLATE_STORAGE" "$TEMPLATE"
else
  info "template already present"
fi
done_ok "template ready"

# --- Create the container (idempotent: skip if it exists) ---
step "Creating container $CTID ($HOSTNAME)"
if pct status "$CTID" &>/dev/null; then
  info "CT $CTID already exists; skipping create"
else
  pct create "$CTID" "$TEMPLATE_STORAGE:vztmpl/$TEMPLATE" \
    --hostname "$HOSTNAME" --cores 2 --memory 2048 --swap 512 \
    --rootfs "$ROOTFS_STORAGE:8" \
    --net0 name=eth0,bridge="$BRIDGE",ip=dhcp \
    --unprivileged 1 --onboot 1
  pct start "$CTID"
  done_ok "CT $CTID created and started"
fi

# --- Attach the control node to the internal network ---
# CT 100 gets a second NIC on vmbr1 (.100) so it can reach every service CT at
# its static internal IP (10.1.1.10X) — no DHCP guessing. Idempotent: re-running
# pct set with the same value is a no-op.
step "Attaching control node to internal network"
pct set "$CTID" -net1 name=eth1,bridge=vmbr1,ip=10.1.1.100/24
done_ok "eth1 = 10.1.1.100 on vmbr1"

# --- Wait for the container network to come up ---
info "waiting for container network…"
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
step "Provisioning control node (locale, Ansible, repo)"
info "installing Ansible…"
pct exec "$CTID" -- bash -c "
  export DEBIAN_FRONTEND=noninteractive
  apt-get -qq update
  sed -i 's/^# *en_US.UTF-8 UTF-8/en_US.UTF-8 UTF-8/' /etc/locale.gen
  locale-gen
  grep -q '^LANG=' /etc/environment || echo 'LANG=en_US.UTF-8' >> /etc/environment
  apt-get -qq -y install ansible git
  [ -d /opt/zai-ops ] || git clone --quiet $REPO_URL /opt/zai-ops
  # community.proxmox >=1.6.0 is what CT 100 uses to build every other CT over the
  # API, so it's as fundamental as Ansible itself and installed right here. Debian's
  # ansible 12 bundles 1.3.0, which can't set the API read timeout — the LXC-create
  # POST then dies at proxmoxer's 5s default. Install the repo's pin into
  # /root/.ansible/collections (which precedes the dist-packages copy in Ansible's
  # search path); --upgrade makes galaxy honour the version range over the bundled
  # 1.3.0. Seeded here so provision.yml works straight after bootstrap, before
  # site.yml; the control_node role re-asserts it (seed-then-own, like the locale).
  ansible-galaxy collection install -r /opt/zai-ops/ansible/requirements.yml --upgrade
  # Put the repo's operator commands (zai-assign, zai-backup, …) on PATH so a
  # fresh \`pct enter $CTID\` can run them by name before site.yml has run. The
  # control_node role re-asserts these idempotently (seeded here, owned by Ansible
  # thereafter — same pattern as the locale). Two hooks: /etc/profile.d covers
  # login/ssh shells; sourcing it from /etc/bash.bashrc covers the interactive
  # *non-login* shell \`pct enter\` gives, which skips profile.d. The case-guard
  # stops nested shells from re-prepending and growing PATH.
  printf '%s\n' \
    '# Managed by zai-ops (bootstrap + control_node role).' \
    'case \":\$PATH:\" in' \
    '  *:/opt/zai-ops/bin:*) ;;' \
    '  *) export PATH=\"/opt/zai-ops/bin:\$PATH\" ;;' \
    'esac' > /etc/profile.d/zai-ops.sh
  grep -q '/etc/profile.d/zai-ops.sh' /etc/bash.bashrc || \
    printf '%s\n' '[ -r /etc/profile.d/zai-ops.sh ] && . /etc/profile.d/zai-ops.sh' >> /etc/bash.bashrc
"
done_ok "control node provisioned"

# --- Mint the Proxmox API token + Ansible Vault for the control node ---
# This is host-side because it must be: an API token can only be created with
# pveum (a host-only binary) or the API, and calling the API would already
# require credentials. So the first credential is minted here, then handed to
# CT 100 as part of equipping it — there is still only one host run.
step "Minting Proxmox API token + vault"
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
  done_ok "token minted and vault encrypted"
else
  info "vault already present; skipping token mint"
fi

# --- Done ---
printf '\n%s==================================================================%s\n' "$GREEN$BOLD" "$RESET"
printf '%s CT %s (%s) ready.%s\n' "$GREEN$BOLD" "$CTID" "$HOSTNAME" "$RESET"
printf '%s==================================================================%s\n' "$GREEN$BOLD" "$RESET"
echo "Next, configure the control node and build the service containers:"
echo
echo "  pct enter $CTID"
echo "  cd /opt/zai-ops/ansible"
echo "  ansible-playbook site.yml                            # configure the control node"
echo "  ansible-playbook verify-proxmox.yml                  # confirm API token"
echo "  zai-assign object-store 101                          # assign object-store its CTID"
echo "  ansible-playbook provision.yml --limit object-store  # create + configure object-store"
echo "  zai-assign postgres 102                              # assign postgres its CTID"
echo "  ansible-playbook provision.yml --limit postgres      # create + configure postgres"

# --- Vault password — printed LAST so it isn't scrolled away ---
if [[ "$PROVISIONED" -eq 1 ]]; then
  printf '\n%s==================================================================%s\n' "$YELLOW$BOLD" "$RESET"
  printf '%s VAULT PASSWORD — back this up off-box (e.g. a password manager).%s\n' "$YELLOW$BOLD" "$RESET"
  echo " Stored on $HOSTNAME at /root/.vault_pass so Ansible auto-decrypts"
  echo " (recoverable there as root). Needed to view/edit secrets:"
  echo "   ansible-vault edit group_vars/all/vault.yml"
  echo
  printf '%s     %s%s\n' "$BOLD" "$VAULT_PASS" "$RESET"
  printf '%s==================================================================%s\n' "$YELLOW$BOLD" "$RESET"
fi
