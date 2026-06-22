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

2. SSH in as root and run the host bootstrap script. Base Proxmox has no
   git, so fetch the single script directly with curl. This creates CT 100,
   the Ansible control node, fixes its locale, installs Ansible + this repo,
   and mints a Proxmox API token for Ansible (stored in an encrypted vault on
   the control node).

   ```bash
   bash -c "$(curl -fsSL https://raw.githubusercontent.com/Z-Space-Society/zai-ops/main/bootstrap.sh)"

   # to override the CT ID (default 100), pass it as an argument:
   # bash -c "$(curl -fsSL https://raw.githubusercontent.com/Z-Space-Society/zai-ops/main/bootstrap.sh)" _ 199
   ```

   The script prints a **vault password** on its last line. Back it up
   off-box — it's also stored on the control node at `/root/.vault_pass`.

3. Enter the control node and run the first Ansible playbook, which
   configures CT 100 itself, then verify the API token works.

   ```bash
   pct enter 100
   cd /opt/zai-ops/ansible
   ansible-playbook site.yml            # configure the control node
   ansible-playbook verify-proxmox.yml  # confirm the API token authenticates
   ```

4. Build the service containers. `provision.yml` creates each CT over the
   Proxmox API and then configures it over SSH. Drive one at a time with
   `--limit` while the stack comes online:

   ```bash
   ansible-playbook provision.yml --limit ct101-nginx   # create + configure CT 101
   ```

   From here Ansible uses the Proxmox API to create and configure all
   remaining containers.

5. Bring the bare-metal inference nodes (salmon, orca, …) into the cluster.
   Enrolling records the node in a git-ignored runtime inventory on the control
   node (names/IPs stay out of the repo); a second playbook configures it
   (NVIDIA driver + CUDA, then builds llama.cpp). Each node needs one-time prep
   first — Secure Boot off, an `ansible` user with sudo and CT 100's key. See
   [docs](docs/README.md#inference-nodes).

   ```bash
   ansible-playbook enroll-inference-node.yml -e "name=salmon ansible_host=192.168.6.63"
   ansible-playbook inference.yml --limit salmon
   ```

6. (Optional) Give a person a login. Pulls their public keys from
   `https://github.com/<user>.keys` and creates a same-named sudo account on the
   control node and every inference node. A temp password is printed; the user
   changes it on first login.

   ```bash
   ansible-playbook add-github-user.yml                  # adds jsayles
   ansible-playbook add-github-user.yml -e github_user=alice
   ```

## Networking

The bootstrap creates an isolated internal bridge `vmbr1` (`10.1.1.0/24`, no
uplink) and makes the host its NAT gateway (`10.1.1.1`), so service containers
can reach the internet for package installs without being exposed on the LAN.

- The control node (CT 100) sits at `10.1.1.100` and reaches every service at
  its static internal IP — no DHCP guessing.
- nginx (CT 101) is the only LAN-facing container: dual-homed on `vmbr0` (DHCP)
  for inbound traffic and `vmbr1` (`10.1.1.101`) to reach upstreams.
- The remaining services live on `vmbr1` only and route out through the host.

## Secrets

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

- `bootstrap.sh` — Host-level script to create CT 100 (the one host entry point)
- `apps/zai-auth/` — Django control app; ATProto-handle login (OIDC for Open WebUI) today, dashboard + Proxmox/Ansible control to come
- `ansible/`
  - `site.yml` — configures the control node (CT 100)
  - `verify-proxmox.yml` — checks the API token authenticates
  - `provision.yml` — creates the service containers over the API, then configures them
  - `enroll-inference-node.yml` — records a bare-metal inference node in the runtime inventory
  - `inference.yml` — configures inference nodes (NVIDIA/CUDA + llama-server)
  - `add-github-user.yml` — creates a human admin account from GitHub keys (CT 100 + inference nodes)
  - `inventory/` — committed blueprint (`hosts.yml`) + git-ignored runtime roster (`local.yml`)
  - `group_vars/all/` — shared vars (`main.yml`) and the encrypted `vault.yml`
  - `roles/` — `control_node`, `nginx`, `nvidia_cuda`, `llama_server`, and more as they come online

## Contributing

Work in feature branches. Nothing merges to `main` without review.
