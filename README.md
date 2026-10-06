# zai-ops

The control node for the Z-Space AI Cluster (ZAI) — both the
infrastructure-as-code that builds a cluster and the control app that operates
it. A local-first shared AI infrastructure deployment at Z-Space, a coworking
space in Vancouver, BC.

The goal of this repo is full reproducibility: flash Proxmox onto any
compatible host, run the bootstrap script, and the full stack rebuilds
itself from this repo.

## How it works

1. Flash Proxmox onto the target host.

2. SSH in as root, install git, clone this repo onto the host, and run the host
   bootstrap script from that clone. Base Proxmox has no git, so installing it
   is the one manual step. The bootstrap creates CT 100, the Ansible control
   node, fixes its locale, installs Ansible + this repo, and mints a Proxmox API
   token for Ansible (stored in an encrypted vault on the control node). See
   [ADR-0008](docs/decisions/0008-host-scripts-from-host-clone.md).

   ```bash
   apt-get update; apt-get install -y git   # 401s from the enterprise repo are expected; the bootstrap disables it
   git clone https://github.com/Z-Space-Society/zai-ops.git /root/zai-ops
   /root/zai-ops/host/bootstrap.sh
   ```

   to override the CT ID (default 100), pass it as an argument:

   ```bash
   /root/zai-ops/host/bootstrap.sh 199
   ```

   The script prints a **vault password** on its last line. Back it up
   off-box — it's also stored on the control node at `/root/.vault_pass`.

