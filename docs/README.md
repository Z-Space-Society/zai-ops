# zai-ops documentation

Infrastructure-as-code for the Z-Space AI Cluster (ZAI). This directory is the
reference manual: how the cluster bootstraps itself, how it's wired, and what
each Ansible role does.

The guiding goal is **full reproducibility** — flash Proxmox onto a host, run
one script, and the stack rebuilds itself from this repo.

## Contents

- [Bootstrap process](#bootstrap-process)
- [Architecture](#architecture)
- [Networking](#networking)
- [Playbooks](#playbooks)
- [Roles](#roles)
- [Secrets & trust model](#secrets--trust-model)
- [Known gotchas](#known-gotchas)

---

## Bootstrap process

There is exactly **one** host-level script, [`bootstrap.sh`](../bootstrap.sh),
run as root on a freshly-flashed Proxmox host. Everything after the control
node exists is driven by Ansible from inside it. Invoke it directly (base
Proxmox has no git):

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/Z-Space-Society/zai-ops/main/bootstrap.sh)"
# override the CT ID (default 100):
# bash -c "$(curl -fsSL .../bootstrap.sh)" _ 199
```

What it does, in order (each phase prints a numbered banner):

1. **Configure apt repositories** — disable the enterprise/Ceph repos, add
   `pve-no-subscription`, update.
2. **Upgrade host packages** — `apt-get full-upgrade`.
3. **Create the internal network** — adds the `vmbr1` bridge (no uplink) with
   the host at `10.1.1.1`, plus a NAT/masquerade rule so internal-only CTs can
   reach the internet. See [Networking](#networking).
4. **Enable IPv4 forwarding** — persisted in `/etc/sysctl.d/99-zai-forward.conf`.
5. **Prepare the container template** — download `debian-13-standard` if absent.
6. **Create the control node** — CT 100 (`ansible-control`), unprivileged,
   2 cores / 2 GB / 8 GB, `net0` on `vmbr0` (DHCP). Skipped if it already exists.
7. **Attach to the internal network** — give CT 100 a `vmbr1` NIC at `10.1.1.100`.
8. **Provision the control node** — fix the locale (must happen before Ansible
   can run at all), install `ansible` + `git`, clone this repo to `/opt/zai-ops`.
9. **Mint the Proxmox API token + vault** — create the `ansible@pve` user, the
   `ZaiProvision` role, and a token; write the credentials into an encrypted
   Ansible Vault on CT 100. See [Secrets & trust model](#secrets--trust-model).

The script prints a **vault password** on its last line — back it up off-box.

After it finishes, continue inside the control node:

```bash
pct enter 100
cd /opt/zai-ops/ansible
ansible-playbook site.yml                           # configure CT 100
ansible-playbook verify-proxmox.yml                 # confirm the API token
ansible-playbook provision.yml --limit ct101-nginx  # create + configure CT 101
```

---

## Architecture

```
                    ┌─────────────── Proxmox host (alhambra) ───────────────┐
   LAN (vmbr0) ─────┤                                                        │
        │           │   CT 100  ansible-control   (control node)            │
        │           │      • runs Ansible, holds the vault + SSH key        │
        │           │      • net0 vmbr0 (DHCP), net1 vmbr1 10.1.1.100       │
        │           │                                                        │
   ┌────┴─────┐     │   CT 101  nginx   (reverse proxy, LAN-facing)         │
   │ clients  │────▶│      • net0 vmbr0 (DHCP), net1 vmbr1 10.1.1.101       │
   └──────────┘     │                                                        │
                    │   CT 102+ postgres / litellm / open-webui  (internal) │
                    │      • vmbr1 only, route out via host NAT (10.1.1.1)   │
                    └────────────────────────────────────────────────────────┘
```

- **CT 100** creates and configures every other container over the Proxmox API
  (create) and SSH (configure). It is the only machine that holds secrets.
- **CT 101 (nginx)** is the only LAN-facing service; it reverse-proxies the
  internal services.
- **CT 102-104** (postgres, litellm, open-webui) live only on the internal
  network and are reached through nginx.

---

## Networking

An isolated internal network keeps everything except the reverse proxy off the
LAN.

| Host                 | LAN (`vmbr0`) | Internal (`vmbr1`, `10.1.1.0/24`) |
| -------------------- | ------------- | --------------------------------- |
| Proxmox host         | physical NIC  | `10.1.1.1` (NAT gateway)          |
| CT 100 control node  | DHCP          | `10.1.1.100`                      |
| CT 101 nginx         | DHCP          | `10.1.1.101`                      |
| CT 102+ services     | —             | `10.1.1.10X` (gw `10.1.1.1`)      |

- `vmbr1` has **no uplink** — it's a pure virtual switch. The host masquerades
  internal traffic out via `vmbr0`, so internal-only CTs can still `apt`/`pip`.
- Service CTs get **static** internal IPs, so CT 100 always knows where to SSH
  (no DHCP guessing).
- nginx is **dual-homed** (LAN + internal); the rest are internal-only.

---

## Playbooks

| Playbook              | Runs on        | Purpose                                              |
| --------------------- | -------------- | --------------------------------------------------- |
| `site.yml`            | CT 100 (local) | Configure the control node (applies `control_node`) |
| `verify-proxmox.yml`  | CT 100 (local) | Read-only check that the API token authenticates    |
| `provision.yml`       | CT 100 → API/SSH | Create service CTs over the API, then configure them |

`provision.yml` has two plays: a **create** play (`connection: local`, talks to
the Proxmox API) and a **configure** play (SSH into the new CT, applies its
role). A `when: ct_netif is defined` guard skips any service host whose create
specs aren't filled in yet, so a no-`--limit` run is safe.

---

## Roles

| Role                                       | Applied to | What it does                                            |
| ------------------------------------------ | ---------- | ------------------------------------------------------- |
| [`control_node`](roles/control_node.md)    | CT 100     | Base config for the Ansible control node                |
| [`nginx`](roles/nginx.md)                  | CT 101     | Install + configure nginx as the cluster reverse proxy  |

(More roles — postgres, litellm, open-webui — will be added here as they come
online.)

---

## Secrets & trust model

- The Proxmox API token lives in `ansible/group_vars/all/vault.yml`, encrypted
  with Ansible Vault and git-ignored.
- The vault password sits at `/root/.vault_pass` on CT 100 so Ansible
  auto-decrypts. This is deliberate: **host root is the trust boundary** —
  anyone with host root can `pct exec` into CT 100 anyway. Encryption-at-rest
  here protects the git tree, not the running box.
- For stricter deployments, drop `vault_password_file` from `ansible.cfg` and
  run with `--ask-vault-pass` (the password is printed at bootstrap for backup).
- Service CTs are reached via a root **ed25519 key** generated on CT 100 and
  injected at create time (key-only login).

---

## Known gotchas

Hard-won lessons with `community.proxmox.proxmox` (the version bundled with
Debian 13's `ansible` 12). These will recur on CT 102-104:

- **Disk must use the `storage:size` form.** Use `disk: "local-lvm:8"`, *not*
  `disk: 8` with a separate `storage:` — the latter renders a pathless rootfs
  that PVE rejects under token auth ("Only root can pass arbitrary filesystem
  paths").
- **Start tasks need `hostname`.** `state: started` with only `vmid` hits a
  KeyError `'name'` on freshly created CTs ([community.proxmox #98]). Pass
  `hostname:` on the start task; a small `retries`/`until` covers the race.
- **Systemd 257 wants nesting.** Unprivileged CTs warn "you may need to enable
  nesting"; service CTs are created with `features: [nesting=1]`.

[community.proxmox #98]: https://github.com/ansible-collections/community.proxmox/issues/98
