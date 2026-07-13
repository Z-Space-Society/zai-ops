# Role: `zai-auth`

Installs [zai-auth](../../apps/zai-auth/) **natively** as the cluster's login
spine — a Django app implementing an ATProto handle → OIDC `id_token` bridge,
so members sign into [`open-webui`](open-webui.md) (and future apps) with
their Bluesky handle instead of a separate password. See
[ADR-0005](../decisions/0005-zai-auth-over-aip.md) for why this app, not AIP,
is the cluster's login bridge.

- **Source:** [`ansible/roles/zai-auth/`](../../ansible/roles/zai-auth/); app
  source: [`apps/zai-auth/`](../../apps/zai-auth/)
- **Applied by:** [`provision.yml`](../../ansible/provision.yml) (configure
  play, `hosts: zai-auth`, **after** the postgres play)
- **Target:** the `zai-auth` CT (platform tier, 110–129) over SSH, internal-only
  on `vmbr1`

## Purpose

A native install: a Django app (gunicorn WSGI, dedicated venv) run under
systemd — **no Docker** (per the [prime directive](../../CLAUDE.md)). It is
**internal-only** on `vmbr1`; the LAN reaches it through the
[`proxy`](proxy.md) edge (`account.{{ cluster_domain }}` → `zai-auth:8000`).
Its state (member identities, OAuth/OIDC tokens) lives in Postgres, so the CT
itself holds nothing unreproducible except the two signing keys (see Secrets).

**One real divergence from every other role: no published package.** litellm,
Open WebUI and HappyView all pip/cargo-install a pinned release. zai-auth *is*
this repo — there's no upstream to pin — so the role `rsync`s
`apps/zai-auth/` straight from the control node's own checkout
(`/opt/zai-ops`) onto the CT before installing its `requirements.txt`. A
`git pull` on CT 100 followed by `provision.yml --limit zai-auth` is the
entire update story, matching the repo's `bin/` convention (`CLAUDE.md`: pull
= live).

## Tasks

