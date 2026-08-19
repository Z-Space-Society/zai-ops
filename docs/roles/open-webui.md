# Role: `open-webui`

Installs [Open WebUI](https://docs.openwebui.com/) **natively** as the cluster's
user-facing chat UI — a FastAPI backend + built Svelte frontend, fronting the
[`litellm`](litellm.md) gateway for both chat and RAG embeddings, with users,
chats and settings persisted in [`postgres`](postgres.md).

- **Source:** [`ansible/roles/open-webui/`](../../ansible/roles/open-webui/)
- **Applied by:** [`provision.yml`](../../ansible/provision.yml) (configure play, `hosts: open-webui`, **after** the postgres play)
- **Target:** the `open-webui` CT (whatever CTID it was assigned — apps tier, 120–129), over SSH, internal-only on `vmbr1`

## Purpose

A native Open WebUI install: a Python app run from a dedicated venv under systemd —
**no Docker** (per the [prime directive](../../CLAUDE.md)). It is **internal-only**
on `vmbr1`; the LAN reaches it through the [`proxy`](proxy.md) edge
(`chat.{{ cluster_domain }}` → `open-webui:8080`). Its state lives in Postgres, so
the CT itself holds nothing unreproducible.

**One backend, the litellm gateway.** Open WebUI is pointed at the
[`litellm`](litellm.md) CT as its OpenAI-compatible upstream
(`OPENAI_API_BASE_URL → http://litellm:4000/v1`, key = a scoped, non-admin
litellm virtual key — **never the master key**, see litellm's
[key-management notes](litellm.md#three-key-management-paths-kept-deliberately-separate));
Ollama probing is off. Every member's request also carries their id/email/name
as forwarded headers (`ENABLE_FORWARD_USER_INFO_HEADERS=true`), so litellm can
attribute spend per person even though everyone shares this one key. **RAG
embeddings route through the same gateway**
(`RAG_EMBEDDING_ENGINE=openai`, model `nomic-embed-text`) rather than Open WebUI's
built-in local sentence-transformers model — so there's no multi-GB model download
and embeddings track litellm's availability (litellm serves an always-on CPU floor
embedder; see [`litellm`](litellm.md)).

### Three deliberate divergences from the `litellm` role

This role is otherwise a near-copy of [`litellm`](litellm.md), but differs where it
must:

1. **uv-managed Python 3.12, not the system interpreter.** Open WebUI 0.10.x requires
   Python `>=3.11,<3.13`, but the Debian 13 CT template ships **3.13**, which it
   refuses. So `python3 -m venv` is unusable here. The role installs a pinned,
   checksummed [`uv`](https://docs.astral.sh/uv/) binary and uses it to fetch a
   managed CPython 3.12 and build the venv from it.
2. **No manual DB-migration step.** Open WebUI runs its own Alembic migrations on
   startup, so the role only needs the DB to exist + a valid `DATABASE_URL` — unlike
   litellm's explicit provision-time `prisma migrate deploy`.
3. **Binds `0.0.0.0`, not loopback.** Caddy reaches this service from a *different*
   CT (the proxy) over `vmbr1`; a `127.0.0.1` bind would refuse that and 502 at the
   edge. The boundary is the `vmbr1`-only NAT network, not a loopback bind.

## Tasks

| Task | Module | Why |
| ---- | ------ | --- |
| Probe + create the `openwebui` PG role | `command`/`shell` → `su - postgres -c psql`, `delegate_to: postgres` | Same idiom as litellm: the bare postgres superuser is **peer-only**, so DB setup is delegated to the postgres CT. Probe `pg_roles` → `CREATE ROLE` (else `ALTER ROLE` to sync the password). Password inlined as pure hex via `$OPENWEBUI_DB_PW` (`no_log`). |
| Probe + create the `openwebui` database | `command` → `su - postgres -c psql`, `delegate_to: postgres` | `CREATE DATABASE` can't run in a transaction → probe `pg_database`, then create `OWNER openwebui`. |
| Create `open-webui` group + user | `group`, `user` | Run the daemon unprivileged, no login shell. |
| Create home + data + config dirs | `ansible.builtin.file` | `/opt/open-webui` (daemon-owned; venv + managed interpreter), `/opt/open-webui/data` + `data/hf-cache` (`0750`, daemon-writable runtime state), `/etc/open-webui` (`0750`, root-owned). |
| Ensure `ca-certificates` | `apt` | The uv + package downloads need a trusted CA bundle. |
| Probe + install pinned `uv` | `command`, then `get_url`/`unarchive`/`copy` | Install uv reproducibly from the **pinned, checksummed** release tarball (not `curl \| sh`). Skipped when the installed `uv --version` already matches. |
| Create the venv with managed Python 3.12 | `command` → `uv venv --python 3.12` (`creates`) | uv fetches a managed CPython if absent. `UV_PYTHON_INSTALL_DIR={{ openwebui_python_install_dir }}` forces it **under `/opt/open-webui`** so the chown owns it and `ProtectHome` doesn't hide it. |
| Confirm the base interpreter is under the role tree | `command` → `sys._base_executable` (`failed_when`) | Guards the load-bearing placement above — fail loud at provision time if the managed interpreter ever lands outside `/opt/open-webui`. |
| Install `open-webui` into the venv | `command` → `uv pip install` | pip runs *inside* the venv → PEP 668 doesn't apply. Pinned version. Notifies `restart open-webui`. |
| Chown the home to `open-webui` | `ansible.builtin.file` (`recurse`) | venv/install ran as root; the daemon reads the interpreter, venv and `open-webui` console script. Runs *after* install so the whole tree (incl. the managed CPython) is covered. |
| Resolve `{{ cluster_domain }}` to the proxy CT | `lineinfile` → `/etc/hosts` | Keeps server-side OIDC calls (discovery, token exchange, and the JWKS fetch that validates a `logout_token`) **inside the cluster** — Cloudflare 403s the `Python-urllib` User-Agent PyJWT fetches with, which is what left back-channel logout inert. See [The public origin is resolved internally](#the-public-origin-is-resolved-internally). The regexp is anchored at both ends: it matches its own rendered line (a regexp the line didn't satisfy would append a duplicate every run) and never the `chat.` subdomain, which is a different CT. |
| Install both Cloudflare Origin CA roots | `copy` → `/usr/local/share/ca-certificates/` | Caddy serves a Cloudflare **Origin CA** cert, trusted only by Cloudflare — once the request stops going through Cloudflare, nothing here trusts it. Both roots are committed to the role's `files/` (public static PEMs; either may have signed the deployed cert). Notifies `update ca trust` + restart. |
| Render the secret env file | `template` (`0600 root`, `no_log`) | `DATABASE_URL`, `WEBUI_SECRET_KEY`, the litellm backend `OPENAI_API_*` + `RAG_*`, the corliss OIDC block (`OAUTH_*`/`OPENID_PROVIDER_URL`, see below), `DATA_DIR`/`HF_HOME`, `HOST`/`PORT`, `WEBUI_URL`. Read by systemd via `EnvironmentFile`. Open WebUI has no config file — it's env-configured. Notifies restart. |
| Install the systemd unit | `template` → `/etc/systemd/system/open-webui.service` | Hardened (`ProtectSystem=strict`, `ReadWritePaths={{ openwebui_data_dir }}`, `HOME` → DATA_DIR, `TimeoutStartSec=300`). Notifies reload + restart. |
| Ensure started + enabled | `ansible.builtin.systemd` | Running now + on boot. |
| Flush handlers | `meta: flush_handlers` | Bring the daemon up with final config (it migrates the DB on this first start) *before* the smoke test. |
| Wait for the port + health check | `wait_for` (`127.0.0.1:8080`) + `uri` (`/health`) | `/health` is unauthenticated and proves the app booted and reached the migrated DB — not merely that the port is open. Generous retries cover first-boot migrations. |

### Handlers

| Handler | Action |
| ------- | ------ |
| `reload systemd` | `systemd: daemon_reload=true` |
| `update ca trust` | `command: update-ca-certificates` — rebuilds `/etc/ssl/certs/ca-certificates.crt`, the merged bundle the unit points every HTTP client at. Defined **before** `restart open-webui` on purpose: handlers run in *definition* order, not notification order, so the bundle is rebuilt before the daemon re-reads it. |
| `restart open-webui` | `service: name=open-webui state=restarted` |

## Variables

Defined in [`defaults/main.yml`](../../ansible/roles/open-webui/defaults/main.yml):

| Variable | Default | Meaning |
| -------- | ------- | ------- |
| `openwebui_version` | *(pinned)* | `open-webui` release installed into the venv. |
| `openwebui_python_version` | `3.12` | Managed CPython uv fetches (Debian 13's 3.13 is rejected by Open WebUI). |
| `openwebui_uv_version` / `openwebui_uv_sha256` | *(pinned)* | The uv binary + its tarball checksum. Bump together. |
| `openwebui_port` / `openwebui_host` | `8080` / `0.0.0.0` | Listen socket; `0.0.0.0` so Caddy can reach it from the proxy CT. |
| `openwebui_home` / `openwebui_venv` / `openwebui_python_install_dir` | `/opt/open-webui[/venv,/python]` | Self-contained venv + the uv-managed interpreter, all under the chowned tree. |
| `openwebui_data_dir` / `openwebui_hf_home` | `/opt/open-webui/data[/hf-cache]` | The one runtime-writable tree (vector db, uploads, caches). |
| `openwebui_env_file` | `/etc/open-webui/open-webui.env` | The `0600` secret env (Open WebUI has no config file). |
| `openwebui_db_name` / `openwebui_db_user` | `openwebui` | The Postgres database + role this role creates. |
| `openwebui_litellm_port` | `4000` | litellm's port — a local default (not read from litellm's role defaults); keep in sync with `litellm_port`. |
| `openwebui_rag_embedding_model` | `nomic-embed-text` | The embedding model litellm serves; keep in sync with `litellm_embedding_model_name`. |
| `openwebui_oidc_client_id` | `open-webui` | Local default (not read from the `corliss` role's vars); keep in sync with `corliss_oidc_client_id`. |
| `openwebui_oidc_provider_url` | `https://{{ cluster_domain }}/.well-known/openid-configuration` | corliss's OIDC discovery document — at the **apex**, not a subdomain (ADR-0006). |
| `openwebui_forward_user_info_headers` | `true` | Forwards each member's id/email/name to litellm as headers, so spend is attributed per person under the one shared key. See [`litellm`](litellm.md#three-key-management-paths-kept-deliberately-separate). |
| `openwebui_jwt_expires_in` | `4h` | Lifetime of OpenWebUI's **own** session JWT. With back-channel logout wired, this is no longer the *only* bound on revocation — but it is the one that still holds when a logout token can't be delivered, so keep it short. See [Sessions outlive corliss](#sessions-outlive-corliss). The upstream default is `4w`. |
| `openwebui_enable_oauth_backchannel_logout` | `true` | Exposes `POST /oauth/backchannel-logout`, which corliss calls on sign-out and on revocation. |
| `openwebui_redis_url` | `redis://:<pw>@{{ hostvars['redis'].ansible_host }}:6379/0` | The [`redis`](redis.md) CT. Host derived from its CTID; password is the shared `redis_auth_password` group_var, so both roles agree by construction. **Setting this couples the whole chat surface to Redis, not just logout** — see below. |
| `openwebui_redis_port` | `6379` | A local default (not read from the redis role's defaults); keep in sync with `redis_port`. |
| `openwebui_redis_key_prefix` | `open-webui` | Upstream default, set explicitly so the key shape an operator greps for is written down. |
| `openwebui_redis_socket_connect_timeout` / `openwebui_redis_socket_timeout` | `2` / `2` | **Load-bearing, not tuning.** Both are unset upstream = no timeout: against a dead redis CT every authenticated request blocks for the OS TCP timeout (~130 s) before failing. |

### Sessions outlive corliss

OpenWebUI mints its own session JWT after the OIDC exchange and authenticates
from it thereafter. **corliss is not consulted again until that token expires.**
corliss enforces membership at `/oidc/authorize` (GATE, v0.5.0), which is
reached only when OpenWebUI has no valid session of its own — so two things used
to follow, neither fixable from the corliss side alone: revoking a member did
not end their chat session, and signing out of corliss did not sign them out of
chat.

**Back-channel logout closes both (corliss v0.6.0).** corliss mints a signed
`logout_token` and POSTs it to `POST /oauth/backchannel-logout` here, on three
triggers: sign-out at `/auth/logout`, a revocation arriving from the registry,
and a reconcile run deactivating someone. OpenWebUI validates the token against
corliss's discovery document and JWKS, then ends the session. Revocation goes
from "up to `JWT_EXPIRES_IN`" to seconds.

**Redis is the half that makes it real, and it is not optional.** Without it
this handler can only delete stored OAuth sessions — it cannot invalidate the
JWT the browser is actually authenticating with, which is the only thing that
matters. Redis is where it writes `open-webui:auth:user:<id>:revoked_at`, the
marker that kills every token issued before that instant. See
[`redis`](redis.md).

**Keep `openwebui_jwt_expires_in` short anyway — it is now bounding different
failures, not zero of them.** Immediate revocation depends on a message being
delivered, so the expiry is what still holds on every path where it isn't:

1. **Redis unreachable.** The revocation marker can't be written. (Chat is also
   broken outright in that state — see [`redis`](redis.md#the-trade-this-role-makes-stated-deliberately)
   — but a Redis that recovers *after* the marker was lost leaves a revoked
   member signed in.)
2. **Redis restarted.** Nothing there is persisted, on purpose, so in-flight
   revocation markers are gone and those sessions come back.
3. **The message doesn't get through** — corliss can't reach this CT, or this CT
   can't reach corliss's JWKS to validate what it was sent. Both legs are now
   internal: corliss POSTs here directly, and the return leg resolves the public
   origin to the [`proxy`](proxy.md) CT (below), so what this depends on is
   **Caddy being up** — not public DNS, not Cloudflare. The URL stays public
   because the OIDC issuer must be the public origin or the token's `iss` won't
   match; only the *route* is internal.

There is still no `end_session_endpoint` in corliss's discovery document —
front-channel/RP-initiated logout (the browser-redirect flavour) is a separate
mechanism and isn't built. Back-channel logout covers the cases that matter
here, since both triggers are server-side events.

### The public origin is resolved internally

**Cloudflare 403s the HTTP client that validates logout tokens.** Open WebUI
validates a `logout_token` by fetching corliss's JWKS with PyJWT's
`PyJWKClient` (`utils/oauth.py::handle_backchannel_logout`), and `PyJWKClient`
uses bare `urllib` — User-Agent `Python-urllib/3.x`. Cloudflare's [Browser
Integrity Check](https://developers.cloudflare.com/waf/tools/browser-integrity-check/),
on by default for every zone, refuses that UA with **`error code: 1010`**. It is
purely UA-based — same URL, same moment, `curl`/Chrome/`httpx` all return 200:

| Client | Result |
| ------ | ------ |
| `Python-urllib/3.12` | **403** |
| `curl/8.4.0`, Chrome, `python-httpx/0.27.0` | 200 |

That is why **login worked while logout was inert**: authlib fetches with
`httpx` (200), PyJWT with `urllib` (403). corliss delivered a valid token, this
CT couldn't validate it, and `POST /oauth/backchannel-logout` returned 400 on
every attempt — leaving revocation bounded by `JWT_EXPIRES_IN` exactly as before
the feature was built.

**The fix is a route change, not a URL change.** Three pieces, all in this role:

1. **`/etc/hosts`** maps `{{ cluster_domain }}` to the [`proxy`](proxy.md) CT, so
   server-side calls to the apex go straight to Caddy over `vmbr1` instead of
   out through the host's NAT to Cloudflare and back. Caddy serves every route
   on all its interfaces (no `bind` in the Caddyfile), so **no proxy-role change
   is needed**.
2. **Both Cloudflare Origin CA roots** land in the CT's trust store. Caddy's cert
   is an Origin CA cert — trusted by Cloudflare and nothing else (see
   [`proxy`](proxy.md): origin TLS, no ACME) — so bypassing Cloudflare means
   bypassing the only party that trusted it. The roots are valid until
   **2029-08-15**; a rebuild after that needs fresh ones, if this shim still
   exists.
3. **`SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt`** in the unit points
   *every* client at Debian's merged bundle. This is the load-bearing half:
   `httpx` verifies against **certifi** by default and would reject the Origin CA
   cert on the internal route — fixing logout by breaking login. urllib, httpx
   and aiohttp all honour `SSL_CERT_FILE`, so one variable gives all three the
   same trust story (verified against this release's pinned httpx 0.28.1 /
   PyJWT 2.13.0, including that public TLS still verifies through the merged
   bundle).

**This is a Cloudflare shim, and it is meant to be deletable.** It follows the
same rule as `MEMBERSHIP_REGISTRY_URL` — service-to-service traffic stays
internal, so recovery doesn't depend on public DNS plus an edge — and it
survives Cloudflare's removal gracefully: give Caddy a publicly-trusted cert and
piece 2 and 3 become no-ops to delete, while piece 1 still earns its place. The
one thing it does **not** fix is the same block hitting a *genuinely external*
Python client: `/auth/client-metadata.json` is the atproto `client_id` document,
fetched server-side by each member's PDS. Bluesky's PDS is Go/TS and passes
today, but a Python-based PDS or authorization server would be 403'd, and
nothing on this side can help. See the [Known
gotchas](../README.md#known-gotchas) entry.

### OIDC login: corliss is the only way in

[`corliss`](corliss.md) is the cluster's sole identity provider — see
[ADR-0005](../decisions/0005-zai-auth-over-aip.md). `open-webui.env.j2` sets
`ENABLE_OAUTH_SIGNUP=true`, `ENABLE_SIGNUP=false`, `ENABLE_LOGIN_FORM=false`,
and the `OAUTH_*`/`OPENID_PROVIDER_URL` block pointing at corliss.

**`ENABLE_PERSISTENT_CONFIG=false` is load-bearing, not decorative.**
`ENABLE_LOGIN_FORM`, `ENABLE_SIGNUP` and several other auth settings are
Open WebUI "PersistentConfig" values: read from the environment only on the
app's *very first* boot, then written to its own database and read from
**there** on every restart after — silently ignoring this env file on every
subsequent `provision.yml` run. Setting `ENABLE_PERSISTENT_CONFIG=false`
makes Open WebUI always trust the environment instead, which is also just the
*correct* model here: this repo's whole premise is that config lives in git,
not a mutable runtime database (same reasoning as the proxy role's
git-tracked Caddyfile). See the [Known
gotchas](../README.md#known-gotchas) entry — this one first surfaced as a
non-obvious bug (`ENABLE_LOGIN_FORM=false` deployed cleanly but the local
email/password form kept showing) precisely because open-webui had already
booted once before this setting existed.

**No native "skip the login page" option.** Open WebUI has no built-in way to
auto-redirect straight to the sole configured OAuth provider
([open-webui/open-webui#24325](https://github.com/open-webui/open-webui/issues/24325)
is the open feature request) — visiting `chat.{{ cluster_domain }}` always
lands on `/auth` first, showing a "Continue with ZAI" button to click even
with the local form gone. The [`proxy`](proxy.md) role's `redirects` field on
this route (`/auth*` → `/oauth/oidc/login`) closes that gap at the edge
instead, matching a redirect pattern the Open WebUI community already uses
with nginx.

**That redirect must exempt the OIDC callback's own completion request, or
login never terminates.** Open WebUI's own `handle_callback` (`utils/oauth.py`)
always finishes a successful login by redirecting the browser back to this
same `/auth` path — that's how its frontend picks up the just-set `token`
session cookie and finishes logging in client-side, then navigates to `/`.
A `/auth*` redirect with no exception catches that completion request too
and bounces it straight into another OIDC round-trip — forever. Symptom:
the browser loops entirely on `chat.{{ cluster_domain }}/auth` (never
visibly reaching corliss again), while `journalctl -u open-webui` shows a
*successful* token exchange (`POST /oidc/token 200`, "Stored OAuth session
server-side") on every single cycle — the login is actually succeeding each
time, the browser just never gets to keep it. Fixed with the `redirects`
entry's `skip_if_cookie: token` field: the edge redirect only fires when
open-webui's session cookie is genuinely absent (a first-time visitor); a
request that already carries it falls through to the real app instead. See
[`proxy`](proxy.md#notes).

### Secrets (auto-generated — no manual step)

`openwebui_db_password` and `openwebui_secret_key` are **generated on first run** by
the `password` lookups in
[`group_vars/all/main.yml`](../../ansible/group_vars/all/main.yml) and persisted
under `/root/.zai-secrets` on CT 100 (same posture as the litellm/garage/restic
secrets, **not** the vault). Because the lookup runs on the control node, the role
sets the PG password and renders `DATABASE_URL` from the identical value, and both
stay stable across rebuilds. `WEBUI_SECRET_KEY` is set explicitly so existing logins
survive a rebuild — unset, Open WebUI would write a random key into `DATA_DIR` that a
fresh CT would regenerate.

`openwebui_openai_api_key` is different from the two above: it's not a
`password` lookup, it's a plain file read
(`{{ _zai_secrets_dir }}/openwebui_litellm_key`) of a scoped virtual key that
only the [`litellm`](litellm.md) role can produce (its own play calls
litellm's live `/key/generate` API — see that role's "Two key-management
paths" section). That makes it the one secret this role consumes but doesn't
generate, and the source of the hard provisioning dependency below.

## Dependencies

- **[`postgres`](postgres.md)** must be provisioned first — this role connects to the
  postgres CT (`delegate_to: postgres`) to create its role+database. A full
  [`provision.yml`](../../ansible/provision.yml) run guarantees the order; a
  `--limit open-webui` run still needs postgres already up.
- **[`litellm`](litellm.md)** must be provisioned first too, and not just for chat to
  work at runtime — `openwebui_openai_api_key` is a file lookup against a secret only
  litellm's play produces (see above), so **this role's own provisioning now fails**
  (missing-file error) if litellm has never successfully run. A full `provision.yml`
  run guarantees the order (litellm before open-webui); a `--limit open-webui` run
  needs litellm already provisioned at least once, not merely reachable.
- **[`redis`](redis.md)** must be provisioned first, and this is the hardest
  runtime dependency of the three. Provisioning needs the `redis` host merely
  *assigned* (its `ansible_host` renders `REDIS_URL`), but at runtime OpenWebUI
  consults Redis on **every authenticated request** — an unreachable Redis 500s
  chat and deletes the session cookie rather than degrading. Read
  [the trade](redis.md#the-trade-this-role-makes-stated-deliberately) before
  touching either side.
- **[`proxy`](proxy.md)** exposes it to the LAN via `caddy_proxy_hosts`
  (`chat.{{ cluster_domain }}`); set the domain once with `zai-set-domain`.

## Verify

```bash
ssh root@10.1.1.<ctid> 'systemctl is-active open-webui'
ssh root@10.1.1.<ctid> 'ss -ltnp | grep 8080'                            # listening?
curl -fs http://10.1.1.<ctid>:8080/health                                # alive (from CT 100; curl isn't on the CT, but it binds 0.0.0.0)
ssh root@<postgres-ip> "su - postgres -c 'psql -l'" | grep openwebui     # DB present

# The internal loop-back, exercised through the exact client that used to 403.
# There is no curl on this CT — use the venv's python, which is the real path.
ssh root@10.1.1.<ctid> 'getent hosts <domain>'                           # expect the proxy CT's IP
ssh root@10.1.1.<ctid> 'SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
  /opt/open-webui/venv/bin/python -c "from jwt import PyJWKClient; \
  PyJWKClient(\"https://<domain>/.well-known/jwks.json\").fetch_data(); print(\"jwks ok\")"'

# end-to-end: browse https://chat.<domain>, create the first (admin) user,
# confirm litellm's models appear, and upload a doc to exercise RAG.
# Revocation: revoke a member in corliss, then expect a 200 (not 400) on
# POST /oauth/backchannel-logout in `journalctl -u open-webui`, and the marker
# `open-webui:auth:user:<id>:revoked_at` present on the redis CT.
```

The **first** user to sign up becomes the admin; there's no seeded account.

## Notes

- **uv-managed interpreter placement is load-bearing.** `UV_PYTHON_INSTALL_DIR` puts
  the managed CPython under `{{ openwebui_home }}` so the recursive chown owns it and
  `ProtectHome=true` (which hides `/root`) doesn't hide it from the daemon. The
  "confirm base interpreter" task fails the provision early if that ever breaks. Don't
  drop it back to uv's default `~/.local/share/uv/python`.
- **`ProtectSystem=strict` + one writable path.** `/` is read-only; the unit re-opens
  only `{{ openwebui_data_dir }}` (`ReadWritePaths`) and sets `HOME` there, so any
  library cache (`HF_HOME`/`SENTENCE_TRANSFORMERS_HOME` point inside it) lands
  writable. If a future Open WebUI version needs to write elsewhere, widen
  `ReadWritePaths` rather than dropping the hardening.
- **Install the `[postgres]` extra, not plain `open-webui`.** SQLAlchemy maps the
  `postgresql://` DSN to its default driver, **psycopg2**, but the base package ships
  only psycopg **v3** (`psycopg[binary]`) — psycopg2-binary is behind the `[postgres]`
  extra. A plain install crash-loops at boot on
  `ModuleNotFoundError: No module named 'psycopg2'` and never binds the port. The
  install task uses `open-webui[postgres]==<version>` so the driver is present.
- **The DSN must pin `?client_encoding=utf8`.** OpenWebUI's *async* engine uses the
  psycopg **v3** driver (separate from the sync psycopg2 path), and with psycopg 3.3
  (pinned by 0.10.x) `pg_catalog.version()` returns **bytes** unless the client
  encoding is set — SQLAlchemy then throws `cannot use a string pattern on a
  bytes-like object` parsing the server version and the app crash-loops at startup.
  `openwebui_database_url` appends `?client_encoding=utf8`
  ([sqlalchemy#11373](https://github.com/sqlalchemy/sqlalchemy/discussions/11373)).
- **Migrations run on startup, and a failure is logged but does NOT crash the app** —
  so `/health` can return 200 over a half-migrated DB. Unlike litellm there's no
  provision-time migration gate; on a major version bump, watch
  `journalctl -u open-webui` for migration errors.
- **nomic embeddings want `search_document:` / `search_query:` prefixes.**
  `nomic-embed-text-v1.5` retrieves poorly without them, and litellm passes input
  through verbatim — so the *client* (Open WebUI's RAG pipeline) must add them. Noted
  so mediocre retrieval isn't re-debugged as a model fault (see the litellm floor
  embedder gotcha in the [main docs](../README.md#known-gotchas)).
- **Don't "simplify" the trust setup to one CA file.** `SSL_CERT_FILE` must point
  at the **merged** system bundle, not at an Origin CA root on its own — the app
  also makes ordinary public HTTPS calls, and a single-CA bundle would break
  every one of them. Adding the roots via `update-ca-certificates` and pointing
  at the merged output is what keeps both working.
- **Disk:** the venv pulls `torch` + `sentence-transformers` (multi-GB) even though
  embeddings are routed out to litellm, so the CT rootfs is sized at 16 GB (vs
  litellm's 8) — bump it if RAG uploads/vector data grow.
- For how the CT is assigned a CTID, created and reached, see
  [`provision.yml`](../../ansible/provision.yml) and the
  [main docs](../README.md#service-ctid-assignment).
