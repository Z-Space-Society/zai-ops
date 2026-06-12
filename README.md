# zai-ops

Infrastructure-as-code for the Z-Space AI Cluster (ZAI) — a local-first
shared AI infrastructure deployment at Z-Space, a coworking space in
Vancouver, BC.

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
   curl -fsSL https://raw.githubusercontent.com/Z-Space-Society/zai-ops/main/bootstrap.sh -o bootstrap.sh
   bash bootstrap.sh          # creates CT 100; pass a CTID to override
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

   From here Ansible uses the Proxmox API to create and configure all
   remaining containers and inference nodes.

## Secrets

The Proxmox API token lives in `ansible/group_vars/all/vault.yml`, encrypted
with Ansible Vault and git-ignored (it's host-specific and never committed).
Ansible decrypts it automatically via `/root/.vault_pass`. To view or edit:

```bash
ansible-vault edit group_vars/all/vault.yml
```

## Principles

- No Docker on LXC service containers — all services run natively under
  systemd
- Inference nodes run llama-server only, nothing else
- The LiteLLM gateway owns all routing and policy
- This repo is the single source of truth for all infrastructure

## Structure

- `bootstrap.sh` — Host-level script to create CT 100 (the one host entry point)
- `ansible/` — Roles and playbooks for all containers and inference nodes

## Contributing

Work in feature branches. Nothing merges to `main` without review.