| Task | Module | Why |
| ---- | ------ | --- |
| Probe + create the `zai_auth` PG role | `command`/`shell` → `su - postgres -c psql`, `delegate_to: postgres` | Same idiom as litellm/open-webui/happyview: the bare postgres superuser is **peer-only**. Probe `pg_roles` → `CREATE ROLE` (else `ALTER ROLE` to sync the password). `no_log`. |
| Probe + create the `zai_auth` database | `command` → `su - postgres -c psql`, `delegate_to: postgres` | `CREATE DATABASE` can't run in a transaction → probe `pg_database`, then create `OWNER zai_auth`. |
| Create `zai-auth` group + user | `group`, `user` | Run the daemon unprivileged, no login shell. |
| Create home/src/config/keys dirs | `ansible.builtin.file` | `/opt/zai-auth` (+ `src/`, daemon-owned), `/etc/zai-auth` (+ `keys/`, root-owned, group-readable). |
| Ensure `rsync` on the control node and the CT | `apt` (one `delegate_to: localhost`) | Both ends of the sync need the binary. |
| Sync the app source | `ansible.posix.synchronize` (`delete: true`) | Mirrors `apps/zai-auth/` from CT 100 onto this CT, excluding local dev artifacts (`.venv`, `__pycache__`, `keys/`, `.env`, `db.sqlite3`) the app's own `.gitignore` also excludes. Notifies restart. |
| Probe + install pinned `uv` | `command`, then `get_url`/`unarchive`/`copy` | Install uv reproducibly from the **pinned, checksummed** release tarball (not `curl \| sh`), same idiom as `open-webui`. Skipped when the installed `uv --version` already matches. |
| Create the venv against the system Python | `command` → `uv venv --python python3` (`creates`) | Debian 13 ships Python 3.13 natively — Django 5.2's ceiling — so unlike `open-webui` there's no managed-interpreter fetch/placement to get right; `uv venv` just wraps the system interpreter. |
| Install dependencies into the venv | `command` → `uv pip install --python {{ zai_auth_venv }}/bin/python -r {{ zai_auth_src }}/requirements.txt` | The synced `requirements.txt` **is** the version pin — no separate role-level version var to drift. Notifies restart. |
| Render the two signing keys | `copy` (`content:`, `0640` root:zai-auth, `no_log`) | EC P-256 (atproto DPoP/ES256) + RSA (OIDC id_token/RS256) PEMs from the cached `group_vars` secrets — see Secrets. Group-readable: Django reads these paths itself. Notifies restart. |
| Render the secret env file | `template` (`0600` root, `no_log`) | `DATABASE_URL`, `SECRET_KEY`, key paths, `PUBLIC_BASE_URL`, `OIDC_CLIENT_ID/SECRET`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`. Read by systemd via `EnvironmentFile`. Notifies restart. |
| Apply database migrations | `command` → `manage.py migrate --noinput` (`no_log`) | Provision-time, before the daemon ever starts — same principle as litellm's `prisma migrate deploy`. Idempotent (`changed_when` on "No migrations to apply"). Notifies restart. |
| Ensure the break-glass local admin exists | `command` → `manage.py ensure_admin` (`no_log`) | Idempotently creates a local username/password superuser (`admin`, no ATProto identity) as a way in if OIDC/ATProto login is ever broken. Only sets the password at creation (never rotates it on re-runs); re-asserts `is_staff`/`is_superuser` on every run so provisioning re-runs heal it if those flags ever get flipped off. See [Notes](#notes). |
| Chown the home to `zai-auth` | `ansible.builtin.file` (`recurse`) | rsync/pip/migrate ran as root; the daemon reads the venv + synced source. Runs *after* install/migrate. |
| Install the systemd unit | `template` → `/etc/systemd/system/zai-auth.service` | Hardened (`ProtectSystem=strict`, `ReadWritePaths={{ zai_auth_home }}`); `ExecStart` runs gunicorn against `zai_auth.wsgi:application`. Notifies reload + restart. |
| Ensure started + enabled | `ansible.builtin.systemd` | Running now + on boot. |
| Flush handlers | `meta: flush_handlers` | Bring the daemon up with final config before the smoke test. |
| Wait for the port + health check | `wait_for` (`127.0.0.1:8000`) + `uri` (`/.well-known/openid-configuration`) | The discovery doc proves the app booted, reached the migrated DB, *and* the signing keys loaded (`signing.py` fails closed on a missing key) — not merely that the port is open. |

### Handlers

| Handler | Action |
| ------- | ------ |
| `reload systemd` | `systemd: daemon_reload=true` |
| `restart zai-auth` | `service: name=zai-auth state=restarted` |

## Variables

Defined in [`defaults/main.yml`](../../ansible/roles/zai-auth/defaults/main.yml):

| Variable | Default | Meaning |
| -------- | ------- | ------- |
| `zai_auth_repo_dir` | `/opt/zai-ops` | Where the app source is synced *from* — the control node's own checkout. |
| `zai_auth_port` / `zai_auth_host` | `8000` / `0.0.0.0` | gunicorn's bind; `0.0.0.0` so Caddy can reach it from the proxy CT. |
| `zai_auth_gunicorn_workers` | `2` | gunicorn worker count. |
| `zai_auth_home` / `zai_auth_src` / `zai_auth_venv` | `/opt/zai-auth[/src,/venv]` | Synced source + venv, all under the chowned tree. |
| `zai_auth_config_dir` / `zai_auth_keys_dir` | `/etc/zai-auth[/keys]` | Env file + the two signing-key PEMs. |
| `zai_auth_db_name` / `zai_auth_db_user` | `zai_auth` | The Postgres database + role this role creates. |
| `zai_auth_url` | `https://account.{{ cluster_domain }}` | `PUBLIC_BASE_URL` — anchors the atproto `client_id`, OIDC issuer, and redirect/JWKS URLs. |
| `zai_auth_oidc_client_id` | `open-webui` | Local default — keep in sync with `open-webui`'s own `openwebui_oidc_client_id`. |
| `zai_auth_oidc_redirect_uris` | `[https://chat.{{ cluster_domain }}/oauth/oidc/callback]` | Open WebUI's OIDC callback, registered on this side. |

### Secrets

