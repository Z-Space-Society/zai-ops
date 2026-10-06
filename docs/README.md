# zai-ops documentation

Infrastructure-as-code for the Z-Space AI Cluster (ZAI). This directory is the
reference manual: how the cluster bootstraps itself, how it's wired, and what
each Ansible role does.

The guiding goal is **full reproducibility** — flash Proxmox onto a host, run
one script, and the stack rebuilds itself from this repo.

## Contents

- [Diagrams](diagrams.md) — topology, provision flow, and the member login path
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
- [Known gotchas](gotchas.md)
- [TODO](#todo)

---

## Bootstrap process

> **The whole path, end to end**, is drawn in [Diagrams → Build and provision
> flow](diagrams.md#build-and-provision-flow) — the five phases from bare host to
> steady state, and which orderings are real data dependencies.

The host bootstrap is [`host/bootstrap.sh`](../host/bootstrap.sh), run as root
on a freshly-flashed Proxmox host from the host's own clone of this repo.
Everything after the control node exists is driven by Ansible from inside it.
Base Proxmox has no git, so installing it is the one manual step (see
[ADR-0008](decisions/0008-host-scripts-from-host-clone.md) and
[Host scripts](#host-scripts)):

```bash
apt-get update; apt-get install -y git   # 401s from the enterprise repo are expected; step 1 disables it
git clone https://github.com/Z-Space-Society/zai-ops.git /root/zai-ops
/root/zai-ops/host/bootstrap.sh
# override the CT ID (default 100):
# /root/zai-ops/host/bootstrap.sh 199
```

What it does, in order (each phase prints a numbered banner):

1. **Configure apt repositories** — disable the enterprise/Ceph repos, add
   `pve-no-subscription`, update.
2. **Upgrade host packages** — `apt-get full-upgrade`.
3. **Suppress the subscription nag** — patch `proxmox-widget-toolkit` so the
   "No valid subscription" popup never fires, and install a dpkg post-invoke
   hook (`/etc/apt/apt.conf.d/00-zai-no-nag`) so the patch is re-applied after
   any upgrade that ships a fresh `proxmoxlib.js`. See [Known gotchas](gotchas.md).
4. **Create the internal network** — adds the `vmbr1` bridge (no uplink) with
   the host at `10.1.1.1`, plus a NAT/masquerade rule so internal-only CTs can
   reach the internet. See [Networking](#networking).
5. **Enable IPv4 forwarding** — persisted in `/etc/sysctl.d/99-zai-forward.conf`.
6. **Prepare the container template** — if no `debian-13-standard` amd64 template
   is on `local`, download the newest one in the `pveam` index. Resolved by
   pattern, not pinned (see [Known gotchas](gotchas.md)).
7. **Create the control node** — CT 100 (`ansible-control`), unprivileged,
   2 cores / 2 GB / 8 GB, `net0` on `vmbr0` (DHCP). Skipped if it already exists.
8. **Attach to the internal network** — give CT 100 a `vmbr1` NIC at `10.1.1.100`.
9. **Provision the control node** — fix the locale (must happen before Ansible
   can run at all), install `ansible`, `git` and `whiptail`, clone this repo to `/opt/zai-ops`,
   install the pinned Ansible collections (`community.proxmox >=1.6.0`, so
   `provision.yml` works before `site.yml` — the bundled 1.3.0 can't set the API
   timeout), and put the repo's [`bin/`](#operator-commands) on PATH (so a fresh
   `pct enter` can run `scn-config`/`zai-backup` before `site.yml` has run).
10. **Mint the Proxmox API token + vault** — create the `ansible@pve` user, the
   `ZaiProvision` role, and a token; write the credentials into an encrypted
   Ansible Vault on CT 100. See [Secrets & trust model](#secrets--trust-model).
11. **Record the Proxmox node name** — capture the host's `hostname` into CT 100's
   runtime inventory (`proxmox_node_name`, via `set-node.yml`) so `provision.yml`
   targets the right node. Nothing about the node is committed.

The script prints a **vault password** on its last line — back it up off-box.

After it finishes, continue inside the control node:

```bash
pct enter 100
scn-config                                      # work down the menu:
                                                #   1 Control Node Setup    site.yml, then verify-proxmox.yml
                                                #   2 Set Domain            the public base domain
                                                #   3 Set TLS               only if not acme, the default
                                                #   4 Container Assignment  each service its CTID
                                                #   5 Provision Containers  create + configure
cd /opt/zai-ops/ansible
ansible-playbook provision.yml --limit proxy    # or provision one by hand
```

---

## Architecture

> **Drawn in full** in [Diagrams → Cluster topology](diagrams.md#cluster-topology)
> — every CT by tier, the proxy's four public routes, and the service-to-service
> calls that stay on `vmbr1`. The prose below is the same picture in words.

- **CT 100** creates and configures every other container over the Proxmox API
  (create) and SSH (configure). It is the only machine that holds secrets.
- **CT 110 (proxy — Caddy)** is the only LAN-facing service; it reverse-proxies
  the internal services. Its routes are declarative in git ([`proxy`
  role](roles/proxy.md)) — rendered into a `Caddyfile` from `caddy_proxy_hosts`,
  not held in a UI database — so the CT holds nothing that needs backing up. Only
  one public hostname should point at a given proxy at a time (controlled at
  Cloudflare).
- **Every other CT** (postgres, redis, happyview, litellm, sync-relay, corliss,
  open-webui) lives only on the internal network and is reached through the
  proxy — except [`sync-relay`](roles/sync_relay.md), which has no route at all
  while it is in Phase A.
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
| `100`–`109` | Core infra   | control (100), object-store (101), postgres (102), [`redis`](roles/redis.md) (103) |
| `110`–`119` | Platform     | proxy/edge (110), registry (111, the [`happyview`](roles/happyview.md) role), gateway (112), sync relay (113, the [`sync_relay`](roles/sync_relay.md) role), PDS (114, the [`pds`](roles/pds.md) role) |
| `120`–`129` | Applications | [`corliss`](roles/corliss.md) (120), open-webui (121), … other user-facing apps |

What separates the last two tiers is **who talks to it**: platform CTs are
consumed by other services, application CTs are consumed by members. `corliss`
is an application despite being the thing that does authentication — it is also
the membership surface and the page a member lands on. `happyview` is the
inverse: no one signs in to it, it is the registry `corliss` reads.

"Consumed by members" means a surface a member **lands on**, not merely a port a
member's device opens a socket to. `litellm` and `sync_relay` are both
platform even though a member's own client connects to each of them directly,
authenticated as that member: neither has a page, neither has a login, and both
are reached *through* an application rather than being one. Reading the rule as
"do member devices talk to it" puts both in the wrong tier.

That rule does **not** separate core from platform, and reaching for it there
puts things in the wrong tier — `postgres` and `redis` are also "consumed by
services, not members", and both are core. What separates *those* two is what
core infra is: the **data foundations**, storage with no logic of its own.
Platform holds services *with* logic — the edge, the AppView, the gateway.

The dependency arrows point **downward** (apps → platform → core), and the line
between core and platform doubles as a trust line: the data foundations stay off
the LAN, while the only internet-facing box (the proxy) sits one tier out.

- The specific numbers above are this cluster's **assigned** layout, not committed
  identity — each is bound with `scn-config` and could differ on another host.
  What's fixed is the `10.1.1.{ctid}` convention and the tier ranges above. See
  [Service CTID assignment](#service-ctid-assignment).

---

## Generic repo vs runtime data

This repo is meant to rebuild *any* cluster, not just this one. The committed
tree holds only generic automation and blueprint constants; **this-cluster facts
live in the git-ignored `ansible/inventory/local.yml` on the control node**. The
inventory is loaded as a directory, so that file merges with the committed
`hosts.yml` automatically.

| Runtime fact | Written by |
| ------------ | ---------- |
| Service CTIDs | `scn-config` ([Service CTID assignment](#service-ctid-assignment)) |
| `cluster_domain` | `scn-config`, Set Domain ([Cluster domain](#cluster-domain)) |
| `caddy_tls_mode` | `scn-config`, Set TLS ([Cluster TLS mode](#cluster-tls-mode)) |
| `proxmox_node_name` | `bootstrap.sh` (from the host's `hostname`), `zai-set-node` |
| Membership-registry identity | `zai-set-registry` |
| Inference-node roster | [`enroll-inference-node.yml`](#inference-nodes) |

What stays committed is what every cluster shares: the `10.1.1.0/24` net, the
`10.1.1.{ctid}` addressing convention, and each service's create specs and
*suggested* `default_ctid`.

A control-node rebuild is therefore **repo + restored runtime data**, so
`local.yml` is backed up alongside the vault (see [Backups](#backups)). The
decision is pinned in [ADR-0001](decisions/0001-repo-stays-generic.md).

---

## Service CTID assignment

The blueprint names services generically (`proxy`, `litellm`, …). Which container
ID each one gets is decided per cluster with `scn-config`, on the control node:

```bash
scn-config                                 # the menu
scn-config nonint show-ctid                # service / CTID / default table
scn-config nonint assign-ctid-defaults     # give every unassigned service its default
scn-config nonint assign-ctid proxy 110    # assign one
```

The menu lists the unassigned services, each ticked and pre-filled with its
`default_ctid` from the blueprint. Untick any to leave for later, or change the
numbers before confirming. Nothing is created: the numbers are recorded in
`inventory/local.yml` by [`assign.yml`](#playbooks), which refuses a CTID that
is outside 100–999, in `reserved_ctids`, or already taken.

From then on every playbook resolves the service to `ctid` and a derived
`ansible_host` of `10.1.1.{ctid}`. `provision.yml --limit <service>` creates
exactly that CT, and fails fast if the service was never assigned. The menu's
**Provision Containers** entry runs that for each ticked service, one at a time
in dependency order (core, then platform, then apps), stopping at the first
failure. It asks Proxmox what exists first, marks each service `action: create` or
`action: rebuild`, and refuses a CTID held by a container that isn't that
service's.

**An assignment is set once.** The CTID is the container's VMID and its address,
so changing it after provisioning moves nothing: the next provision creates a
second, empty container, the old one keeps the data, and every other service
still points at the old address. The menu therefore shows assigned services as
locked. To correct a number *before* the service has been provisioned:
`scn-config nonint assign-ctid <service> <ctid> --reassign`. See
[ADR-0010](decisions/0010-scn-config.md).

**`reserved_ctids`** (in [`group_vars/all/main.yml`](../ansible/group_vars/all/main.yml))
lists CTIDs that are never handed out. The default is `[100]`, the control node.
On a **brownfield** host, widen it to every live CTID *before* assigning
anything, so no run can stomp a container the cluster didn't create.

> **Control-node exception.** The `10.1.1.{ctid}` convention is for **service
> containers only**. The control node's internal IP is pinned to `10.1.1.100` by
> `bootstrap.sh` regardless of its CTID (which may be 199 on a brownfield box), so
> it carries no `ctid` in the inventory and is never assigned.

### Cluster domain

The cluster's public base domain is the same category of data — per-cluster, not
committed identity — so it's set the same way, with `scn-config`'s **Set
Domain** entry, or scripted:

```bash
scn-config nonint show-domain                       # what is recorded
scn-config nonint set-domain zai.cascadia.design    # cluster_domain, cluster-wide
```

Both are a front end to [`set-domain.yml`](#playbooks). The playbook
validates the value is a plausible lowercase FQDN, then **read-modify-writes** the
same `inventory/local.yml` into `all.vars.cluster_domain` — so the CTID assignments
and inference roster sharing the file survive. Re-recording the current value is an
idempotent no-op. From then on `cluster_domain` resolves for every playbook;
later proxy routes build on it (e.g. `api.{{ cluster_domain }}`).

Changing a recorded domain changes nothing that is running: each service
renders its public URLs when it is provisioned, so every provisioned service
keeps the old addresses until it is provisioned again. The menu says so before
it saves.

### Cluster TLS mode

How the [`proxy`](roles/proxy.md) edge gets its certificate depends on what sits
in front of it, so it is per-cluster data too, set with `scn-config`'s **Set
TLS** entry, or scripted:

```bash
scn-config nonint show-tls                          # what is recorded, or the default
scn-config nonint set-tls acme ops@example.org      # explicit acme, optional Let's Encrypt contact
scn-config nonint set-tls none                      # external edge in front, or pre-DNS smoke test
scn-config nonint set-tls origin_ca                 # Cloudflare proxies the domain (needs the vault cert/key)
```

`acme` is the role default, so a cluster that never sets it is its own edge and
Caddy obtains and renews Let's Encrypt certs. The menu offers to provision the
proxy once a mode is saved. Both are a front end to
[`set-tls.yml`](#playbooks). The playbook validates that the mode is one of the
three, that an email comes only with `acme`, and that `origin_ca` has
`cloudflare_origin_cert` and `cloudflare_origin_key` in the vault. It then
**read-modify-writes** `all.vars.caddy_tls_mode` (and `caddy_acme_email`) in the
same `inventory/local.yml`, keeping the CTID assignments, inference roster and
`cluster_domain`. The email is rebuilt with the mode rather than merged, so
switching away from `acme`, or re-running `acme` without an email, removes a stale
contact. Re-recording the current setting is an idempotent no-op. Replay the proxy
to apply it.

The mode used to be inferred from the vault. An existing cluster must record its
mode after pulling that change and before its next proxy replay; see
[the migration table](roles/proxy.md#migration-record-the-mode-before-the-first-replay).

---

## Inference nodes

The inference nodes (salmon, orca, …) are **bare-metal Debian 13 machines** with
NVIDIA GPUs, sitting on the LAN/tailnet *outside* the Proxmox host. They run
`llama-server` only — no double duty, which is the vault's ADR-001 — and are
reached for inference by the LiteLLM gateway. They serve the chat (and
GPU-class) models; the *baseline embedding* model is **not** one of them — it runs
as an always-on CPU `llama-server` co-located in the `litellm` CT (see
[`litellm`](roles/litellm.md)), so embeddings survive any inference node being down.

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
| `site.yml`            | CT 100 (local) | Configure the control node (applies `control_node`). Run by `scn-config`'s Control Node Setup |
| `verify-proxmox.yml`  | CT 100 (local) | Read-only check that the API token authenticates. Run by Control Node Setup, after `site.yml` |
| `assign.yml`          | CT 100 (local) | Record service → CTID assignments in runtime inventory (the `scn-config` engine) |
| `set-domain.yml`      | CT 100 (local) | Record the cluster's public base domain in runtime inventory (the engine behind `scn-config`'s Set Domain) |
| `set-tls.yml`         | CT 100 (local) | Record the proxy's TLS mode, and the acme contact email, in runtime inventory (the engine behind `scn-config`'s Set TLS) |
| `set-node.yml`        | CT 100 (local) | Record the Proxmox node name in runtime inventory (the `zai-set-node` engine; `bootstrap.sh` calls it automatically) |
| `set-registry.yml`    | CT 100 (local) | Record the membership registry's per-cluster identity in runtime inventory (the `zai-set-registry` engine) |
| `set-smtp.yml`        | CT 100 (local) | Set, clear or show the cluster's outbound mail relay in `/root/.zai-secrets/smtp_url` (the engine behind `scn-config`'s Set SMTP). The URL is taken from the environment, never an argument |
| `provision.yml`       | CT 100 → API/SSH | Create service CTs over the API, then configure them |
| `ct-status.yml`       | CT 100 (local) | Read-only: list the containers on the node, so `scn-config` can mark each service as new or existing |
| `admins.yml`          | corliss (SSH) | Show, add or remove a cluster admin by running Corliss's `list_admins` / `make_admin` on its CT, or re-apply the roster to Corliss's own copy (`sync_admins`, `admin_action=apply`). The roster record is the authority; nothing is stored in zai-ops (the engine behind `scn-config`'s Cluster Admins) |
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
| `scn-config` | Menu-driven cluster configuration, in the order a new cluster needs it: configure the control node and check the API token, set the [domain](#cluster-domain) and the [TLS mode](#cluster-tls-mode), assign each service its CTID ([Service CTID assignment](#service-ctid-assignment)), provision the assigned services in dependency order, manage the cluster's admins, and set the outbound mail relay | [`assign.yml`](#playbooks), [`provision.yml`](#playbooks), [`admins.yml`](#playbooks), [`set-smtp.yml`](#playbooks), and `site.yml`, `verify-proxmox.yml`, `set-domain.yml`, `set-tls.yml` |
| `scn-config nonint <command>` | The same, scripted: `setup`; `show-domain`, `set-domain <domain>`; `show-tls`, `set-tls <acme\|none\|origin_ca> [email]`; `show-ctid`, `assign-ctid <service> <ctid>`, `assign-ctid-defaults`; `show-admins`, `add-admin <handle-or-did> [--admit] [--tier T]`, `remove-admin <handle-or-did>`, `apply-admins [corliss] [pds]` (gives the roster as it stands to everything that keeps a copy; all of them when none is named); `show-smtp`, `set-smtp` (URL on stdin), `clear-smtp` | the same playbooks |
| `zai-set-node <node>` | Record the Proxmox node name (bootstrap does this automatically) | [`set-node.yml`](#playbooks) |
| `zai-set-registry <key> <value>` | Record a membership-registry identity (`client_key`, `service_did`), read by [corliss](roles/corliss.md) | [`set-registry.yml`](#playbooks) |
| `zai-backup [run]` | Run the control-node backup (also the timer's `ExecStart`) | restic |
| `zai-backup <restic subcmd>` | Ad-hoc query/restore against the repo (`snapshots`, `check`, `restore …`) | restic |
| `zai-litellm-key create <name>` | Mint a per-person raw-API LiteLLM virtual key, printed once | litellm `/key/generate` |
| `zai-litellm-key list` | List virtual keys by alias/spend/budget/status | litellm `/key/list` |
| `zai-litellm-key revoke <name>` | Delete a virtual key by its alias | litellm `/key/delete` |

Deployed *service* tooling (the `garage` binary, `garage-init.sh`) is a different
category — it lives on its service CT, not the control node, and isn't an operator
command. Only control-node operator commands belong in `bin/`.

### Host scripts

Scripts that must run on the Proxmox host itself live in [`host/`](../host/), run
as root by path from the host's clone at `/root/zai-ops`. They are not on PATH.
The split from `bin/` is where the script runs: `bin/` drives Ansible from CT 100,
while `host/` does what CT 100 can't reach. CT 100 talks to Proxmox only through
the API token and has no SSH path to the host, so anything needing host accounts,
`pct` or `pveum` belongs here. The host's clone and CT 100's are independent, so
`git pull` on the host before running one. See
[ADR-0008](decisions/0008-host-scripts-from-host-clone.md).

| Script | Does |
| ------ | ---- |
| `host/bootstrap.sh [ctid]` | Build CT 100 and hand it the API token. See [Bootstrap process](#bootstrap-process) |
| `host/import-github-user.sh <user>...` | Create a sudo account on the host from each user's GitHub public keys. The host-side counterpart of [`add-github-user.yml`](roles/github_user.md). Import only: a re-run adds keys new on GitHub and never removes any |

---

## Roles

| Role                                       | Applied to | What it does                                            |
| ------------------------------------------ | ---------- | ------------------------------------------------------- |
| [`control_node`](roles/control_node.md)    | CT 100     | Base config for the Ansible control node                |
| [`proxy`](roles/proxy.md)                  | `proxy`    | Caddy reverse proxy — the LAN-facing edge; single apt package, git-tracked routes |
| [`nvidia_cuda`](roles/nvidia_cuda.md)      | inference nodes | NVIDIA driver + CUDA toolkit (bare-metal Debian 13) |
| [`llama_server`](roles/llama_server.md)    | inference nodes | Build llama.cpp (CUDA) + install the `llama-server` unit |
| [`github_user`](roles/github_user.md)      | CT 100 + inference nodes | Create a human admin account from GitHub public keys, with sudo. For the Proxmox host itself, see [Host scripts](#host-scripts) |
| [`object_store`](roles/object_store.md)    | `object-store` | Single-node Garage (S3-compatible) — the on-box backup target |
| [`postgres`](roles/postgres.md)            | `postgres` | PostgreSQL 17 (Debian-native) — the internal database server |
| [`redis`](roles/redis.md)                  | `redis`    | Redis (Debian-native) — the revocation store that lets Open WebUI invalidate an already-issued session JWT, so a corliss back-channel logout actually ends a chat session. **Once wired, it is a hard dependency of the whole chat surface, not just of logout** |
| [`corliss`](roles/corliss.md)            | `corliss` | ATProto→OIDC login bridge (Django, venv) — Postgres-backed, cloned from [Z-Space-Society/Corliss](https://github.com/Z-Space-Society/Corliss) at a pinned tag, fronted by Caddy at the **apex** domain; the sole identity provider for Open WebUI. Also serves the cluster console at `/manage/` — the cluster's only admin write surface since the `manage_console` SPA was deleted — reconciles its membership cache from the registry, and is where a non-member applies to join, the application written to the applicant's own PDS, never to the registry |
| [`litellm`](roles/litellm.md)              | `litellm`  | LiteLLM proxy (venv) — OpenAI-compatible gateway, Postgres-backed; + an always-on CPU floor embedder (`nomic-embed-text`) |
| [`open-webui`](roles/open-webui.md)        | `open-webui` | OpenWebUI chat UI (uv-managed Python 3.12 venv) — Postgres-backed, fronted by Caddy, talks to litellm for chat + RAG embeddings |
| [`happyview`](roles/happyview.md)          | `happyview` | HappyView AT Protocol AppView platform (Rust binary, built from source) — Postgres-backed, fronted by Caddy |
| [`sync_relay`](roles/sync_relay.md) | `sync-relay` | Automerge sync server (Rust binary, built from source) — the server end of the automerge-repo WebSocket protocol behind shared notes, Postgres-backed. **Deliberately has no Caddy route:** the Phase A build enforces no membership, so `vmbr1` is the entire access boundary. See [ADR-0007](decisions/0007-sync-relay-and-space-membership.md) |
| [`pds`](roles/pds.md)                      | `pds`      | In-house AT Protocol PDS (atproto-pds, Rust binary, built from source at a pinned revision): hosts the cluster's atproto accounts. SQLite on disk, no Postgres. Fronted by Caddy at `pds.<domain>` |
| [`backup`](roles/backup.md)                | CT 100     | restic + daily timer backing up runtime state to the object store |
| [`manifest`](roles/manifest.md)            | every service play (last task) | Writes `<service>.json` to Garage: installed version, zai-ops revision, timestamp. Read by Corliss's `/systems/`. See [ADR-0009](decisions/0009-service-manifests-in-garage.md) |

Adding a service? Follow [Adding a service](adding-a-service.md): a role is not
done until it writes a manifest and Corliss has a health check for it.

---

## Secrets & trust model

See [SECURITY.md](../SECURITY.md) for the full operator-facing statement of
this model, and [ADR-0004](decisions/0004-vault-trust-boundary.md) for the
decision record.

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
- The two [service manifest](roles/manifest.md) keys
  (`manifest_writer_*`, `manifest_reader_*`) follow the same pattern. The writer
  stays on CT 100; the reader is rendered into Corliss's env. Neither has any
  grant on `zai-backups`.
- corliss's break-glass local admin password (`corliss_admin_password`)
  follows the same auto-generated, `/root/.zai-secrets`-persisted pattern —
  it's **DR-critical**: the only way into corliss's `/admin/` if ATProto/OIDC
  login is ever broken. See [`roles/corliss.md`](roles/corliss.md#secrets).
- Open WebUI's scoped LiteLLM key (`openwebui_litellm_key`, the F1 fix) is
  persisted the same way, at `/root/.zai-secrets/openwebui_litellm_key` — but
  unlike every secret above, it isn't a `password`/`pipe` lookup: it can only
  be produced by calling litellm's live `/key/generate` API, so it's minted
  by an Ansible task, not a Jinja lookup. See
  [`roles/litellm.md`](roles/litellm.md#three-key-management-paths-kept-deliberately-separate).
- Corliss's LiteLLM provisioner key (`corliss_litellm_provisioner_key`) is
  minted by an Ansible task the same way, at
  `/root/.zai-secrets/corliss_litellm_provisioner_key`, and is the one place in
  the cluster outside CT 100 that holds an **admin-scoped** LiteLLM credential —
  Corliss mints and deletes members' API keys on their behalf, which `/key/*`
  will not let a plain virtual key do. It belongs to a `proxy_admin` *user*
  rather than being the master key, so a compromised Corliss costs one
  revocation instead of a proxy-wide rotation. What makes that defensible is on
  the Corliss side: no request ever chooses whose keys it acts on. See
  [`roles/corliss.md`](roles/corliss.md#secrets).
- CT 100 also holds `/etc/zai-litellm/admin.env` (`0600`, rendered by the
  `litellm` role) — the LiteLLM master key + API base that
  [`zai-litellm-key`](#operator-commands) uses to mint/list/revoke per-person
  raw-API keys. Same plaintext-on-CT-100 posture as everything else here; not
  generate-once state, just re-rendered every run.

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
# Object store is the restic backend, so it comes up first (assign it with
# scn-config if it has no CTID yet):
ansible-playbook provision.yml --limit object-store
ansible-playbook backup.yml
```

The backup is one command, [`zai-backup`](#operator-commands) — `zai-backup` runs
it (also what the timer fires), and any restic subcommand (`zai-backup snapshots`,
`zai-backup check`, `zai-backup restore …`) is forwarded against the repo. restic
is an implementation detail; operators never invoke it directly.

**Tiers.** Tier 1 (control-node state) is live today. Tier 2 pulls service-CT
state into the same repo: **Postgres** (a cluster-wide `pg_dumpall` streamed
over SSH straight into the repo, tag `zai-postgres`) is **on** —
`postgres_enabled=true` in [`bin/zai-backup`](../bin/zai-backup) — since
Postgres now holds unreproducible state with no other backup path: LiteLLM's
virtual keys/spend (`litellm`'s `STORE_MODEL_IN_DB`, including the per-person
raw-API keys `zai-litellm-key` mints) and Open WebUI's users/chats. Losing
the postgres CT without this tier means losing every issued API key and every
member's chat history, not just config.
The proxy CT needs no Tier-2 backup — its routes are in git and its cert in the
vault, so it holds no runtime state. See [`backup`](roles/backup.md).

> **Scope caveat — this is not yet disaster recovery.** The object store sits on
> the *same physical disk* as everything else, so today's backup guards
> **CT-level** loss (restore a clobbered service container) but **not whole-host**
> loss (dead disk, stolen box, fire). Closing that gap is a second, **off-site**
> restic target — a one-line backend addition, tracked in [TODO](#todo).

---

## Known gotchas

Problems that were debugged the hard way live in their own file:
**[gotchas.md](gotchas.md)**. Check it before chasing a Proxmox API, apt, PATH
or TLS oddity, and add to it when you learn a new one.

---

## TODO

- **Off-site backup target.** The [`backup`](#backups) job ships runtime state to
  the on-box object store (CT 105), which guards CT-level loss but not whole-host
  loss. Add a second restic target off the box (SFTP/B2/S3) so a dead host or
  lost site is recoverable — restic's backend is swappable, so this is a second
  repo in the same wrapper, not a rewrite.
