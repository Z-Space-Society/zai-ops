# Role: `object_store`

Installs and configures the cluster's S3-compatible object store. Today the
implementing daemon is [Garage](https://garagehq.deuxfleurs.fr/) (Deuxfleurs); the
role is named for the *function* so the daemon can change without a rename.

- **Source:** [`ansible/roles/object_store/`](../../ansible/roles/object_store/)
- **Applied by:** [`provision.yml`](../../ansible/provision.yml) (configure play, `hosts: object-store`)
- **Target:** the `object-store` CT (CT 105), over SSH, internal-only on `vmbr1`

## Purpose

Provides an on-box S3 endpoint for the [`backup`](backup.md) job's restic
repository, and holds the per-service manifests Corliss's `/systems/` reads (see
[`manifest`](manifest.md) and
[ADR-0009](../decisions/0009-service-manifests-in-garage.md)). It is **internal-only** — reached over `vmbr1`, never LAN-facing.
Single-node Garage: one replica, one zone.

> **Backup-scope caveat:** the store lives on the same physical disk as
> everything else, so it guards **CT-level** loss (restore a clobbered service
> CT) but **not whole-host** loss. An off-site restic target is the planned
> follow-up — see [`backup`](backup.md).

## Tasks

| Task | Module | Why |
| ---- | ------ | --- |
| Create `garage` group + user | `group`, `user` | Run the daemon unprivileged, no login shell. |
| Create metadata + data dirs | `ansible.builtin.file` | `/var/lib/garage/{meta,data}`, owned by `garage` (`0700`). |
| Install the garage binary | `ansible.builtin.get_url` | Static musl binary (no apt package). Optional SHA-256 pins integrity. Notifies `restart garage`. |
| Deploy the config | `template` → `/etc/garage.toml` | `0600 garage:garage` — carries `rpc_secret` + `admin_token`; Garage 2.x refuses to start if they're group/other-readable. Notifies `restart garage`. |
| Install the systemd unit | `template` → `/etc/systemd/system/garage.service` | Hardened (`ProtectSystem=strict`, `ReadWritePaths` = state dirs). Notifies reload + restart. |
| Ensure garage started + enabled | `ansible.builtin.systemd` | Running now + on boot. |
| Flush handlers | `meta: flush_handlers` | Bring the daemon up with the final config *before* cluster init runs. |
| Wait for the S3 API | `ansible.builtin.wait_for` (`127.0.0.1:3900`) | Don't init until the daemon answers. |
| Install + run the init script | `template` + `command` (`creates:`) | One-time: assign layout, import the key, create + grant the bucket. Idempotent via a sentinel. |
| Look up + import the manifest keys | `command` → `garage key info` / `garage key import` (`no_log`) | Two scoped keys for [service manifests](manifest.md): a writer for the manifest role, a read-only key for Corliss. Imported from generated secrets so a rebuild restores the same credentials. **Not** in `garage-init.sh`: its sentinel means additions there never run on an initialised cluster. |
| Look up + create the manifest bucket | `command` → `garage bucket info` / `garage bucket create` | `zai-manifests`, separate from `zai-backups` so neither manifest key can touch the restic repo. Converges every run. |
| Grant the manifest keys | `command` → `garage bucket allow` (`changed_when: false`) | Writer read+write (s3_object checks the existing object before a PUT), reader read-only. Re-allowing is a no-op in Garage. |
| Record the manifest | `include_role: manifest` (`garage`, `garage_version`) | After the bucket exists, so Garage's own manifest lands on the first run. See [`manifest`](manifest.md). |

### Handlers

| Handler | Action |
| ------- | ------ |
| `reload systemd` | `systemd: daemon_reload=true` |
| `restart garage` | `service: name=garage state=restarted` |

## Cluster init

`garage-init.sh` runs **once** (guarded by `creates: /var/lib/garage/.zai-initialized`):

1. **Layout** — assign this node a zone + capacity, apply as version 1.
2. **Key** — `garage key import` the vault-pinned credentials (rather than
   letting Garage generate random ones) so a rebuild restores the *same* key and
   restic keeps its repo.
3. **Bucket** — create `zai-backups` and grant the key read+write.

Credentials are passed via the environment (`ZAI_OS_ACCESS_KEY` /
`ZAI_OS_SECRET_KEY`), so the secret never lands in the on-disk script.

## Variables

Defined in [`defaults/main.yml`](../../ansible/roles/object_store/defaults/main.yml):

| Variable | Default | Meaning |
| -------- | ------- | ------- |
| `garage_version` | `v2.3.0` | Pinned release. Bump with the checksum. |
| `garage_binary_sha256` | *(pinned)* | SHA-256 of the binary; `get_url` fails on mismatch. Recompute on a bump. |
| `garage_metadata_dir` / `garage_data_dir` | `/var/lib/garage/{meta,data}` | On-disk state; the restic objects land in `data`. |
| `garage_capacity` | `20GB` | Capacity advertised to the layout (under the 32 GB rootfs). Decimal suffix. |
| `garage_region` | `garage` | S3 region; must match the `backup` role. |
| `object_store_bucket` | `zai-backups` | Bucket restic writes to. |
| `object_store_key_name` | `zai-backup` | Name of the imported access key. |
| `manifest_writer_key_name` / `manifest_reader_key_name` | `zai-manifest-writer` / `zai-manifest-reader` | The two scoped manifest keys. The bucket name, `manifest_bucket`, is in `group_vars/all/main.yml` because the manifest and corliss roles read it too. |

### Secrets (auto-generated — no manual step)

`garage_rpc_secret`, `garage_admin_token`, `object_store_access_key`,
`object_store_secret_key`, and the four `manifest_{writer,reader}_{access,secret}_key`
values are **generated on first run** by the `password`
lookups in [`group_vars/all/main.yml`](../../ansible/group_vars/all/main.yml) and
persisted under `/root/.zai-secrets` on CT 100. Re-runs reuse them, so the key
stays stable across rebuilds (restic keeps its repo). Because the lookup runs on
the control node, the `object_store` play (CT 105) and the [`backup`](backup.md)
play (CT 100) resolve to the *same* credentials. Nothing to paste into the vault.

## Verify

```bash
ssh root@10.1.1.105 'systemctl is-active garage'
ssh root@10.1.1.105 'garage status'          # one healthy node
ssh root@10.1.1.105 'garage bucket list'     # → zai-backups, zai-manifests
ssh root@10.1.1.105 'garage bucket info zai-manifests'   # writer RW, reader R
```

## Notes

- Ports: S3 API `3900` (the only one off-box, on `vmbr1`), RPC `3901` and admin
  `3903` are bound to localhost.
- The same `object_store_access_key` / `object_store_secret_key` feed restic in
  the [`backup`](backup.md) role — one credential, both sides.
- The manifest keys have no grant on `zai-backups`, and the backup key has none
  on `zai-manifests`. Manifests are not in any backup: a replay rebuilds them.
- For how the CT is created and reached, see the
  [main docs](../README.md#networking) and [`provision.yml`](../../ansible/provision.yml).
