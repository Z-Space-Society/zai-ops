# Role: `backup`

Installs [restic](https://restic.net/) on the control node and schedules a daily
backup of the cluster's unreproducible runtime state to the
[object store](object_store.md).

- **Source:** [`ansible/roles/backup/`](../../ansible/roles/backup/)
- **Applied by:** [`backup.yml`](../../ansible/backup.yml) (`hosts: control_node`)
- **Target:** CT 100 (local connection)

## Purpose

Full recovery is meant to be **repo + restored state**: reflash, run
`bootstrap.sh`, restore the runtime state, re-run Ansible. This role backs up
that state so the "restore" half exists:

| Path | Why it can't be rebuilt |
| ---- | ----------------------- |
| `group_vars/all/vault.yml` | The Proxmox API token (encrypted). |
| `/root/.vault_pass` | Decrypts the vault; host root is the trust boundary. |
| `/root/.ssh/id_ed25519{,.pub}` | The key injected into every service CT — changing it locks Ansible out. |
| `/root/.zai-secrets` | Auto-generated secrets (object-store key, restic repo password) — a fresh CT 100 would regenerate different ones. |
| `inventory/local.yml` *(if present)* | The inference-node roster; git-ignored runtime data. |

It uses **restic** (encrypted, deduplicated, snapshotted) against the Garage S3
bucket, on a **daily systemd timer**.

> **Scope caveat:** the object store is on-box, so this currently protects
> against **CT-level** loss, not whole-host loss. restic's backend is swappable —
> adding an off-site repo (SFTP, B2, S3) later is a second target in the same
> wrapper, not a rewrite.

## Tasks

| Task | Module | Why |
| ---- | ------ | --- |
| Install restic | `ansible.builtin.apt` | Base package. |
| Create `/etc/zai-backup` | `ansible.builtin.file` (`0700`) | Holds the env file. |
| Deploy `restic.env` | `template` (`0600`) | Repo URL, repo password, S3 creds. Root-only. |
| Install `zai-backup.sh` | `template` (`0700`) | The backup wrapper (init-if-needed → backup → prune). |
| Install service + timer | `template` | `oneshot` service run by a daily timer. Notifies `reload systemd`. |
| Enable the timer | `ansible.builtin.systemd` | Schedule on boot. |
| Run an initial backup | `ansible.builtin.command` | Fail the play now on bad creds / unreachable store, not silently at 03:00. |

### Handler

| Handler | Action |
| ------- | ------ |
| `reload systemd` | `systemd: daemon_reload=true` (pick up unit changes) |

## What the wrapper does

`zai-backup.sh` (sourced from `restic.env`):

1. `restic init` if the repo is fresh (probed with `restic cat config`).
2. `restic backup --tag zai-control-node` over the always-present paths plus any
   optional path that exists.
3. `restic forget --prune` with the retention below.

## Variables

Defined in [`defaults/main.yml`](../../ansible/roles/backup/defaults/main.yml):

| Variable | Default | Meaning |
| -------- | ------- | ------- |
| `backup_paths` | vault, vault pass, SSH key | Always backed up. |
| `backup_optional_paths` | `inventory/local.yml` | Included only if present. |
| `backup_s3_endpoint` | `http://{{ object-store IP }}:3900` | Read from inventory, not duplicated. |
| `backup_s3_bucket` / `backup_s3_region` | `zai-backups` / `garage` | Must match the object store. |
| `backup_keep_daily/weekly/monthly` | `7 / 4 / 6` | restic retention. |
| `backup_oncalendar` | `*-*-* 03:00:00` | Timer schedule (+ 15 min jitter). |
| `backup_run_now` | `true` | Run once at the end of the play. |
| `backup_postgres_enabled` | `false` | Tier-2 seam; inert until Postgres exists. |

### Secrets (auto-generated — no manual step)

`restic_repo_password` and the `object_store_access_key`/`object_store_secret_key`
(the same key the [object store](object_store.md) imported) are generated on
first run and persisted under `/root/.zai-secrets` — see
[`group_vars/all/main.yml`](../../ansible/group_vars/all/main.yml). Nothing to
enter by hand.

> **DR note:** the repo is encrypted with `restic_repo_password`, which lives in
> `/root/.zai-secrets` *and* inside the repo. To restore after losing CT 100 you
> need that password out-of-band — escrow `/root/.zai-secrets` off-box, the same
> way the vault password is backed up at bootstrap. An on-box CT-level restore
> just needs the directory put back before re-running Ansible.

## Run

```bash
# Object store must exist first (it's the restic backend):
ansible-playbook provision.yml --limit object-store
ansible-playbook backup.yml
```

## Verify / restore

```bash
# On CT 100:
systemctl list-timers zai-backup.timer
/usr/local/bin/zai-backup.sh                       # manual run
source /etc/zai-backup/restic.env && restic snapshots

# Restore the latest snapshot into a staging dir, then copy paths back:
restic restore latest --target /tmp/restore
```

## Notes

- Secrets live only in `restic.env` (`0600`) and the vault — never in the repo.
- **Tier 2 (service data)** — once the Postgres CT is online, set
  `backup_postgres_enabled: true` and wire the `pg_dump`-over-SSH block in
  `zai-backup.sh.j2`; it streams into the *same* restic repo.
- For the trust model behind backing up the vault password, see the
  [main docs](../README.md#secrets--trust-model).
