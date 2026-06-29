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

### Floor embedder (co-located)

The role also installs an **always-on CPU embedding server** on this same CT: a
`llama-server --embedding` bound to `127.0.0.1:{{ litellm_embedding_port }}` serving
`nomic-embed-text-v1.5`, registered in `config.yaml` as the model
`{{ litellm_embedding_model_name }}`. This guarantees the cluster *always* has an
embedding model (which RAG clients like a future OpenWebUI need) **independent of the
GPU inference nodes**, which are intermittent. The reasoning for co-locating rather
than a separate CT: embeddings are reached *through* litellm
(`OpenWebUI → litellm → backend`), so they can never be more available than litellm
itself — putting the floor in this CT makes its availability *equal* litellm's, with
no extra failure domain. The binary is a **prebuilt CPU llama.cpp release** (not a
source build) so the lean proxy CT never grows a C++ toolchain; the GGUF is small
(~274 MB) so it's fetched at provision time. Disable with
`litellm_embedding_enabled: false`.

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
| *(floor embedder)* Create dirs | `ansible.builtin.file` | `/opt/llama-embed/{,dist,models}` for the binary, its libs and the GGUF. |
| *(floor embedder)* Download the prebuilt CPU llama.cpp release | `ansible.builtin.get_url` | Pinned `ubuntu-x64` (CPU) asset; optional `sha256` pin (same idiom as object_store). No source build → no toolchain on this CT. |
| *(floor embedder)* Extract `llama-server` | `ansible.builtin.unarchive` (`--strip-components=1`, `creates:`) | Binary + `.so` libs unpack flat under one versioned dir; strip it into `dist/`. `creates:` skips re-extraction. |
| *(floor embedder)* Stage the nomic GGUF | `ansible.builtin.get_url` | `nomic-embed-text-v1.5.f16.gguf` pinned to a HF commit revision; small enough to fetch at provision time. |
| *(floor embedder)* Install the unit + start/enable | `template` → `/etc/systemd/system/llama-embed.service`, `systemd` | CPU `--embedding` on loopback. Notifies reload + `restart llama-embed`. Started+enabled (the model is fetched by this role, so no "stage by hand first" gap). |
| Deploy `config.yaml` | `template` (`0640 root:litellm`) | Non-secret model routing; the floor embedder entry is prepended when enabled; master key referenced as `os.environ/…`. Notifies restart. |
| Render the secret env file | `template` (`0600 root`, `no_log`) | `DATABASE_URL` (with the generated db password), `LITELLM_MASTER_KEY`, `LITELLM_SALT_KEY`, `STORE_MODEL_IN_DB`, plus `LITELLM_MODE=PRODUCTION` / `LITELLM_LOG=ERROR`. Read by systemd via `EnvironmentFile`. Notifies restart. |
| Install the systemd unit | `template` → `/etc/systemd/system/litellm.service` | Hardened (`ProtectSystem=strict`, `ReadWritePaths={{ litellm_home }}` so the daemon can read its pre-fetched Prisma cache, `PRISMA_OFFLINE_MODE=true`, `TimeoutStartSec=120` for startup headroom). Notifies reload + restart. |
| Ensure started + enabled | `ansible.builtin.systemd` | Running now + on boot. |
| Flush handlers | `meta: flush_handlers` | Bring the daemon up with final config *before* the smoke test. By now the client is generated and the schema migrated, so startup connects to a ready DB. |
| Wait for the port + health check | `wait_for` (`127.0.0.1:4000`) + `uri` (`/health/liveliness`) | Liveness proves the app booted and reached the migrated DB — not merely that the port is open. |
| *(floor embedder)* Wait + embedding smoke test | `wait_for` + `uri` (POST `/v1/embeddings`) | Runs after the flush (so a changed unit is restarted). A returned vector proves the server booted in embeddings mode and the model loaded. |

### Handlers

