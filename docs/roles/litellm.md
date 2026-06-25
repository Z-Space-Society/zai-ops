# Role: `litellm`

Installs [LiteLLM](https://docs.litellm.ai/) **natively** as the cluster's
OpenAI-compatible gateway — a single API in front of the inference nodes
(`llama-server`) and any external model providers, with keys/spend/models
persisted in [`postgres`](postgres.md).

- **Source:** [`ansible/roles/litellm/`](../../ansible/roles/litellm/)
- **Applied by:** [`provision.yml`](../../ansible/provision.yml) (configure play, `hosts: litellm`, **after** the postgres play)
- **Target:** the `litellm` CT (whatever CTID it was assigned), over SSH, internal-only on `vmbr1`

## Purpose

A native LiteLLM proxy: a Python app (`litellm[proxy]`) run from a dedicated venv
under systemd — **no Docker** (per the [prime directive](../../CLAUDE.md)). It is
**internal-only** on `vmbr1`; the LAN reaches it through the
[`proxy`](proxy.md) edge (`api.{{ cluster_domain }}` → `litellm:4000`). Its state
(virtual keys, spend, runtime-added models) lives in Postgres, so the CT itself
holds nothing unreproducible.

**Postgres-backed by default.** The role provisions its *own* `litellm` role +
database on the postgres CT and points `DATABASE_URL` at it. Because
`litellm[proxy]` ships only the Prisma *schema* (the Docker image generates the
client and migrates the DB out of band), the role does both **at provision time** —
`prisma generate` then `prisma migrate deploy`, *before* the daemon ever starts. So
that database must exist and the DSN must be valid first, which is why this play is
ordered after [`postgres`](postgres.md) (and why a `--limit litellm` run still needs
postgres already provisioned).

## Tasks

| Task | Module | Why |
| ---- | ------ | --- |
| Probe + create the `litellm` PG role | `command`/`shell` → `su - postgres -c psql`, `delegate_to: postgres` | The bare postgres superuser is **peer-only** (no TCP superuser), so DB setup is delegated to the postgres CT and run as the `postgres` OS user. psql has no `CREATE ROLE IF NOT EXISTS` → probe `pg_roles`, then `CREATE ROLE` (else `ALTER ROLE` to sync the password). psql does **not** interpolate `:'var'` in a `-c` string, so the password is inlined into the SQL; it's pure hex so it can't break the quoting, and reaches the delegate as `$LITELLM_DB_PW` (`no_log`, kept out of the templated command). |
| Probe + create the `litellm` database | `command` → `su - postgres -c psql`, `delegate_to: postgres` | `CREATE DATABASE` can't run in a transaction/DO block → probe `pg_database`, then create `OWNER litellm`. |
| Create `litellm` group + user | `group`, `user` | Run the daemon unprivileged, no login shell. |
| Create home + config dirs | `ansible.builtin.file` | `/opt/litellm` (daemon-owned; venv + Prisma cache), `/etc/litellm` (`0750`, root-owned, group-readable). |
| Install `python3-venv` + `pip` | `apt` | Debian 13 marks the system Python externally-managed (PEP 668); we never pip into it. |
| Install `litellm[proxy]` **+ `prisma`** into the venv | `ansible.builtin.pip` (`virtualenv=`) | pip runs *inside* the venv → PEP 668 doesn't apply. Both pinned (the schema is version-coupled, and `litellm[proxy]` does **not** pull prisma — it lives in the `extra_proxy` extra). `PRISMA_SKIP_POSTINSTALL_GENERATE=1` defers client generation to the explicit step below. Notifies `restart litellm`. |
| Locate the Prisma schema | `command` → venv python | Ask the venv where `litellm_proxy_extras/schema.prisma` lives rather than hardcode a `site-packages` path a point release could move. |
| Generate the Prisma client | `command` → `prisma generate --schema` | Builds the Python client and fetches the query-engine binary into the daemon's cache — the native equivalent of the Docker image's build-time `prisma generate`. No DB needed. |
| Migrate the schema | `command` → `prisma migrate deploy --schema` (`no_log`) | Applies the migration files `litellm_proxy_extras` ships to the (already-created) `litellm` DB, **at provision time, before the daemon starts** — running `litellm` directly never migrates (only the Docker entrypoint does). Idempotent: a converged DB reports "No pending migrations to apply". `DATABASE_URL` carries the password → `no_log`. Notifies restart. |
| Chown the home to `litellm` | `ansible.builtin.file` (`recurse`) | pip/generate/migrate ran as root; the daemon reads the interpreter/console script, the generated Prisma client and the engine cache. Runs *after* generate/migrate so the whole tree is covered. |
| Deploy `config.yaml` | `template` (`0640 root:litellm`) | Non-secret model routing; master key referenced as `os.environ/…`. Notifies restart. |
| Render the secret env file | `template` (`0600 root`, `no_log`) | `DATABASE_URL` (with the generated db password), `LITELLM_MASTER_KEY`, `LITELLM_SALT_KEY`, `STORE_MODEL_IN_DB`, plus `LITELLM_MODE=PRODUCTION` / `LITELLM_LOG=ERROR`. Read by systemd via `EnvironmentFile`. Notifies restart. |
| Install the systemd unit | `template` → `/etc/systemd/system/litellm.service` | Hardened (`ProtectSystem=strict`, `ReadWritePaths={{ litellm_home }}` so the daemon can read its pre-fetched Prisma cache, `PRISMA_OFFLINE_MODE=true`, `TimeoutStartSec=120` for startup headroom). Notifies reload + restart. |
| Ensure started + enabled | `ansible.builtin.systemd` | Running now + on boot. |
| Flush handlers | `meta: flush_handlers` | Bring the daemon up with final config *before* the smoke test. By now the client is generated and the schema migrated, so startup connects to a ready DB. |
| Wait for the port + health check | `wait_for` (`127.0.0.1:4000`) + `uri` (`/health/liveliness`) | Liveness proves the app booted and reached the migrated DB — not merely that the port is open. |

### Handlers

| Handler | Action |
| ------- | ------ |
| `reload systemd` | `systemd: daemon_reload=true` |
| `restart litellm` | `service: name=litellm state=restarted` |

## Variables

Defined in [`defaults/main.yml`](../../ansible/roles/litellm/defaults/main.yml):

| Variable | Default | Meaning |
| -------- | ------- | ------- |
| `litellm_version` | *(pinned)* | `litellm[proxy]` release installed into the venv. Bump deliberately (Prisma schema is version-coupled). |
| `litellm_prisma_version` | *(pinned)* | `prisma` (client) installed alongside, since `litellm[proxy]` omits it. `0.15.0` is the first release to run on Python 3.13 (Debian 13). |
| `litellm_port` | `4000` | Listen port; matches the proxy route. |
| `litellm_venv` / `litellm_home` | `/opt/litellm[/venv]` | Self-contained venv + daemon home (Prisma cache). |
| `litellm_config_file` / `litellm_env_file` | `/etc/litellm/{config.yaml,litellm.env}` | Non-secret config and the `0600` secret env. |
| `litellm_db_name` / `litellm_db_user` | `litellm` | The Postgres database + role this role creates. |
| `litellm_model_list` | `[]` | Model routing rendered into `config.yaml`. Empty by default; models can also be added at runtime (persisted in PG via `STORE_MODEL_IN_DB`). |

### Secrets (auto-generated — no manual step)

`litellm_master_key`, `litellm_salt_key` and `litellm_db_password` are
**generated on first run** by the `password` lookups in
[`group_vars/all/main.yml`](../../ansible/group_vars/all/main.yml) and persisted
under `/root/.zai-secrets` on CT 100 (same posture as the garage/restic secrets,
**not** the vault). Because the lookup runs on the control node, the value the
role sets as the PG password and the value rendered into `DATABASE_URL` are
identical, and both stay stable across rebuilds. Read the master key with:

```bash
cat /root/.zai-secrets/litellm_master_key   # the sk-… clients authenticate with
```

## Dependencies

- **[`postgres`](postgres.md)** must be provisioned first — this role connects to
  the postgres CT (`delegate_to: postgres`) to create its role+database, and the
  server must already be up with `scram-sha-256` so the role password hashes
  correctly. A full [`provision.yml`](../../ansible/provision.yml) run guarantees
  the order.
- **[`proxy`](proxy.md)** exposes it to the LAN via `caddy_proxy_hosts`
  (`api.{{ cluster_domain }}`); set the domain once with `zai-set-domain`.

## Verify

```bash
ssh root@10.1.1.<ctid> 'systemctl is-active litellm'
ssh root@10.1.1.<ctid> 'curl -fs http://127.0.0.1:4000/health/liveliness'   # alive
ssh root@<postgres-ip> "su - postgres -c 'psql -l'" | grep litellm          # DB present
```

## Notes

- **`DATABASE_URL` is required even with `STORE_MODEL_IN_DB=true`** — the model
  store *is* that database; a missing/invalid DSN fails the provision-time
  `prisma migrate deploy`.
- **`LITELLM_SALT_KEY` must never change once a model is stored.** It encrypts the
  provider credentials `STORE_MODEL_IN_DB` writes to Postgres; there is no recovery
  short of dropping the encrypted rows and re-adding the models. It's kept distinct
  from `litellm_master_key` on purpose — unset, LiteLLM would encrypt with the
  master key instead, coupling credential encryption to an otherwise-rotatable key.
  Like the other secrets it's pinned by a `password` lookup, so it stays stable.
- **Generated passwords are hex** (`chars=digits,abcdef`) so they need no
  percent-encoding inside the `DATABASE_URL`.
- **Prisma engine is pre-fetched; the daemon runs offline.** `prisma generate`
  fetches the query-engine binary into `{{ litellm_home }}/.cache` at provision
  time; the unit sets `PRISMA_OFFLINE_MODE=true` so the daemon **reads** that cache
  rather than fetching at runtime (a download would fail under `ProtectSystem=strict`
  anyway). `ProtectSystem=strict` makes `/` read-only; the unit re-opens
  `{{ litellm_home }}` (`ReadWritePaths`) and sets `HOME` there so that cache stays
  readable/writable. If a future LiteLLM version wants to write elsewhere, widen
  `ReadWritePaths` rather than dropping the hardening.
- For how the CT is assigned a CTID, created and reached, see
  [`provision.yml`](../../ansible/provision.yml) and the
  [main docs](../README.md#service-ctid-assignment).