3. Enter the control node, configure CT 100 itself, verify the API token, and
   record this cluster's identity: the public base domain and the membership
   registry.

   ```bash
   pct enter 100
   scn-config    # the menu; its first two entries, in order:
                 #   1 Control Node Setup  site.yml, then verify-proxmox.yml
                 #   2 Cluster Settings    the domain and the membership registry
   ```

   - **Control Node Setup** runs `site.yml` (configure CT 100) and then
     `verify-proxmox.yml` (confirm the API token authenticates), stopping if
     the first fails. Scripted: `scn-config nonint setup`.
   - **Cluster Settings** lists four values with what is recorded for each:
     - **Proxmox host name**: `bootstrap.sh` already recorded it from the
       host's `hostname`. Change it only to correct it, for example after
       renaming the host.
     - **Domain**: required before provisioning the proxy. Its Caddy routes
       are built from `cluster_domain`, and every service's public URL
       (`owui.`, `api.`, `view.`, …) derives from it, so setting it once moves
       them all together. Scripted: `scn-config nonint set-domain example.com`.
     - **Registry service DID**: the account whose repo holds the public admin
       roster. Provisioning succeeds without it and nobody is an admin, so it
       is the one to check when admin links don't appear. Scripted:
       `scn-config nonint set-registry service_did did:plc:…`.
     - **Registry client key**: the registry's public HappyView client key.
       Optional. Scripted: `scn-config nonint set-registry client_key hvc_…`.
       [corliss](docs/roles/corliss.md) reads both registry values.

   All four are stored in git-ignored runtime state
   ([`inventory/local.yml`](docs/README.md#cluster-settings)), which is what
   keeps the committed tree free of this cluster's identity.

   Two settings further down the menu matter before you provision:

   - **Set SMTP**: the outbound mail relay. It is a secret, so it is kept out
     of that file. Set it before provisioning the PDS, or the PDS runs with
     mail off. See [Outbound email](docs/roles/pds.md#outbound-email-smtp).
   - **Set TLS**: how the proxy gets its certificate. Skip it and the cluster
     is its own edge (`acme`: Caddy obtains and renews Let's Encrypt certs, so
     public `:80` and `:443` must reach the proxy CT). Choose `none` to stand
     the proxy up HTTP-only before DNS exists or behind another edge.
     Scripted: `scn-config nonint set-tls <mode>`. See
     [TLS modes](docs/roles/proxy.md#tls-modes).

4. Build the service containers in two passes: **assign** every service its
   container ID, then **provision** them.

   ```bash
   scn-config    # Container Assignment: accept the defaults (or untick and renumber), then Assign
   ```

   Assigning only records numbers in git-ignored runtime state; nothing is
   created. Assign every service before provisioning any, because services
   reference each other's addresses (the proxy skips the route of any service
   that has no CTID yet, so a service assigned later needs a proxy re-run). The defaults follow the tier convention: **100–109 core
   infra, 110–119 platform, 120–129 applications** (see
   [Networking](docs/README.md#networking)).

   An assignment is **set once**. The menu will not change one, because
   renumbering does not move a provisioned container. The scripted equivalent of
   accepting the defaults is `scn-config nonint assign-ctid-defaults`; see
   [Service CTID assignment](docs/README.md#service-ctid-assignment).

   Then provision. `scn-config`'s **Provision Containers** entry runs the
   commands below for the services you tick, in this order, stopping at the
   first failure. By hand:

   ```bash
   cd /opt/zai-ops/ansible
   # provision each — create over the API, configure over SSH.
   #    object store first: it's the restic backend the backup job writes to.
   #    postgres before happyview/litellm/sync-relay/corliss/open-webui —
   #    each of those roles creates its own role + database on the postgres CT.
   ansible-playbook provision.yml --limit object-store
   ansible-playbook provision.yml --limit postgres
   ansible-playbook provision.yml --limit redis   # before open-webui, which
                                                  #   builds its REDIS_URL from it
   ansible-playbook provision.yml --limit proxy
   ansible-playbook provision.yml --limit happyview
   ansible-playbook provision.yml --limit litellm
   ansible-playbook provision.yml --limit sync-relay # ~15-30 min: builds
                                                  #   from source on a cold CT
   ansible-playbook provision.yml --limit pds        # ~15-30 min: builds
                                                  #   from source on a cold CT
   ansible-playbook provision.yml --limit corliss
   ansible-playbook provision.yml --limit open-webui
   ```

   (`scn-config`, `zai-backup`, … are operator commands in the repo's
   [`bin/`](bin/), on `PATH` on the control node. They run in place from git, so
   a `git pull` updates them.)

5. Turn on backups. The control node backs up the unreproducible runtime state
   to the object store on a daily timer. The backup is one command, `zai-backup`:

   ```bash
   ansible-playbook backup.yml          # install the daily timer + run once now
   zai-backup                           # run a backup by hand (what the timer fires)
   zai-backup snapshots                 # list snapshots (any restic subcommand works)
   zai-backup check                     # verify repository integrity
   ```

   Each run captures the control-node state (Tier 1) and a cluster-wide
   `pg_dumpall` from the postgres CT (Tier 2). See
   [Backups](docs/README.md#backups).

6. Bring the bare-metal inference nodes (salmon, orca, …) into the cluster.
   Enrolling records the node in a git-ignored runtime inventory on the control
   node (names/IPs stay out of the repo); a second playbook configures it
   (NVIDIA driver + CUDA, then builds llama.cpp). Each node needs one-time prep
   first — Secure Boot off, an `ansible` user with sudo and CT 100's key. See
   [docs](docs/README.md#inference-nodes).

   ```bash
   ansible-playbook enroll-inference-node.yml -e "name=salmon ansible_host=192.168.6.63"
   ansible-playbook inference.yml --limit salmon
   ```

7. (Optional) Give a person a login. Pulls their public keys from
   `https://github.com/<user>.keys` and creates a same-named sudo account on the
   control node and every inference node. A temp password is printed; the user
   changes it on first login.

   ```bash
   ansible-playbook add-github-user.yml                  # adds jsayles
   ansible-playbook add-github-user.yml -e github_user=alice
   ```

   Ansible doesn't reach the Proxmox host itself. For a login there, run the host
   script as root from the host's clone. It takes one or more users, and a re-run
   only adds keys that are new on GitHub:

   ```bash
   /root/zai-ops/host/import-github-user.sh jsayles bmann
   ```

## Networking

The bootstrap creates an isolated internal bridge `vmbr1` (`10.1.1.0/24`, no
uplink) and makes the host its NAT gateway (`10.1.1.1`), so service containers
can reach the internet for package installs without being exposed on the LAN.

- The control node (CT 100) sits at `10.1.1.100` and reaches every service at
  its static internal IP — no DHCP guessing.
- proxy (Caddy) is the only LAN-facing container: dual-homed on `vmbr0` (DHCP)
  for inbound traffic and `vmbr1` (`10.1.1.110`) to reach upstreams.
- The remaining services live on `vmbr1` only and route out through the host.

## Secrets

See [SECURITY.md](SECURITY.md) for the full trust model (why the vault
password lives on the same box as the vault, and the upgrade path for
stricter deployments).

The Proxmox API token lives in `ansible/group_vars/all/vault.yml`, encrypted
with Ansible Vault and git-ignored (it's host-specific and never committed).
Ansible decrypts it automatically via `/root/.vault_pass`. To view or edit:

```bash
ansible-vault edit group_vars/all/vault.yml
```

## Documentation

Full reference docs live in [`docs/`](docs/README.md) — the bootstrap process,
architecture, networking, and a note for every role.

## Principles

- No Docker on LXC service containers — all services run natively under
  systemd
- Inference nodes run llama-server only, nothing else
- The LiteLLM gateway owns all routing and policy
- This repo is the control node: the single source of truth for the cluster's
  infrastructure and its operating control app

## Structure

- `host/`: scripts run as root on the Proxmox host, from its clone at `/root/zai-ops`
  - `bootstrap.sh`: creates CT 100 (the host entry point)
  - `import-github-user.sh`: creates a sudo account on the host from GitHub keys
- `ansible/`
  - the playbooks, listed in the [Playbooks table](docs/README.md#playbooks)
  - `inventory/` — committed blueprint (`hosts.yml`) + git-ignored runtime roster (`local.yml`)
  - `group_vars/all/` — shared vars (`main.yml`) and the encrypted `vault.yml`
  - `roles/`: one per service, each with a note in [`docs/roles/`](docs/roles/)
- `bin/`: operator commands run on CT 100 (`scn-config`, `zai-backup`, `zai-litellm-key`)

No application source lives here. The control app (ATProto-handle login, OIDC
for Open WebUI) is [Z-Space-Society/Corliss](https://github.com/Z-Space-Society/Corliss);
the `corliss` role clones it onto its CT at the tag pinned in
[`defaults/main.yml`](ansible/roles/corliss/defaults/main.yml), like every other
role installs a pinned release. See
[ADR-0006](docs/decisions/0006-corliss-standalone-apex.md).

## Contributing

Work in feature branches. Nothing merges to `main` without review.
