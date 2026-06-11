# zai-ops

Infrastructure-as-code for the Z-Space AI Cluster (ZAI) — a local-first
shared AI infrastructure deployment at Z-Space, a coworking space in
Vancouver, BC.

The goal of this repo is full reproducibility: flash Proxmox onto any
compatible host, run the bootstrap script, and the full stack rebuilds
itself from this repo.

## How it works

1. Flash Proxmox onto the target host
2. SSH in and run the host bootstrap script — this creates CT 100, the
   Ansible control node
3. From CT 100, Ansible uses the Proxmox API to create and configure all
   remaining containers and inference nodes

## Principles

- No Docker on LXC service containers — all services run natively under
  systemd
- Inference nodes run llama-server only, nothing else
- The LiteLLM gateway owns all routing and policy
- This repo is the single source of truth for all infrastructure

## Structure

- `bootstrap/` — Host-level script to create CT 100
- `ansible/` — Roles and playbooks for all containers and inference nodes

## Contributing

Work in feature branches. Nothing merges to `main` without review.