| Handler | Action |
| ------- | ------ |
| `reload systemd` | `systemd: daemon_reload=true` |
| `restart litellm` | `service: name=litellm state=restarted` |
| `restart llama-embed` | `service: name=llama-embed state=restarted` |

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
| `litellm_embedding_enabled` | `true` | Master switch for the whole floor-embedder block (tasks + the prepended `config.yaml` entry). |
| `litellm_embedding_model_name` | `nomic-embed-text` | `model_name` clients request at `:4000`; the loopback route maps to it. |
| `litellm_embedding_port` | `8090` | Loopback port for the embedding `llama-server`. **Must differ from `litellm_port`.** |
| `llama_embed_release` | *(pinned, e.g. `b9840`)* | llama.cpp release tag; the `ubuntu-x64` (CPU) asset is fetched from it. |
| `llama_embed_binary_sha256` / `llama_embed_model_sha256` | `""` | Optional `sha256` integrity pins (`omit` when empty). |
| `llama_embed_dir` | `/opt/llama-embed` | Holds `dist/` (binary + libs) and `models/` (the GGUF). |
| `llama_embed_model_repo` / `llama_embed_model_rev` / `llama_embed_model_file` | *(nomic GGUF)* | HF repo, pinned commit revision, and filename of the embedding GGUF. |
| `llama_embed_ctx` | `8192` | `--ctx-size`/`--batch-size`; nomic's full context (needs the unit's yarn rope flags). |

### Secrets (auto-generated — no manual step)

`litellm_master_key`, `litellm_salt_key` and `litellm_db_password` are
**generated on first run** by the `password` lookups in
[`group_vars/all/main.yml`](../../ansible/group_vars/all/main.yml) and persisted
under `/root/.zai-secrets` on CT 100 (same posture as the garage/restic secrets,
**not** the vault). Because the lookup runs on the control node, the value the
role sets as the PG password and the value rendered into `DATABASE_URL` are
identical, and both stay stable across rebuilds.

**The `.zai-secrets` files hold only the generated hex** — the `sk-` prefix on
`litellm_master_key` / `litellm_salt_key` is added by the template, *not* stored in
the file. So `cat`-ing the file gives you the key **without** `sk-`; clients and the
admin UI authenticate with the full `sk-…` value. Read the actual master key either
way:

```bash
echo "sk-$(cat /root/.zai-secrets/litellm_master_key)"   # reconstruct the sk-… key
# …or read the rendered value straight off the litellm CT:
ssh root@10.1.1.<ctid> 'grep MASTER_KEY /etc/litellm/litellm.env'
```

The admin UI login is username `admin`, password = that full `sk-…` master key.

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

# Floor embedder: up on loopback, and reachable through litellm (the OpenWebUI path).
ssh root@10.1.1.<ctid> 'systemctl is-active llama-embed'
ssh root@10.1.1.<ctid> 'curl -fs http://127.0.0.1:8090/v1/embeddings \
  -H "Content-Type: application/json" \
  -d "{\"model\":\"nomic-embed-text\",\"input\":\"search_document: hello\"}" | head -c 120'
ssh root@10.1.1.<ctid> 'curl -fs http://127.0.0.1:4000/v1/embeddings \
  -H "Authorization: Bearer $(grep MASTER_KEY /etc/litellm/litellm.env | cut -d= -f2)" \
  -H "Content-Type: application/json" \
  -d "{\"model\":\"nomic-embed-text\",\"input\":\"search_query: hello\"}" | head -c 120'
# Catch the glibc gotcha early — the Ubuntu binary must resolve on Debian 13:
ssh root@10.1.1.<ctid> 'ldd /opt/llama-embed/dist/llama-server | grep -i "not found" || echo OK'
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
- **The floor embedder is a CPU prebuilt binary on purpose.** The `ubuntu-x64` asset
  links against an older glibc; Debian 13's newer glibc runs it via backward
  compatibility (the verify step's `ldd` check catches the rare reverse case). If a
  future release ever needs a glibc newer than the host, pin an older release rather
  than reintroduce a source build (which would drag the C++ toolchain onto this CT).
- **nomic wants task-instruction prefixes — that's the *client's* job, not this role's.**
  `nomic-embed-text-v1.5` expects `search_document:` on indexed chunks and
  `search_query:` on queries; without them retrieval quality drops. LiteLLM passes the
  input through verbatim, so **OpenWebUI's RAG pipeline must add the prefixes** when it
  is built. Flagged here so mediocre retrieval isn't re-debugged as a model problem.
- **nomic's full 8192 context needs rope scaling.** llama.cpp defaults to 2048; the
  unit passes `--ctx-size 8192 --batch-size 8192 --rope-scaling yarn --rope-freq-scale 0.75`
  so full-length RAG chunks aren't silently truncated.
- **`litellm_embedding_port` must differ from `litellm_port`** — they share the CT.
- For how the CT is assigned a CTID, created and reached, see
  [`provision.yml`](../../ansible/provision.yml) and the
  [main docs](../README.md#service-ctid-assignment).