`zai_auth_db_password`, `zai_auth_secret_key`, `zai_auth_oidc_client_secret`
and `zai_auth_admin_password` follow the standard `password` lookup pattern in
[`group_vars/all/main.yml`](../../ansible/group_vars/all/main.yml) — generated
on first run, persisted under `/root/.zai-secrets`, stable across rebuilds.
`zai_auth_admin_password` is the **DR-critical** break-glass admin's password
(see [Tasks](#tasks) above) — the only way into `/admin/` if ATProto/OIDC
login is ever broken, so it belongs alongside the two signing keys below on
any escrow checklist.

The two signing keys (`zai_auth_atproto_ec_key`, `zai_auth_oidc_rsa_key`) are
the **DR-critical** items — losing `atproto_ec` invalidates the atproto
`client_id`'s identity (every member re-consents); losing `oidc_rsa`
invalidates every `id_token`/session in flight. They're generated **once**
with `openssl genpkey` (PKCS8 PEM, matching the format the app's own
`manage.py generate_keys` produces for local dev) and cached under
`/root/.zai-secrets` — **not** generated on-box by the app's own management
command, whose docstring explicitly defers production provisioning as "a
deployment-spec decision." Caching them on the control node means they ride
the *existing* Tier-1 control-node backup for free, rather than needing a new
per-CT backup path.

## Dependencies

- **[`postgres`](postgres.md)** must be provisioned first — this role connects
  to the postgres CT (`delegate_to: postgres`) to create its role+database. A
  full [`provision.yml`](../../ansible/provision.yml) run guarantees the
  order; a `--limit zai-auth` run still needs postgres already up.
- **The control node's own checkout** (`/opt/zai-ops`) is the source of truth
  synced onto the CT — there's no upstream release to pull instead.
- **[`open-webui`](open-webui.md)** is the one OIDC relying party today. It
  doesn't block zai-auth's provisioning, but Open WebUI logins won't work
  until both are up and its `OPENID_PROVIDER_URL`/`OAUTH_CLIENT_*` are
  pointed at zai-auth (already wired in `open-webui`'s own defaults/template).
- **[`proxy`](proxy.md)** exposes it to the LAN via `caddy_proxy_hosts`
  (`account.{{ cluster_domain }}`); set the domain once with `zai-set-domain`.

## Verify

```bash
ssh root@10.1.1.<ctid> 'systemctl is-active zai-auth'
ssh root@10.1.1.<ctid> 'ss -ltnp | grep 8000'                              # listening?
curl -fs http://10.1.1.<ctid>:8000/.well-known/openid-configuration        # discovery doc
ssh root@<postgres-ip> "su - postgres -c 'psql -l'" | grep zai_auth        # DB present
# end-to-end: browse https://chat.<domain>, click the ZAI OAuth button,
# log in with an ATProto handle, confirm sub/handle/email land in Open WebUI
# and that local signup/login are gone.
```

## Notes

- **Granting admin to an ATProto member: `zai-make-admin <handle>`.** Promotes
  a handle to `is_staff`/`is_superuser` keyed on its DID (`manage.py
  make_admin`, backed by [`make-admin.yml`](../../ansible/make-admin.yml)) —
  resolves the handle via DNS TXT then HTTPS well-known, verifies against the
  DID document's `alsoKnownAs`, then `get_or_create`s on `did`. Because
  `atproto_oauth.views._upsert_member` also keys exclusively on `did` and
  never touches `is_staff`/`is_superuser`, a member promoted this way (before
  or after their first login) always lands on the same row and keeps admin
  rights — one account, admin from first login. This account never gets a
  password (`set_unusable_password()`); it only ever authenticates via
  ATProto OAuth, unlike the break-glass `admin` account above.
- **The app has no `email` field to add — it's already on `AbstractUser`.**
  Sourcing it (via `transition:email` + `com.atproto.server.getSession`
  against the member's own PDS — DPoP tokens can't be proxied) was the
  app-side half of this deployment; see the "Deployment" section of
  [`apps/zai-auth/README.md`](../../apps/zai-auth/README.md).
- **`ProtectSystem=strict` doesn't hide `/etc/zai-auth`.** Strict makes `/`
  *read-only*, not inaccessible — the env file and signing keys stay readable
  without needing `ReadWritePaths`; only `zai_auth_home` needs write access
  (nothing under `/etc` is written at runtime).
- **rsync `delete: true` is deliberate.** The CT's copy of the app is meant to
  be an exact mirror of this repo's `apps/zai-auth/` — a file removed from the
  repo should disappear from the CT too, not linger.
- For how the CT is assigned a CTID, created and reached, see
  [`provision.yml`](../../ansible/provision.yml) and the
  [main docs](../README.md#service-ctid-assignment).
