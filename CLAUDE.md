# CLAUDE.md

Guidance for Claude when working in this repo (zai-ops — infrastructure-as-code
for the Z-Space AI Cluster).

**Prime directive:** full reproducibility. Flash Proxmox, run one script, and
the stack rebuilds itself from this repo. Every change must preserve that —
nothing manual that isn't captured in `bootstrap.sh` or Ansible.

## Documentation maintenance (IMPORTANT)

The [`docs/`](docs/README.md) tree is the source of truth for how the cluster
works. **Keep it in sync in the same change that alters behavior** — never leave
docs for "later":

- **New/changed/removed role** → update `docs/roles/<role>.md` and the roles
  table in `docs/README.md`. For a new role, create its note from the pattern in
  the existing role docs (purpose · task-by-task table with the *why* · variables
  · dependencies · verify · notes).
- **New/changed playbook** → update the Playbooks table in `docs/README.md` (and
  the run steps in the top-level `README.md` if the operator flow changes).
- **Networking, bootstrap phases, addressing, or trust model change** → update
  the relevant section of `docs/README.md` (and the table/diagram).
- **A new gotcha learned the hard way** → add it to the "Known gotchas" section
  of `docs/README.md` so it isn't re-debugged.

When you finish a task, double-check whether any doc above needs the same edit.

## Key decisions (pinned)

These are settled. Don't silently reverse them; if a change requires it, call it
out and update the docs.

- **One host script, then Ansible.** `bootstrap.sh` (run as root on the Proxmox
  host) is the only host-level step. It builds CT 100, the Ansible control node;
  everything after is driven by Ansible *from inside CT 100*.
- **CT 100 builds the rest over the API.** Service containers are *created* via
  the Proxmox API (`community.proxmox.proxmox`) and *configured* over SSH. No
  host-side per-CT scripting.
- **Proxmox auth = API token, not root@pam.** User `ansible@pve`, role
  `ZaiProvision` (privsep 0). Token is sufficient for the full create lifecycle
  (confirmed) — do not fall back to root@pam.
- **Internal network on `vmbr1` (`10.1.1.0/24`, no uplink).** Host = `10.1.1.1`
  and is the NAT gateway for internal-only CTs. Addresses derive from the CTID
  (`10.1.1.{ctid}`, static); CTIDs follow a tiered convention — `100-109` core
  infra (control `.100`, object-store, postgres), `110-119` platform (proxy/edge,
  auth, gateway), `120-129` apps. `proxy` (Caddy) is the only LAN-facing CT
  (dual-homed on `vmbr0` + `vmbr1`); everything else is internal-only and routes
  out via the host.
- **SSH into service CTs via an injected key.** CT 100's root ed25519 public key
  is injected at create time; key-only root login. No per-CT passwords.
- **Secrets:** API token in `ansible/group_vars/all/vault.yml` (Ansible Vault,
  git-ignored). Vault password at `/root/.vault_pass` on CT 100 — host root is
  the trust boundary by design.
- **Non-secret shared vars** go in `ansible/group_vars/all/main.yml` (e.g.
  `proxmox_node_name`, `ct_rootfs_storage`) — never hardcode the node or storage
  in playbooks, and don't put non-secrets in the vault.
- **Native services, no Docker on LXC.** Service containers run under systemd
  directly. CTs are unprivileged with `features: [nesting=1]`.
- **Template:** `debian-13-standard` for all CTs. Template storage `local`,
  rootfs storage `local-lvm` (overridable via `ct_rootfs_storage`).
- **Inventory is data-driven.** Per-CT create specs (cores/memory/disk/netif)
  live on the host entry in `inventory/hosts.yml`; the create play reads them.
- **Operator commands live in `bin/`, run in place from git.** Things a human runs
  by hand (`zai-assign`, `zai-backup`) live in the repo's `bin/`, put on PATH via
  two hooks — `/etc/profile.d/zai-ops.sh` for login/ssh shells, and the same
  snippet sourced from `/etc/bash.bashrc` for the interactive *non-login* shell
  `pct enter` gives (profile.d alone leaves `zai-*` not-found there; see Known
  gotchas). Seeded by `bootstrap.sh` so a fresh `pct enter` works pre-Ansible,
  re-asserted by the `control_node` role. *Nothing* is copied to `/usr/local/bin`,
  so `git pull` is the whole update story (pull = live; no playbook replay).
  Name them for what they *do*, not the tool underneath (`zai-backup`, not
  `zai-restic`; restic is hidden behind a subcommand dispatch). Roles render only
  the secret/host-specific bits an in-git script can't carry (e.g. creds + IPs in
  `/etc/zai-backup/restic.env`), and symlink — not copy — committed systemd units
  so unit edits are live on pull too. This generalizes the same rule that keeps the
  blueprint number-free: the artifact is the repo, never a deployed copy.

## community.proxmox gotchas

Version bundled with Debian 13's `ansible` 12. These recur on every new CT:

- **Disk must be `storage:size`.** Use `disk: "local-lvm:8"`, never `disk: 8`
  with a separate `storage:` (renders a pathless rootfs → PVE rejects under
  token auth: "Only root can pass arbitrary filesystem paths").
- **Start tasks need `hostname`.** `state: started` with only `vmid` KeyErrors on
  `'name'` for freshly-created CTs ([issue #98]). Pass `hostname:` and add a
  small `retries`/`until` for the post-create race.
- **Systemd 257 wants nesting.** Create service CTs with `features: [nesting=1]`.

[issue #98]: https://github.com/ansible-collections/community.proxmox/issues/98

## Conventions

- **Idempotency is required.** Re-running `bootstrap.sh` or any playbook must be
  safe. Guard host-level edits; prefer modules over shell.
- **Validate before declaring done.** YAML must parse; proxy changes run
  `caddy validate`. Don't claim a play works without the `PLAY RECAP` showing
  `failed=0`.
- **Match the surrounding style** — comments explain *why*, not *what*.
