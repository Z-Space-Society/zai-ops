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
database on the postgres CT and points `DATABASE_URL` at it. LiteLLM runs **Prisma
migrations on first start**, so that database must exist and the DSN must be valid
*before* the service comes up — which is why this play is ordered after
[`postgres`](postgres.md) (and why a `--limit litellm` run still needs postgres
already provisioned).

## Tasks

| Task | Module | Why |
| ---- | ------ | --- |
| Probe + create the `litellm` PG role | `command`/`shell` → `su - postgres -c psql`, `delegate_to: postgres` | The bare postgres superuser is **peer-only** (no TCP superuser), so DB setup is delegated to the postgres CT and run as the `postgres` OS user. psql has no `CREATE ROLE IF NOT EXISTS` → probe `pg_roles`, then `CREATE ROLE` (else `ALTER ROLE` to sync the password). Password passed via psql's `:'pw'` interpolation from `$LITELLM_DB_PW` (`no_log`, kept out of argv). |
| Probe + create the `litellm` database | `command` → `su - postgres -c psql`, `delegate_to: postgres` | `CREATE DATABASE` can't run in a transaction/DO block → probe `pg_database`, then create `OWNER litellm`. |
| Create `litellm` group + user | `group`, `user` | Run the daemon unprivileged, no login shell. |
| Create home + config dirs | `ansible.builtin.file` | `/opt/litellm` (daemon-owned; venv + Prisma cache), `/etc/litellm` (`0750`, root-owned, group-readable). |
| Install `python3-venv` + `pip` | `apt` | Debian 13 marks the system Python externally-managed (PEP 668); we never pip into it. |
| Install `litellm[proxy]` into the venv | `ansible.builtin.pip` (`virtualenv=`) | pip runs *inside* the venv → PEP 668 doesn't apply. Pinned version (Prisma schema is version-coupled). Notifies `restart litellm`. |
| Chown the venv to `litellm` | `ansible.builtin.file` (`recurse`) | pip ran as root; the daemon reads the interpreter/console script and (under `ProtectSystem=strict`) writes Prisma's engine cache here. |
| Deploy `config.yaml` | `template` (`0640 root:litellm`) | Non-secret model routing; master key referenced as `os.environ/…`. Notifies restart. |
| Render the secret env file | `template` (`0600 root`, `no_log`) | `DATABASE_URL` (with the generated db password), `LITELLM_MASTER_KEY`, `STORE_MODEL_IN_DB`. Read by systemd via `EnvironmentFile`. Notifies restart. |
| Install the systemd unit | `template` → `/etc/systemd/system/litellm.service` | Hardened (`ProtectSystem=strict`, `ReadWritePaths={{ litellm_home }}` for Prisma, `TimeoutStartSec=120` for first-boot migration). Notifies reload + restart. |
| Ensure started + enabled | `ansible.builtin.systemd` | Running now + on boot. |
| Flush handlers | `meta: flush_handlers` | Bring the daemon up (first-boot Prisma migrations) with final config *before* the smoke test. |
| Wait for the port + health check | `wait_for` (`127.0.0.1:4000`) + `uri` (`/health/liveliness`) | Liveness proves the app booted and migrations succeeded — not merely that the port is open. |

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
| `litellm_port` | `4000` | Listen port; matches the proxy route. |
| `litellm_venv` / `litellm_home` | `/opt/litellm[/venv]` | Self-contained venv + daemon home (Prisma cache). |
| `litellm_config_file` / `litellm_env_file` | `/etc/litellm/{config.yaml,litellm.env}` | Non-secret config and the `0600` secret env. |
| `litellm_db_name` / `litellm_db_user` | `litellm` | The Postgres database + role this role creates. |
| `litellm_model_list` | `[]` | Model routing rendered into `config.yaml`. Empty by default; models can also be added at runtime (persisted in PG via `STORE_MODEL_IN_DB`). |

### Secrets (auto-generated — no manual step)

`litellm_master_key` and `litellm_db_password` are **generated on first run** by
the `password` lookups in
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
  store *is* that database; a missing/invalid DSN fails the first-boot migration.
- **Generated passwords are hex** (`chars=digits,abcdef`) so they need no
  percent-encoding inside the `DATABASE_URL`.
- **Prisma writes on first boot.** `ProtectSystem=strict` makes `/` read-only;
  the unit re-opens `{{ litellm_home }}` (`ReadWritePaths`) and sets `HOME` there
  so Prisma can fetch/cache its query-engine binary. If a future LiteLLM version
  wants to write elsewhere, widen `ReadWritePaths` rather than dropping the
  hardening.
- For how the CT is assigned a CTID, created and reached, see
  [`provision.yml`](../../ansible/provision.yml) and the
  [main docs](../README.md#service-ctid-assignment).
