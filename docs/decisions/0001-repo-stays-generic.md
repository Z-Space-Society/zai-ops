# ADR-0001: The repo stays generic; cluster specifics are runtime data

Status: Accepted
Date: 2026-06-17

## Context

zai-ops is the infrastructure-as-code for the Z-Space AI Cluster, and the pilot
for COAI — a replicable model meant to stand up *other* clusters from the same
repo. The prime directive is full reproducibility: flash Proxmox, run one script,
and the stack rebuilds from this repo.

That goal is undermined if this-cluster facts get baked into the committed tree.
Concretely, the bare-metal inference nodes (salmon, orca, …) have names, IPs, and
per-node model choices that belong to *this* deployment, not to the generic
product. Hardcoding them would make the repo a snapshot of one site instead of a
blueprint for any.

The vault already establishes the pattern: the Proxmox API token is host-specific
and lives only on the control node, git-ignored, decrypted at runtime.

## Decision

The repo holds **generic automation** — roles, playbooks, and blueprint constants
that any cluster reuses (e.g. the internal `10.1.1.0/24` network and the CT-ID
layout). **This-cluster specifics** — which inference nodes exist, their
addresses, their models — live only as **runtime data on the control node**,
git-ignored, never committed.

For inference nodes this is `ansible/inventory/local.yml`, written by
`enroll-inference-node.yml` and loaded via the directory inventory alongside the
committed `inventory/hosts.yml`. It is treated like the vault: backed up as part
of the control node, so a rebuild is *repo + restored runtime data*.

The operator flow: `enroll-inference-node.yml` records a node (name/IP/model),
then `inference.yml` configures it.

## Scope / status

The split is **complete**. All this-cluster identity now lives in the git-ignored
runtime inventory (`inventory/local.yml`), written by per-fact setter playbooks
and merged with the committed blueprint via the directory inventory:

- inference roster — `enroll-inference-node.yml`
- service CTIDs (and the `10.1.1.{ctid}` IPs derived from them) — `assign.yml`
- `cluster_domain` — `set-domain.yml`
- `proxmox_node_name` — `set-node.yml` (recorded automatically by `bootstrap.sh`
  from the host's `hostname`)

Only genuine blueprint constants stay committed: the `10.1.1.0/24` net, the
`10.1.1.{ctid}` addressing *convention*, the `reserved_ctids` defaults, and each
service's create specs. The committed tree carries no node, no domain, and no
container numbers — the same blueprint stands up a cluster on any host.

## Consequences

Positive:
- The repo is a reusable blueprint, not a single-site snapshot — the COAI goal.
- One consistent home for runtime secrets/specifics (control node, backed up).
- Adding a node is a one-line enroll command; nothing to commit.

Negative / tradeoffs:
- A control-node backup is now load-bearing for full recovery (repo alone can't
  reconstruct the node roster). Backup mechanism is a tracked TODO.
- A fresh checkout can't see the live roster — intentional, but worth knowing.

## References

- [`docs/README.md`](../README.md) — Generic repo vs runtime data; Secrets & trust model
- `ansible/enroll-inference-node.yml`, `ansible/inference.yml`
