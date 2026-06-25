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
- [Service CTID assignment](#service-ctid-assignment)
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
3. **Suppress the subscription nag** — patch `proxmox-widget-toolkit` so the
   "No valid subscription" popup never fires, and install a dpkg post-invoke
   hook (`/etc/apt/apt.conf.d/00-zai-no-nag`) so the patch is re-applied after
   any upgrade that ships a fresh `proxmoxlib.js`. See [Known gotchas](#known-gotchas).
4. **Create the internal network** — adds the `vmbr1` bridge (no uplink) with
   the host at `10.1.1.1`, plus a NAT/masquerade rule so internal-only CTs can
   reach the internet. See [Networking](#networking).
5. **Enable IPv4 forwarding** — persisted in `/etc/sysctl.d/99-zai-forward.conf`.
6. **Prepare the container template** — download `debian-13-standard` if absent.
7. **Create the control node** — CT 100 (`ansible-control`), unprivileged,
   2 cores / 2 GB / 8 GB, `net0` on `vmbr0` (DHCP). Skipped if it already exists.
8. **Attach to the internal network** — give CT 100 a `vmbr1` NIC at `10.1.1.100`.
9. **Provision the control node** — fix the locale (must happen before Ansible
   can run at all), install `ansible` + `git`, clone this repo to `/opt/zai-ops`,
   and put the repo's [`bin/`](#operator-commands) on PATH (so a fresh
   `pct enter` can run `zai-assign`/`zai-backup` before `site.yml` has run).
10. **Mint the Proxmox API token + vault** — create the `ansible@pve` user, the
   `ZaiProvision` role, and a token; write the credentials into an encrypted
   Ansible Vault on CT 100. See [Secrets & trust model](#secrets--trust-model).

The script prints a **vault password** on its last line — back it up off-box.

After it finishes, continue inside the control node:

```bash
pct enter 100
cd /opt/zai-ops/ansible
ansible-playbook site.yml                       # configure the control node
ansible-playbook verify-proxmox.yml             # confirm the API token
zai-assign proxy 110                            # assign proxy its CTID (10.1.1.110)
ansible-playbook provision.yml --limit proxy    # create + configure proxy
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
   ┌────┴─────┐     │   CT 110  proxy  (Caddy reverse proxy, LAN-facing)    │
   │ clients  │────▶│      • net0 vmbr0 (DHCP), net1 vmbr1 10.1.1.110       │
   └──────────┘     │                                                        │
                    │   CT 101+ object-store / postgres / litellm (internal)│
                    │      • vmbr1 only, route out via host NAT (10.1.1.1)   │
                    └────────────────────────────────────────────────────────┘
```

- **CT 100** creates and configures every other container over the Proxmox API
  (create) and SSH (configure). It is the only machine that holds secrets.
- **CT 110 (proxy — Caddy)** is the only LAN-facing service; it reverse-proxies
  the internal services. Its routes are declarative in git ([`proxy`
  role](roles/proxy.md)) — rendered into a `Caddyfile` from `caddy_proxy_hosts`,
  not held in a UI database — so the CT holds nothing that needs backing up. Only
  one public hostname should point at a given proxy at a time (controlled at
  Cloudflare).
- **CT 102+** (postgres, litellm, open-webui) live only on the internal network
  and are reached through the proxy.
- **CT 101** (object-store, Garage) is internal-only too — it's the restic
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
| CT 101 object-store  | —             | `10.1.1.101` (gw `10.1.1.1`)      |
| CT 102 postgres      | —             | `10.1.1.102` (gw `10.1.1.1`)      |
| CT 110 proxy (Caddy) | DHCP          | `10.1.1.110`                      |
| CT 12X apps          | —             | `10.1.1.12X` (gw `10.1.1.1`)      |

- `vmbr1` has **no uplink** — it's a pure virtual switch. The host masquerades
  internal traffic out via `vmbr0`, so internal-only CTs can still `apt`/`pip`.
- Service CTs get **static** internal IPs, so CT 100 always knows where to SSH
  (no DHCP guessing).
- The proxy CT is **dual-homed** (LAN + internal) — it's the edge; every other
  service is internal-only.

**CTID tiers (a convention, not enforced).** The example numbers follow a tiered
layout so the CTID itself signals where a service sits in the dependency stack —
and the gaps leave room to grow a tier without renumbering:

| Range       | Tier         | Examples                                   |
| ----------- | ------------ | ------------------------------------------ |
| `100`–`109` | Core infra   | control (100), object-store (101), postgres (102) |
| `110`–`119` | Platform     | proxy/edge (110), auth (111), gateway (112) |
| `120`–`129` | Applications | open-webui (120), … other user-facing apps |

The dependency arrows point **downward** (apps → platform → core), and the line
between core and platform doubles as a trust line: the data foundations stay off
the LAN, while the only internet-facing box (the proxy) sits one tier out.

- The specific numbers above are this cluster's **assigned** layout, not committed
  identity — each is bound with `zai-assign` and could differ on another host.
  What's fixed is the `10.1.1.{ctid}` convention and the tier ranges above. See
  [Service CTID assignment](#service-ctid-assignment).

---

## Generic repo vs runtime data

This repo is meant to rebuild *any* cluster, not just this one (it's the pilot
for COAI). So **this-cluster facts stay out of the committed tree** and live only
as **runtime data on the control node**, git-ignored — the same pattern the vault
already uses for the API token.

- The repo holds **generic** automation (roles, playbooks) and the **blueprint
  constants** every cluster reuses — the `10.1.1.0/24` net and the
  `10.1.1.{ctid}` addressing *convention* (but not the specific numbers).
- This-cluster specifics — *which inference nodes exist, which CTID each service
  got, and the cluster's public base domain* — live in
  `ansible/inventory/local.yml`, written by
  [`enroll-inference-node.yml`](#playbooks) (inference roster),
  [`assign.yml`](#service-ctid-assignment) (`zai-assign`, service CTIDs), and
  [`set-domain.yml`](#service-ctid-assignment) (`zai-set-domain`, `cluster_domain`),
  and never committed. The committed `hosts.yml` carries **no container numbers** at
  all — services are keyed by logical name (`proxy`, `litellm`, …) and their IP
  is *derived* from the assigned CTID, so there's no second field to drift.
- The inventory is loaded as a **directory** (`inventory/`), so the committed
  blueprint (`hosts.yml`, with an empty `inference_nodes` group and number-free
  service blueprint) and the runtime `local.yml` merge automatically.

Because neither the roster nor the CTID assignments live in the repo, a
control-node rebuild is **repo + restored runtime data** — back up `local.yml`
alongside the vault. The decision is pinned in
[ADR-0001](decisions/0001-repo-stays-generic.md). Only `proxmox_node_name` still
lives in the committed inventory and is slated for the same treatment — see
[TODO](#todo).

---

## Service CTID assignment

The committed blueprint names services generically (`proxy`, `litellm`, …) and
carries **no container numbers**. The operator binds a service to a container ID
once, with the `zai-assign` command — one of the [operator
commands](#operator-commands) in the repo's `bin/`, on PATH. It runs in place from
git (self-locating to load `ansible.cfg`), so there's no installed copy to drift:

```bash
zai-assign proxy 110                  # proxy is now CT 110 at 10.1.1.110, cluster-wide
zai-assign proxy 111 -e reassign=true # move it (reassign guards against accidental clobber)
```

`zai-assign` is thin sugar over [`assign.yml`](#playbooks); the playbook is the
engine. It validates (CTID in range 100–999, not in
[`reserved_ctids`](#service-ctid-assignment), not already held by another service,
the service exists in the blueprint, and not already assigned unless
`reassign=true`), then **read-modify-writes** the whole `inventory/local.yml`
structure so every other assignment — and the inference-node roster that shares
the file — survives. Assigning a service the CTID it already has is an idempotent
no-op.

From then on the merged inventory resolves the service to `ctid` and a derived
`ansible_host` of `10.1.1.{ctid}` for every playbook; `provision.yml --limit
<service>` creates exactly that CT and **fails fast** if the service was never
assigned.

**`reserved_ctids`** (in [`group_vars/all/main.yml`](../ansible/group_vars/all/main.yml))
is the safety rail: a per-cluster list the allocator refuses to assign over.
Default is `[100]` (the control node). On a **brownfield** host, widen it to every
live CTID *before* assigning anything, so no run can stomp a container the cluster
didn't create.

> **Control-node exception.** The `10.1.1.{ctid}` convention is for **service
> containers only**. The control node's internal IP is pinned to `10.1.1.100` by
> `bootstrap.sh` regardless of its CTID (which may be 199 on a brownfield box), so
> it carries no `ctid` in the inventory and is never assigned.

### Cluster domain

The cluster's public base domain is the same category of data — per-cluster, not
committed identity — so it's set the same way, with the `zai-set-domain` operator
command:

```bash
zai-set-domain zai.cascadia.design    # cluster_domain, cluster-wide
```

`zai-set-domain` is thin sugar over [`set-domain.yml`](#playbooks). The playbook
validates the value is a plausible lowercase FQDN, then **read-modify-writes** the
same `inventory/local.yml` into `all.vars.cluster_domain` — so the CTID assignments
and inference roster sharing the file survive. Re-recording the current value is an
idempotent no-op. From then on `cluster_domain` resolves for every playbook;
later proxy routes build on it (e.g. `api.{{ cluster_domain }}`).

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
| `assign.yml`          | CT 100 (local) | Bind a service to a CTID in runtime inventory (the `zai-assign` engine) |
| `set-domain.yml`      | CT 100 (local) | Record the cluster's public base domain in runtime inventory (the `zai-set-domain` engine) |
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

## Operator commands

The things an operator *runs by hand* live in the repo's [`bin/`](../bin/), put on
PATH when the control node is configured. The convention:

- **Named for what they do, not the tool underneath** — `zai-backup`, not
  `zai-restic`; restic is an implementation detail hidden behind the command.
- **Run in place from git.** Nothing is copied to `/usr/local/bin`, so the command
  you run is always the one in the checkout — a `git pull` is the whole update
  story, no playbook replay (the [prime directive](../CLAUDE.md): pull = live).

| Command | Does | Backed by |
| ------- | ---- | --------- |
| `zai-assign <service> <ctid>` | Bind a service to a CTID in runtime inventory | [`assign.yml`](#playbooks) |
| `zai-set-domain <domain>` | Record the cluster's public base domain in runtime inventory | [`set-domain.yml`](#playbooks) |
| `zai-backup [run]` | Run the control-node backup (also the timer's `ExecStart`) | restic |
| `zai-backup <restic subcmd>` | Ad-hoc query/restore against the repo (`snapshots`, `check`, `restore …`) | restic |

Deployed *service* tooling (the `garage` binary, `garage-init.sh`) is a different
category — it lives on its service CT, not the control node, and isn't an operator
command. Only control-node operator commands belong in `bin/`.

---

## Roles

| Role                                       | Applied to | What it does                                            |
| ------------------------------------------ | ---------- | ------------------------------------------------------- |
| [`control_node`](roles/control_node.md)    | CT 100     | Base config for the Ansible control node                |
| [`proxy`](roles/proxy.md)                  | `proxy`    | Caddy reverse proxy — the LAN-facing edge; single apt package, git-tracked routes |
| [`nvidia_cuda`](roles/nvidia_cuda.md)      | inference nodes | NVIDIA driver + CUDA toolkit (bare-metal Debian 13) |
| [`llama_server`](roles/llama_server.md)    | inference nodes | Build llama.cpp (CUDA) + install the `llama-server` unit |
| [`github_user`](roles/github_user.md)      | CT 100 + inference nodes | Create a human admin account from GitHub public keys, with sudo |
| [`object_store`](roles/object_store.md)    | `object-store` | Single-node Garage (S3-compatible) — the on-box backup target |
| [`postgres`](roles/postgres.md)            | `postgres` | PostgreSQL 17 (Debian-native) — the internal database server |
| [`litellm`](roles/litellm.md)              | `litellm`  | LiteLLM proxy (venv) — OpenAI-compatible gateway, Postgres-backed |
| [`backup`](roles/backup.md)                | CT 100     | restic + daily timer backing up runtime state to the object store |

(More roles — open-webui — will be added here as they come online.)

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
[Garage](roles/object_store.md) (S3-compatible) in the `object-store` CT,
internal-only on `vmbr1`. restic encrypts and deduplicates, so the vault password
and SSH key are safe at rest in the bucket.

```bash
# Object store is the restic backend, so it's assigned and comes up first:
zai-assign object-store 105
ansible-playbook provision.yml --limit object-store
ansible-playbook backup.yml
```

The backup is one command, [`zai-backup`](#operator-commands) — `zai-backup` runs
it (also what the timer fires), and any restic subcommand (`zai-backup snapshots`,
`zai-backup check`, `zai-backup restore …`) is forwarded against the repo. restic
is an implementation detail; operators never invoke it directly.

**Tiers.** Tier 1 (control-node state) is live today. Tier 2 pulls service-CT
state into the same repo, enabled with a flag once its CT is up: **Postgres**
(a cluster-wide `pg_dumpall` streamed over SSH straight into the repo, tag
`zai-postgres`) with `postgres_enabled=true` in [`bin/zai-backup`](../bin/zai-backup).
The proxy CT needs no Tier-2 backup — its routes are in git and its cert in the
vault, so it holds no runtime state. See [`backup`](roles/backup.md).

> **Scope caveat — this is not yet disaster recovery.** The object store sits on
> the *same physical disk* as everything else, so today's backup guards
> **CT-level** loss (restore a clobbered service container) but **not whole-host**
> loss (dead disk, stolen box, fire). Closing that gap is a second, **off-site**
> restic target — a one-line backend addition, tracked in [TODO](#todo).

---

## Known gotchas

Hard-won lessons with `community.proxmox.proxmox` (the version bundled with
Debian 13's `ansible` 12). These will recur on the remaining service CTs:

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

On the **control node** itself:

- **`pct enter` is a non-login shell, so `/etc/profile.d` never loads.** Putting
  the repo's `bin/` on PATH via a `/etc/profile.d/zai-ops.sh` snippet alone left
  `zai-assign` "command not found" inside `pct enter 100` — that shell is
  interactive but *non-login*, and only login shells (and ssh) source
  `/etc/profile.d`. Fix: also source the snippet from `/etc/bash.bashrc`, which
  Debian's interactive *non-login* bash does read. Both the bootstrap seed and the
  `control_node` role install both hooks; the snippet case-guards `$PATH` so nested
  shells don't keep prepending. (Check which you're in: `shopt -q login_shell`.)

On the **Proxmox host** itself:

- **The "No valid subscription" nag comes back after every upgrade.** The popup
  is a client-side check in `proxmox-widget-toolkit`; a one-shot `sed` on
  `proxmoxlib.js` is undone the moment `apt full-upgrade` ships a fresh copy of
  that package. `bootstrap.sh` instead writes a tiny idempotent patcher
  (`/usr/local/sbin/pve-no-nag`, marker-guarded so re-runs are no-ops) and wires
  it as a dpkg `Post-Invoke` hook (`/etc/apt/apt.conf.d/00-zai-no-nag`), so the
  patch is re-applied automatically after any package operation that replaces the
  file. It restarts `pveproxy` only when it actually patches; hard-refresh the
  browser afterward to clear the cached JS.

Lessons on third-party apt repos under **Debian 13** (any CT):

- **Debian 13 verifies apt signatures with Sequoia (`sqv`), which rejects SHA1
  key self-signatures from 2026-02-01.** A third-party repo whose signing key is
  SHA1-bound (e.g. OpenResty) fails the apt update with `Sub-process /usr/bin/sqv
  returned an error code … not signed`, even though the signature is valid. Fix:
  drop an apt.conf so apt uses the classic `gpgv` verifier
  (`APT::Key::gpgvcommand "gpgv";`) — it checks the same signature but accepts
  SHA1 self-sigs, so authenticity is kept rather than disabled. **Install `gpgv`
  first** (Debian 13 ships none — sqv replaced it), in its own task before the
  override and before the third-party repo, or the override breaks every repo
  including the ones needed to install gpgv (`Cannot find gpgv`). No current role
  needs this — Caddy and Postgres use Debian's own repos — but it's kept here as a
  forward lesson for the next SHA1-bound third-party repo.

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

- **Move `proxmox_node_name` into runtime data.** The service-CT numbers and IPs
  are now runtime ([Service CTID assignment](#service-ctid-assignment)), so the
  committed tree is number-free — but `proxmox_node_name: alhambra` still pins one
  this-cluster fact in `group_vars/all/main.yml`. Finish the
  [ADR-0001](decisions/0001-repo-stays-generic.md) split by sourcing it from
  runtime data the way the CTID assignments now work.
- **Off-site backup target.** The [`backup`](#backups) job ships runtime state to
  the on-box object store (CT 105), which guards CT-level loss but not whole-host
  loss. Add a second restic target off the box (SFTP/B2/S3) so a dead host or
  lost site is recoverable — restic's backend is swappable, so this is a second
  repo in the same wrapper, not a rewrite.
