# Role: `backup`

Schedules a daily backup of the cluster's unreproducible runtime state to the
[object store](object_store.md). The backup logic is the
[`zai-backup`](../../bin/zai-backup) operator command (in the repo's `bin/`); this
role only installs restic, renders the secret env, and wires up the systemd timer.

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
bucket, on a **daily systemd timer**. restic is an implementation detail —
operators talk to `zai-backup`, not to restic.

> **Scope caveat:** the object store is on-box, so this currently protects
> against **CT-level** loss, not whole-host loss. restic's backend is swappable —
> adding an off-site repo (SFTP, B2, S3) later is a second target in the same
> script, not a rewrite.

## Where things live (and what's live on `git pull`)

The backup deliberately splits **code/config** (in git, on PATH) from
**secrets/host values** (rendered once by this role):

| Thing | Lives in | Live on `git pull`? |
| ----- | -------- | ------------------- |
| Backup logic; paths, retention, postgres on/off | [`bin/zai-backup`](../../bin/zai-backup) config block | ✅ yes |
| Timer schedule | [`files/zai-backup.timer`](../../ansible/roles/backup/files/zai-backup.timer) (symlinked in) | ✅ after `systemctl daemon-reload` |
| Repo URL, repo password, S3 creds, postgres IP | `/etc/zai-backup/restic.env` (rendered, `0600`, git-ignored) | ❌ re-rendered by `backup.yml` — secrets can't be committed |

So everything you'd edit day-to-day is live on a pull. The only carve-outs are
secrets (never in git) and a `daemon-reload` when you change the *schedule*.

## Tasks

| Task | Module | Why |
| ---- | ------ | --- |
| Install restic | `ansible.builtin.apt` | Base package. |
| Create `/etc/zai-backup` | `ansible.builtin.file` (`0700`) | Holds the env file. |
| Render `restic.env` | `template` (`0600`) | Repo URL, repo password, S3 creds, postgres IP. Root-only. |
| Symlink service + timer | `ansible.builtin.file` (`state: link`) | Point `/etc/systemd/system` at the committed units, so unit edits + pull are live. Notifies `reload systemd`. |
| Enable the timer | `ansible.builtin.systemd` | Schedule on boot. |
| Run an initial backup | `ansible.builtin.command` (`zai-backup run`) | Fail the play now on bad creds / unreachable store, not silently at 03:00. |

### Handler

| Handler | Action |
| ------- | ------ |
| `reload systemd` | `systemd: daemon_reload=true` (pick up unit symlink changes) |

## The `zai-backup` command

One function-named command; the full run is its default verb (also what the
timer's `ExecStart` fires), and any other verb is passed straight to restic:

```bash
zai-backup              # run the backup: init-if-needed → backup → prune
zai-backup snapshots    # list snapshots
zai-backup check        # verify repository integrity
zai-backup restore …    # restore — any restic subcommand works
```

A run does: `restic init` if the repo is fresh (probed with `restic cat config`)
→ `restic backup --tag zai-control-node` over the always-present paths plus any
optional path that exists → optional Tier-2 `pg_dumpall` → `restic forget --prune`
with the retention in the script.

## Variables

Defined in [`defaults/main.yml`](../../ansible/roles/backup/defaults/main.yml) —
trimmed to what's needed to render the env and run the timer. The backup
*behaviour* (paths, retention, postgres toggle, schedule) lives in git, not here:

| Variable | Default | Meaning |
| -------- | ------- | ------- |
| `backup_s3_endpoint` | `http://{{ object-store IP }}:3900` | Read from inventory, not duplicated. |
| `backup_s3_bucket` / `backup_s3_region` | `zai-backups` / `garage` | Must match the object store. |
| `backup_repository` | `s3:…/zai-backups` | What restic opens. |
| `backup_run_now` | `true` | Run once at the end of the play. |

Behaviour knobs (edit + `git pull`): `backup_paths` / retention /
`postgres_enabled` in [`bin/zai-backup`](../../bin/zai-backup); the schedule
(`OnCalendar`) in [`files/zai-backup.timer`](../../ansible/roles/backup/files/zai-backup.timer).

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
# On CT 100 (zai-backup is on PATH):
systemctl list-timers zai-backup.timer
zai-backup                                         # manual run
zai-backup snapshots

# Restore the latest snapshot into a staging dir, then copy paths back:
zai-backup restore latest --target /tmp/restore
```

## Notes

- Secrets live only in `restic.env` (`0600`) and the vault — never in the repo.
- **Tier 2 (service data)** — service-CT state pulled into the *same* restic repo.
  (The proxy CT needs no Tier-2 backup: its routes live in git and its TLS cert in
  the vault, so it holds no unreproducible state.)
  - **Postgres** — wired. Set `postgres_enabled=true` in
    [`bin/zai-backup`](../../bin/zai-backup) once the `postgres` CT is up (assign
    its CTID first, then re-run `backup.yml` once so `ZAI_POSTGRES_HOST` lands in
    `restic.env`; after that the toggle is a pull-to-live edit). A run then streams
    a cluster-wide `pg_dumpall --clean --if-exists` over SSH straight into the
    restic repo via `--stdin` (tag `zai-postgres`) — no dump file on disk, on
    either box. **Restore:** `zai-backup restore latest --target /tmp/restore` (or
    `--tag zai-postgres`), then `psql -f /tmp/restore/pg_dumpall.sql` as the
    `postgres` superuser on the CT.
- For the trust model behind backing up the vault password, see the
  [main docs](../README.md#secrets--trust-model).
