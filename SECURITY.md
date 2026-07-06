# Security posture

This is infrastructure-as-code for a single-tenant, local-first AI cluster
(and the pilot for COAI, a replicable version of the same model). This
document states the trust model deliberately, rather than leaving it as an
accident of implementation. See [ADR-0004](docs/decisions/0004-vault-trust-boundary.md)
for the full reasoning.

## Trust boundary: the control node, not the vault

The Proxmox API token is stored encrypted (Ansible Vault) in
`ansible/group_vars/all/vault.yml`, and the vault password lives at
`/root/.vault_pass` on the control node (CT 100), `0600`, so Ansible
auto-decrypts. **Vault encryption protects the git repository, not the
running box**: an accidental commit/push of `vault.yml` exposes ciphertext,
not the secret. It does not stop anyone who already has root on CT 100 from
reading the password file and decrypting the vault themselves.

That's intentional. This repo's architecture already treats **Proxmox host
root as game over by design** — host root can `pct exec` into any container,
including CT 100, regardless of vault posture. CT 100 is the real perimeter;
the vault's encryption-at-rest is a git-hygiene control, not a second
security boundary inside the box.

The same posture applies to `/root/.zai-secrets` on CT 100 (the
auto-generated object-store key and restic repository password): plaintext
on the box, `0600`, no manual entry required.

## Other controls in place

- **SSH**: service containers and inference nodes are reached via a root
  `ed25519` key generated on CT 100 and injected at create time — key-only
  login, no passwords.
- **API auth**: the Proxmox API token is scoped to a dedicated `ZaiProvision`
  role (`ansible@pve`), not `root@pam`.
- **Host key checking**: `ansible.cfg` uses trust-on-first-use
  (`StrictHostKeyChecking=accept-new`) with a pinned `known_hosts`, not
  `host_key_checking=False`.
- **Secrets never touch process argv**: `bootstrap.sh` pipes secrets over
  stdin into `pct exec`, since command-line arguments are readable via
  `/proc/<pid>/cmdline` for the life of the process.
- **Internal-only network**: all service containers except the edge proxy
  sit on `vmbr1` (`10.1.1.0/24`, no uplink) with no LAN exposure; the proxy is
  the only dual-homed, LAN-facing container.

## Upgrade path for stricter deployments

A Steward deployment handling client data may want a stronger posture than
the pilot default. This works today with **zero code changes** — it's an
operational switch:

1. Remove `vault_password_file` from `ansible/ansible.cfg`.
2. Run Ansible interactively with `--ask-vault-pass`; keep the password only
   in a password manager (never written to disk on the control node).

`bootstrap.sh` already prints the vault password once, for off-box backup, so
this mode has no bootstrap-side dependency.

## Reporting a vulnerability

Open a [private security advisory](https://github.com/Z-Space-Society/zai-ops/security/advisories/new)
on this repository, or contact the maintainer directly. Please don't open a
public issue for anything that could compromise a running deployment.
