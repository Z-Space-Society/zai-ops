# Role: `postgres`

Installs [PostgreSQL](https://www.postgresql.org/) **natively** as the cluster's
internal database server, backing the other services (litellm, open-webui).

- **Source:** [`ansible/roles/postgres/`](../../ansible/roles/postgres/)
- **Applied by:** [`provision.yml`](../../ansible/provision.yml) (configure play, `hosts: postgres`)
- **Target:** the `postgres` CT (whatever CTID it was assigned), over SSH, internal-only on `vmbr1`

## Purpose

A **bare** Postgres server: it stands up the cluster, listens on its internal IP,
and opens the internal subnet to `scram-sha-256` password auth — but it creates
**no application databases or roles**. Each consuming service provisions its own
database and credentials in its own role; those plays must run **after** this one
so `password_encryption` is already `scram-sha-256` when a role password is set
(otherwise the hash won't match the `scram-sha-256` HBA rule and auth fails).

**Debian-native, no PGDG repo.** Debian 13 (trixie) ships PostgreSQL 17 in its
own repo, so the role just `apt install`s `postgresql-17`. That deliberately
avoids the third-party apt-key / Sequoia `sqv` gotcha that any SHA1-bound
third-party apt repo hits on Debian 13 (see
[main docs](../README.md#known-gotchas)).

## Tasks

| Task | Module | Why |
| ---- | ------ | --- |
| Install PostgreSQL | `apt` | `postgresql-{{ postgres_version }}` from Debian; auto-creates the `main` cluster and a stock `postgresql.conf` that already has `include_dir = 'conf.d'`. |
| Configure listen + encryption | `template` → `conf.d/zz-zai.conf` | `listen_addresses` (internal IP) + `password_encryption`. `zz-` prefix wins over the stock file. Notifies **restart** (listen change needs a full restart). |
| Deploy `pg_hba.conf` | `template` (`0640 postgres:postgres`) | Full-file auth policy — keeps the local `peer` lines and adds the internal-subnet `scram-sha-256` rule. Notifies **reload** (SIGHUP). |
| Start + enable | `ansible.builtin.service` | Running now + on boot. |
| Flush handlers | `meta: flush_handlers` | Bring the server up with the final config *before* validating. |
| Verify listener | `command: pg_isready -h {{ ansible_host }}` | Proves `listen_addresses` took effect on the internal IP (the feature shipped). |
| Assert HBA parses | `command` → `pg_hba_file_rules` | Fail on a malformed rule — the pg_hba analogue of `caddy validate`. |

### Handlers

| Handler | Action |
| ------- | ------ |
| `restart postgresql` | `service: name=postgresql state=restarted` (for `listen_addresses`). |
| `reload postgresql` | `service: name=postgresql@{{ postgres_version }}-main state=reloaded` — the **instance** unit, not the `postgresql` wrapper (a oneshot whose reload is a no-op, which would silently skip the `pg_hba.conf` reload). |

## Variables

Defined in [`defaults/main.yml`](../../ansible/roles/postgres/defaults/main.yml):

| Variable | Default | Meaning |
| -------- | ------- | ------- |
| `postgres_version` | `17` | PG major. Matches Debian 13's repo, so no PGDG repo needed. |
| `postgres_listen_addresses` | `localhost,{{ ansible_host }}` | `ansible_host` is the derived `10.1.1.{ctid}`. The CT has no LAN NIC, so nothing leaks to the LAN. |
| `postgres_hba_subnet` | `10.1.1.0/24` | Internal subnet allowed SCRAM TCP auth. |

## Verify

```bash
ssh root@10.1.1.<ctid> 'systemctl is-active postgresql'
ssh root@10.1.1.<ctid> 'pg_isready -h 10.1.1.<ctid> -p 5432'      # accepting connections
ssh root@10.1.1.<ctid> "su - postgres -c \"psql -c 'SELECT version()'\""
```

## Notes

- **Remote auth is inert on a fresh server.** No role has a password yet, so the
  internal-subnet HBA rule has nothing to authenticate until an app role creates
  a password-bearing role. The `pg_isready` verify confirms the listener is up,
  not that any credential works.
- **`pg_hba.conf` is a full-file template by design** (reproducible auth posture,
  like the Caddyfile / `garage.toml`). The local `peer` lines are load-bearing:
  `su - postgres -c psql` — used by the [`backup`](backup.md) role's `pg_dumpall`
  and this role's verify — depends on them.
- **Backup:** the [`backup`](backup.md) role streams a cluster-wide `pg_dumpall`
  over SSH into the restic repo (tag `zai-postgres`). Enable with
  `backup_postgres_enabled: true` once this CT is up.
- For how the CT is assigned a CTID, created and reached, see
  [`provision.yml`](../../ansible/provision.yml) and the
  [main docs](../README.md#service-ctid-assignment).
