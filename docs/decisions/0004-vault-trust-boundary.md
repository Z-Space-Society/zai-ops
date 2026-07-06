# ADR-0004: Vault password co-located with the control node

Status: Accepted
Date: 2026-07-05

## Context

`bootstrap.sh` writes the Ansible Vault password to `/root/.vault_pass` on
CT 100, and `ansible.cfg` points `vault_password_file` at it so playbooks
auto-decrypt `group_vars/all/vault.yml` (the encrypted Proxmox API token). The
encrypted vault lives in the same container, at
`/opt/zai-ops/ansible/group_vars/all/vault.yml`.

Flagged by the Fable security review (Issue 3): the key sits beside the lock.
Vault encryption at rest protects exactly one thing — the git working tree, so
an accidental `git add`/push of `vault.yml` exposes ciphertext, not secrets
(and `.gitignore` already blocks committing it regardless). It does **not**
protect against anyone with access to CT 100, since decrypting requires only
reading `/root/.vault_pass` on the same box.

The realistic threat model here makes this mostly fine for the pilot: this
repo's architecture already treats **host root as game over by design** —
anyone with Proxmox host root can `pct exec` into CT 100 regardless of any
vault posture, so CT 100 is the real trust boundary, not the vault. But
because this repo is the COAI product template — meant to stand up other
Steward deployments, some of which may handle client data under a stricter
threat model — this needed to be a **documented decision**, not an unstated
default.

## Decision

Keep the vault password co-located with the control node
(`/root/.vault_pass`, mode `0600`, root-owned, asserted by the `control_node`
role) for the **pilot tier**. State the model explicitly in
[SECURITY.md](../../SECURITY.md) and here, rather than leaving it implicit:

- Vault encryption protects the **repo** (the git tree and its history), not
  the **box**.
- **CT 100 is the trust boundary.** Anyone with root on CT 100 — or root on
  the Proxmox host, which can always `pct exec` into CT 100 — already has
  everything the vault protects. This is consistent with the rest of the
  trust model (SSH keys injected at create time, the `ansible@pve` token
  scoped to a dedicated role, etc.).
- The same posture covers `/root/.zai-secrets` (the auto-generated
  object-store key and restic repo password) — plaintext on the box,
  `0600`, no manual entry, for the same reason.
- For deployments that need a stricter posture (e.g. a Steward handling
  client data), the upgrade path is **operational, not code**: remove
  `vault_password_file` from `ansible.cfg` and run interactively with
  `--ask-vault-pass`, keeping the password only in a password manager. This
  works today with zero code changes — `bootstrap.sh` already prints the
  password once, for off-box backup, regardless of which mode is used.

## Consequences

Positive:
- No surprise: the posture is written down, not an accident of where
  `bootstrap.sh` happened to write a file.
- A zero-cost stricter mode already exists for deployments that need it —
  nothing to build, just an operational switch.
- Consistent with the cluster's existing trust model (host root as the
  perimeter, not the vault).

Negative / tradeoffs:
- Anyone who compromises CT 100 — not just the Proxmox host — gets the
  Proxmox API token, full stop. Defense-in-depth here is limited to file
  permissions (`0600`) and container isolation (unprivileged LXC).
- Future Stewards must actively opt into the stricter mode; the default
  optimizes for pilot convenience, not maximum security.

## References

- [SECURITY.md](../../SECURITY.md) — the operator-facing security posture doc
- [`docs/README.md`](../README.md#secrets--trust-model) — Secrets & trust model
- `ansible/roles/control_node` — the `0600`/root assertion on `/root/.vault_pass`
- `ansible/ansible.cfg` — `vault_password_file` setting
- Fable Security Review, Issue 3 (Obsidian:
  `03_Projects/Z-Space AI/ZAI-Ops/Fable Security Review`)
