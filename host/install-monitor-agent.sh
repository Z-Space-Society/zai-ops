#!/usr/bin/env bash
#
# install-monitor-agent.sh: install or update the Beszel monitoring agent on a
# machine Ansible doesn't manage (zai-ops#3, step 6). Run as root.
#
# On the Proxmox host, from the host's clone of zai-ops:
#
#   /root/zai-ops/host/install-monitor-agent.sh        # control node is CT 100
#   /root/zai-ops/host/install-monitor-agent.sh 199    # or another CTID
#
# On an inference node, run the copy CT 100 prints instead, which carries its
# inputs in a header (MONITOR_AGENT_PRESET=1, below):
#
#   scn-config nonint print-monitor-agent-install salmon > /root/salmon-agent.sh
#
# It does what the monitor_agent role does on a container (docs/roles/
# monitor_agent.md): the checksummed release, a beszel-agent user, a fixed
# fingerprint, a root-only env file, the role's own hardened unit, and the
# daily apt list refresh unless Proxmox's pveupdate already does it. A re-run
# changes only what moved and restarts the agent only then.
#
# On the host, every input is READ from CT 100 as data: the hub's address, the
# agent token, the hub key's public half, and the version and checksum pins from
# CT 100's checkout. Nothing CT 100 produces is ever run here: CT 100 has no
# path into the host (ADR-0008), and running its output as host root would give
# it one.
set -euo pipefail

