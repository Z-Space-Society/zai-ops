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
- [Generic repo vs runtime data](#generic-repo-vs-runtime-data)
- [Inference nodes](#inference-nodes)
- [Playbooks](#playbooks)
- [Roles](#roles)
- [Secrets & trust model](#secrets--trust-model)
- [Backups](#backups)
- [Known gotchas](#known-gotchas)
- [TODO](#todo)

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
- **CT 105** (object-store, Garage) is internal-only too — it's the restic
  backend the [`backup`](#backups) job writes to, not a user-facing service.
- **Inference nodes** (salmon, orca, …) are **bare-metal**, *outside* the Proxmox
  host — they run `llama-server` only, behind the gateway, and are configured by
  CT 100 over SSH. See [Inference nodes](#inference-nodes).

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
| CT 105 object-store  | —             | `10.1.1.105` (gw `10.1.1.1`)      |

- `vmbr1` has **no uplink** — it's a pure virtual switch. The host masquerades
  internal traffic out via `vmbr0`, so internal-only CTs can still `apt`/`pip`.
- Service CTs get **static** internal IPs, so CT 100 always knows where to SSH
  (no DHCP guessing).
- nginx is **dual-homed** (LAN + internal); the rest are internal-only.

---

## Generic repo vs runtime data

This repo is meant to rebuild *any* cluster, not just this one (it's the pilot
for COAI). So **this-cluster facts stay out of the committed tree** and live only
as **runtime data on the control node**, git-ignored — the same pattern the vault
already uses for the API token.

- The repo holds **generic** automation (roles, playbooks) and **blueprint
  constants** every cluster reuses — the `10.1.1.0/24` net, the CT-ID layout.
- This-cluster specifics — *which inference nodes exist, their IPs, their models*
  — live in `ansible/inventory/local.yml`, written by
  [`enroll-inference-node.yml`](#playbooks) and never committed.
- The inventory is loaded as a **directory** (`inventory/`), so the committed
  blueprint (`hosts.yml`, with an empty `inference_nodes` group) and the runtime
  `local.yml` merge automatically.

Because the roster isn't in the repo, a control-node rebuild is **repo +
restored runtime data** — back up `local.yml` alongside the vault. The decision
is pinned in [ADR-0001](decisions/0001-repo-stays-generic.md). The existing
service-CT IPs and `proxmox_node_name` still live in the committed inventory and
are slated for the same treatment — see [TODO](#todo).

---

## Inference nodes

The inference nodes (salmon, orca, …) are **bare-metal Debian 13 machines** with
NVIDIA GPUs, sitting on the LAN/tailnet *outside* the Proxmox host. They run
`llama-server` only — no double duty — and are reached for inference by the
LiteLLM gateway (addressing per the vault's ADR-002).

CT 100 configures them over SSH as a dedicated **`ansible` user** (NOPASSWD
sudo), using its root ed25519 key. Two roles apply: [`nvidia_cuda`](roles/nvidia_cuda.md)
(driver + CUDA) then [`llama_server`](roles/llama_server.md) (build llama.cpp,
install the unit, enabled-not-started until a GGUF is staged).

Operator flow, from inside CT 100:

```bash
ansible-playbook enroll-inference-node.yml -e "name=salmon ansible_host=192.168.6.63"
ansible-playbook inference.yml --limit salmon
```

`ansible_host` is whatever reaches the node — a LAN IP today, a Tailscale 100.x
later; the repo bakes in neither.

**Node prep (manual, per node), before the first run:**

- **Secure Boot disabled** in BIOS (unsigned NVIDIA modules won't load otherwise;
  the role asserts it).
- An **`ansible` user with NOPASSWD sudo**, with **CT 100's root public key** in
  its `authorized_keys`.
- On Trixie, `systemd-networkd` needs a `.network` file to DHCP (e.g.
  `/etc/systemd/network/20-wired.network` with `DHCP=yes`) so the node is
  reachable at its enrolled address.

---

## Playbooks

| Playbook              | Runs on        | Purpose                                              |
| --------------------- | -------------- | --------------------------------------------------- |
| `site.yml`            | CT 100 (local) | Configure the control node (applies `control_node`) |
| `verify-proxmox.yml`  | CT 100 (local) | Read-only check that the API token authenticates    |
| `provision.yml`       | CT 100 → API/SSH | Create service CTs over the API, then configure them |
| `enroll-inference-node.yml` | CT 100 (local) | Record a bare-metal inference node in the runtime inventory (records only) |
| `inference.yml`       | CT 100 → SSH   | Configure inference nodes (`nvidia_cuda` + `llama_server`) |
| `add-github-user.yml` | CT 100 (local) + SSH | Create a human admin account from GitHub keys, with sudo, on CT 100 + inference nodes |
| `backup.yml`          | CT 100 (local) | Install restic + a daily timer backing up control-node runtime state to the object store |

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
| [`nvidia_cuda`](roles/nvidia_cuda.md)      | inference nodes | NVIDIA driver + CUDA toolkit (bare-metal Debian 13) |
| [`llama_server`](roles/llama_server.md)    | inference nodes | Build llama.cpp (CUDA) + install the `llama-server` unit |
| [`github_user`](roles/github_user.md)      | CT 100 + inference nodes | Create a human admin account from GitHub public keys, with sudo |
| [`object_store`](roles/object_store.md)    | CT 105     | Single-node Garage (S3-compatible) — the on-box backup target |
| [`backup`](roles/backup.md)                | CT 100     | restic + daily timer backing up runtime state to the object store |

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
  injected at create time (key-only login). Inference nodes are reached with the
  same key as a dedicated `ansible` user (see [Inference nodes](#inference-nodes)).
- The inference-node roster (`ansible/inventory/local.yml`) is git-ignored
  runtime state, like the vault — backed up with the control node by the
  [`backup`](#backups) job. See [Generic repo vs runtime data](#generic-repo-vs-runtime-data).
- The object-store key and restic repo password are **auto-generated** on first
  run by `password` lookups (see `group_vars/all/main.yml`) and persisted under
  `/root/.zai-secrets` on CT 100 — same plaintext-on-the-box posture as
  `/root/.vault_pass`, no manual entry. They're part of restored state: a fresh
  CT 100 regenerates different values, so restore `/root/.zai-secrets` before
  re-running Ansible.

---

## Backups

Recovery is meant to be **repo + restored state**: reflash, run `bootstrap.sh`,
restore the runtime state, re-run Ansible. The [`backup`](roles/backup.md) role
makes the "restore" half real — it backs up the unreproducible bits (the vault,
`/root/.vault_pass`, the root SSH key, and `inventory/local.yml`) with
[restic](https://restic.net/) on a **daily systemd timer**.

The restic repository is the cluster **object store**: a single-node
[Garage](roles/object_store.md) (S3-compatible) on CT 105, internal-only on
`vmbr1`. restic encrypts and deduplicates, so the vault password and SSH key are
safe at rest in the bucket.

```bash
# Object store is the restic backend, so it comes up first:
ansible-playbook provision.yml --limit object-store
ansible-playbook backup.yml
```

**Tiers.** Tier 1 (control-node state) is live today. Tier 2 (service databases —
`pg_dump` over SSH into the same repo) is a documented seam in
[`backup`](roles/backup.md), inert until Postgres/CT 102 exists.

> **Scope caveat — this is not yet disaster recovery.** The object store sits on
> the *same physical disk* as everything else, so today's backup guards
> **CT-level** loss (restore a clobbered service container) but **not whole-host**
> loss (dead disk, stolen box, fire). Closing that gap is a second, **off-site**
> restic target — a one-line backend addition, tracked in [TODO](#todo).

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

Hard-won lessons on the bare-metal **inference nodes** (Debian 13 + NVIDIA):

- **Secure Boot must be disabled** in BIOS — unsigned NVIDIA kernel modules
  won't load otherwise. Manual BIOS step; `nvidia_cuda` asserts it and fails fast.
- **Trixie needs `contrib non-free`.** The minimal install only enables
  `main non-free-firmware`; the NVIDIA packages aren't visible until you add them.
- **`nvidia-cuda-toolkit-gcc` bridges the GCC 14 / nvcc 12.4 mismatch.** Without
  it the CUDA build fails on a compiler-version check.
- **`systemd-networkd` won't DHCP without a `.network` file.** Create
  `/etc/systemd/network/20-wired.network` with `DHCP=yes` during node prep, or the
  node never comes up on the network.
- **CUDA arch is auto-detected** from `nvidia-smi` (`compute_cap`) — no per-host
  build flag to maintain.

Hard-won lessons provisioning **human accounts** (`add-github-user.yml`):

- **Forced first-login password change needs a real temp password.** Over SSH
  *key* auth, PAM (`UsePAM yes`, Debian default) asks for the *current* password
  to authorize the new one — a locked/empty account can't complete the change. So
  the account is seeded with a printed temp password rather than locked.
- **Set passwords with `chpasswd`, not `password_hash`.** Debian 13's Python 3.13
  removed the stdlib `crypt` module Ansible's `password_hash` filter used;
  `chpasswd` on the target uses libc crypt instead, so no `python3-passlib` on
  the control node.

---

## TODO

- **Retrofit the committed inventory into the generic/runtime split.** Move the
  service-CT internal IPs and `proxmox_node_name: alhambra` out of the committed
  tree into runtime data, the way the inference nodes already work
  ([ADR-0001](decisions/0001-repo-stays-generic.md)). Touches `bootstrap.sh`,
  `provision.yml`, and `inventory/hosts.yml`.
- **Off-site backup target.** The [`backup`](#backups) job ships runtime state to
  the on-box object store (CT 105), which guards CT-level loss but not whole-host
  loss. Add a second restic target off the box (SFTP/B2/S3) so a dead host or
  lost site is recoverable — restic's backend is swappable, so this is a second
  repo in the same wrapper, not a rewrite.
