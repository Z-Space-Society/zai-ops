# Role: `control_node`

Base configuration for the Ansible control node (CT 100, `ansible-control`).

- **Source:** [`ansible/roles/control_node/`](../../ansible/roles/control_node/)
- **Applied by:** [`site.yml`](../../ansible/site.yml) (`hosts: control_node`, `become: true`)
- **Target:** CT 100, over a local connection

## Purpose

The host bootstrap gets CT 100 to the point where Ansible can run (locale,
Ansible, repo clone, vault). This role takes over from there and makes the
control node idempotently re-configurable: it re-asserts the locale, brings the
system up to date, installs the Proxmox API client, and prepares the SSH key
used to reach service containers.

## Tasks

| Task | Module | Why |
| ---- | ------ | --- |
| Ensure the `en_US.UTF-8` locale is generated | `community.general.locale_gen` | Re-asserted **before** the upgrade so a `glibc`/`locales` upgrade regenerates `locale-archive` with the locale present instead of wiping it. |
| Update apt cache and run a full upgrade | `ansible.builtin.apt` (`upgrade: full`) | Keep the control node current. |
| Install the Proxmox API client library | `ansible.builtin.apt` (`python3-proxmoxer`, `python3-requests`) | `community.proxmox` modules talk to the API through `proxmoxer`. |
| Ensure root has an ed25519 SSH keypair | `ansible.builtin.user` (`generate_ssh_key`) | The public key is injected into each service CT at create time (the proxmox module's `pubkey`) so Ansible can SSH in afterward. Idempotent — only generates if absent. |
| Assert the vault password file is root-only | `ansible.builtin.file` (`mode: 0600`) | Defense-in-depth: catches permission drift on `/root/.vault_pass`. The bootstrap already writes it with `umask 077`. |
| Put the repo's `bin/` on PATH | `ansible.builtin.copy` (`/etc/profile.d/zai-ops.sh`) | Make the operator commands (`zai-assign`, `zai-backup`, …) discoverable as `zai-*` for interactive shells. They run in place from git — nothing is copied to `/usr/local/bin`, so `git pull` is enough to update them. |

## Variables

None. The role is intentionally parameter-free — it configures the one,
well-known control node.

## Dependencies

- Collections: `community.general` (`locale_gen`).
- Assumes the bootstrap has already run (locale fixed, repo cloned, vault
  written).

## Notes

- **Order matters:** the locale task runs before the upgrade on purpose (see the
  table). Don't reorder.
- **Operator commands live in [`bin/`](../../bin/), run in place.** `zai-assign`,
  `zai-backup`, … are named for what they *do*, not the tool underneath; this role
  only puts the directory on PATH. Nothing is installed to `/usr/local/bin`, so the
  command you run is always the one in git — `git pull` updates them with no replay.
- The generated key lives at `/root/.ssh/id_ed25519`; the create play reads
  `/root/.ssh/id_ed25519.pub` via a `lookup('file', ...)`.
- See the [Secrets & trust model](../README.md#secrets--trust-model) for why the
  vault password lives on the box.