die() { echo "install-monitor-agent: $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "this script must run as root."

AGENT_USER=beszel-agent
AGENT_BIN=/usr/local/bin/beszel-agent
DOWNLOAD_DIR=/opt/beszel-agent
DATA_DIR=/var/lib/beszel-agent
ENV_FILE=/etc/beszel-agent.env
UNIT_DIR=/etc/systemd/system

if [[ "${MONITOR_AGENT_PRESET:-}" == 1 ]]; then
  # --- Inputs from the header CT 100 wrote ------------------------------------
  for var in HUB_URL TOKEN KEY NAME DOMAIN VERSION SHA256 UNIT REFRESH_SERVICE REFRESH_TIMER; do
    v="MONITOR_AGENT_$var"
    [[ -n "${!v:-}" ]] || die "$v is not set; regenerate this script on CT 100."
  done
  HUB_URL=$MONITOR_AGENT_HUB_URL TOKEN=$MONITOR_AGENT_TOKEN KEY=$MONITOR_AGENT_KEY
  NAME=$MONITOR_AGENT_NAME DOMAIN=$MONITOR_AGENT_DOMAIN
  VERSION=$MONITOR_AGENT_VERSION SHA256=$MONITOR_AGENT_SHA256
  UNIT_TEXT=$MONITOR_AGENT_UNIT
  REFRESH_SERVICE=$MONITOR_AGENT_REFRESH_SERVICE REFRESH_TIMER=$MONITOR_AGENT_REFRESH_TIMER
  CTID=""
else
  # --- Inputs read from CT 100 (the Proxmox host) ------------------------------
  CTID="${1:-100}"
  command -v pct >/dev/null || die "no pct here: run this on the Proxmox host, or use the script CT 100 prints for other machines."
  pct status "$CTID" 2>/dev/null | grep -q running || die "CT $CTID is not running; pass the control node's CTID."
  REPO="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
  ROLE="$REPO/ansible/roles/monitor_agent"
  ct() { pct exec "$CTID" -- "$@"; }
  # One value from a committed YAML file in CT 100's checkout, quotes dropped.
  pin() { ct awk -v k="$1:" '$1==k{gsub(/"/, "", $2); print $2}' "/opt/zai-ops/ansible/$2"; }

  # ansible-inventory reads only the inventory, so this carries the hub's CTID
  # and the domain from local.yml and no vault secret.
  read -r HUB_CTID DOMAIN < <(ct bash -c 'cd /opt/zai-ops/ansible && ansible-inventory --host beszel' \
    | python3 -c 'import json, sys; v = json.load(sys.stdin); print(v.get("ctid", ""), v.get("cluster_domain", ""))')
  [[ -n "$HUB_CTID" && -n "$DOMAIN" ]] || die "CT $CTID records no CTID for beszel, or no cluster_domain; provision the hub first."
  PORT=$(pin beszel_port group_vars/all/main.yml)
  VERSION=$(pin beszel_version group_vars/all/main.yml)
  SHA256=$(pin monitor_agent_sha256 roles/monitor_agent/defaults/main.yml)
  # stdout, never argv: the token is never on a command line here.
  TOKEN=$(ct cat /root/.zai-secrets/beszel_agent_token)
  KEY=$(ct ssh-keygen -y -f /root/.zai-secrets/beszel_hub_key)
  [[ -n "$PORT" && -n "$VERSION" && -n "$SHA256" && -n "$TOKEN" && -n "$KEY" ]] \
    || die "could not read the pins, the agent token or the hub key from CT $CTID."
  # The host is 10.1.1.1 on vmbr1, so it reaches the hub directly.
  HUB_URL="http://10.1.1.$HUB_CTID:$PORT"
  NAME=$(hostname)

  # The role's own unit, its five variables filled in, so there is one unit
  # definition and not two. A placeholder left over means the template grew a
  # variable this list doesn't know.
  UNIT_TEXT=$(sed \
    -e 's|{{ ansible_managed }}|Managed by zai-ops (host/install-monitor-agent.sh)|' \
    -e "s|{{ monitor_agent_user }}|$AGENT_USER|g" \
    -e "s|{{ monitor_agent_group }}|$AGENT_USER|g" \
    -e "s|{{ monitor_agent_bin }}|$AGENT_BIN|g" \
    -e "s|{{ monitor_agent_data_dir }}|$DATA_DIR|g" \
    "$ROLE/templates/beszel-agent.service.j2")
  [[ "$UNIT_TEXT" != *"{{"* ]] || die "the unit template has a variable this script doesn't fill in."
  REFRESH_SERVICE=$(cat "$ROLE/files/apt-lists-refresh.service")
  REFRESH_TIMER=$(cat "$ROLE/files/apt-lists-refresh.timer")
fi

[[ "$SHA256" =~ ^[0-9a-f]{64}$ ]] || die "the checksum pin is not a sha256: $SHA256"
CHANGED=0
RELOAD=0

# put <path> <mode> <owner> <content>: write only when the content differs.
put() {
  local path=$1 mode=$2 owner=$3 content=$4
  if [[ ! -f "$path" ]] || [[ "$(cat "$path")" != "$content" ]]; then
    install -m "$mode" -o "$owner" -g "$owner" /dev/null "$path"
    printf '%s\n' "$content" > "$path"
    return 0
  fi
  return 1
}

# --- Service account + directories -------------------------------------------------
getent group "$AGENT_USER" >/dev/null || groupadd --system "$AGENT_USER"
id "$AGENT_USER" >/dev/null 2>&1 \
  || useradd --system -g "$AGENT_USER" -s /usr/sbin/nologin -d "$DATA_DIR" -M "$AGENT_USER"
install -d -m 0755 -o root -g root "$DOWNLOAD_DIR"
install -d -m 0700 -o "$AGENT_USER" -g "$AGENT_USER" "$DATA_DIR"

# --- Binary --------------------------------------------------------------------
# Named by version, as in the role: a re-run at the same version downloads
# nothing, and a download that fails the checksum is never kept.
command -v curl >/dev/null || DEBIAN_FRONTEND=noninteractive apt-get -qq -y install curl
TARBALL="$DOWNLOAD_DIR/beszel-agent_linux_amd64-$VERSION.tar.gz"
if ! echo "$SHA256  $TARBALL" | sha256sum -c --status 2>/dev/null; then
  tmp=$(mktemp)
  curl -fsSL -o "$tmp" "https://github.com/henrygd/beszel/releases/download/v$VERSION/beszel-agent_linux_amd64.tar.gz"
  if ! echo "$SHA256  $tmp" | sha256sum -c --status; then
    rm -f "$tmp"
    die "the v$VERSION download does not match its pinned checksum; nothing was installed."
  fi
  install -m 0644 -o root -g root "$tmp" "$TARBALL"
  rm -f "$tmp"
  rm -f "$AGENT_BIN"
fi
if [[ ! -x "$AGENT_BIN" ]]; then
  tar -xzf "$TARBALL" -C "$(dirname "$AGENT_BIN")" beszel-agent
  chown root:root "$AGENT_BIN"
  chmod 0755 "$AGENT_BIN"
  CHANGED=1
fi

# --- Identity + config ----------------------------------------------------------
# The role's fingerprint: sha256 of <domain>/<name>, 48 hex characters, so a
# reinstalled machine reattaches to its record in the hub.
FINGERPRINT=$(printf '%s/%s' "$DOMAIN" "$NAME" | sha256sum | cut -c1-48)
put "$DATA_DIR/fingerprint" 0600 "$AGENT_USER" "$FINGERPRINT" && CHANGED=1
put "$ENV_FILE" 0600 root "HUB_URL=$HUB_URL
TOKEN=$TOKEN
KEY=$KEY
SYSTEM_NAME=$NAME
DATA_DIR=$DATA_DIR
DISABLE_SSH=true" && CHANGED=1
put "$UNIT_DIR/beszel-agent.service" 0644 root "$UNIT_TEXT" && { CHANGED=1; RELOAD=1; }

# --- apt list refresh --------------------------------------------------------------
# Proxmox's pve-daily-update.timer runs pveupdate, which refreshes the lists
# every day, so the host needs nothing more. Elsewhere, the role's timer.
if systemctl cat pve-daily-update.timer >/dev/null 2>&1; then
  echo "apt lists: refreshed daily by Proxmox's pve-daily-update.timer"
else
  put "$UNIT_DIR/apt-lists-refresh.service" 0644 root "$REFRESH_SERVICE" && RELOAD=1
  put "$UNIT_DIR/apt-lists-refresh.timer" 0644 root "$REFRESH_TIMER" && RELOAD=1
fi

[[ $RELOAD -eq 0 ]] || systemctl daemon-reload
if ! systemctl cat pve-daily-update.timer >/dev/null 2>&1; then
  systemctl enable --now apt-lists-refresh.timer >/dev/null 2>&1
  # Fresh lists before the agent's first check, as the role does on a new CT.
  find /var/lib/apt/lists -maxdepth 1 -name '*_Packages' -mtime -1 | grep -q . || apt-get update -qq
fi

systemctl enable beszel-agent >/dev/null 2>&1
if [[ $CHANGED -eq 1 ]]; then
  systemctl restart beszel-agent
else
  systemctl start beszel-agent
fi

# --- Smoke test --------------------------------------------------------------------
# The agent logs this once the hub has checked its signature and taken it on.
SINCE=$(systemctl show -p ActiveEnterTimestamp --value beszel-agent)
for _ in $(seq 30); do
  if journalctl -u beszel-agent --since "$SINCE" --no-pager -q | grep -q 'WebSocket connected'; then
    echo "beszel-agent v$VERSION on $NAME: connected to $HUB_URL"
    break
  fi
  sleep 2
done
journalctl -u beszel-agent --since "$SINCE" --no-pager -q | grep -q 'WebSocket connected' \
  || die "the agent did not connect to $HUB_URL within a minute; see: journalctl -u beszel-agent -n 50"

# --- Alerts ------------------------------------------------------------------------
# The hub can only take alerts for a system once it exists, which it now does.
if [[ -n "$CTID" ]]; then
  pct exec "$CTID" -- /opt/zai-ops/bin/scn-config nonint apply-monitor-agents beszel \
    | grep -A4 -E '^fatal|PLAY RECAP'
else
  echo "Now, on CT 100: scn-config nonint apply-monitor-agents beszel  (puts the alerts on $NAME)"
fi
