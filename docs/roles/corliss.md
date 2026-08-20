# Role: `corliss`

Installs [Corliss](https://github.com/Z-Space-Society/Corliss) **natively** as
the cluster's login spine — a Django app implementing an ATProto handle → OIDC
`id_token` bridge, so members sign into [`open-webui`](open-webui.md) (and
future apps) with their Bluesky handle instead of a separate password. See
[ADR-0005](../decisions/0005-zai-auth-over-aip.md) for why this app, not AIP,
is the cluster's login bridge, and
[ADR-0006](../decisions/0006-corliss-standalone-apex.md) for why it now lives
in its own repo and owns the apex domain.

- **Source:** [`ansible/roles/corliss/`](../../ansible/roles/corliss/); app
  source: [`Z-Space-Society/Corliss`](https://github.com/Z-Space-Society/Corliss)
  at the pinned `corliss_version`
- **Applied by:** [`provision.yml`](../../ansible/provision.yml) (configure
  play, `hosts: corliss`, **after** the postgres play)
- **Target:** the `corliss` CT (application tier, 120–129) over SSH,
  internal-only on `vmbr1`

## Purpose

A native install: a Django app (gunicorn WSGI, dedicated venv) run under
systemd — **no Docker** (per the [prime directive](../../CLAUDE.md)). It is
**internal-only** on `vmbr1`; the LAN reaches it through the
[`proxy`](proxy.md) edge — and unlike every other service it is fronted at the
**apex** `{{ cluster_domain }}`, not a subdomain (ADR-0006: its OIDC issuer is
the bare origin, so discovery has to live at `<issuer>/.well-known/…`). Its
state (member identities, OAuth/OIDC tokens) lives in Postgres, so the CT
itself holds nothing unreproducible except the two signing keys (see Secrets).

**Installed like every other role: a pinned upstream release.** The app used to
be a sub-app of this repo (`apps/zai-auth/`), rsync'd off the control node's
checkout; it now lives in its own repo and the role `git clone`s it at the tag
in `corliss_version`. Updating the app is therefore a *reviewable version bump*
in `defaults/main.yml` plus a role replay — not a side effect of `git pull` on
CT 100.

### URL surface

| Path | Serves |
| ---- | ------ |
| `/` | the landing page — public brochure signed out, your standing signed in. **Open to everyone by design:** it is where a refused non-member lands, and the only page that explains why and offers the way to ask |
| `/auth/login`, `/auth/logout`, `/auth/oauth/callback` | the human + ATProto-client surface. Anyone with an atproto handle may sign *in*; membership is a separate question, asked below |
| `/auth/client-metadata.json` | ATProto client metadata — **this URL is the `client_id`** |
| `/.well-known/openid-configuration`, `/.well-known/jwks.json` | OIDC discovery + JWKS (root-scoped: issuer is the bare origin) |
| `/oidc/authorize`, `/oidc/token` | OIDC provider endpoints (reached via discovery). **`authorize` is membership-gated** — it is the handoff into open-webui and is re-checked on every exchange, so a session that predates the gate, or a member revoked since signing in, is refused here rather than walking into chat |
| `/api/` | the member's own API keys — issue, list, revoke, and usage, read live from LiteLLM. Membership-gated, and *issuing* needs a real grant on top of that: a roster admin reaches the page but gets no key |
| `/membership/events` | the SCN registry's membership push — bearer-authed, machine-to-machine (see [Secrets](#secrets)) |
| `/membership/apply` | the other end of the same subject, and the only thing a signed-in **non**-member can do here: writes a `membership.request` record into the applicant's **own PDS** (corliss already holds their tokens from login, so it writes there directly). Confers nothing — the record asks, and only an admin's grant in the registry space answers. `v0.8.0`+ |
| `/manage/` | the cluster console — the application queue, member roll, admin roster, and the reconcile button. Gated on the atproto roster (`is_cluster_admin`), **not** on any Django flag and **never on membership**, so it stays reachable on a rebuilt cluster with an empty membership cache. The queue lists only applications still awaiting a decision (`v0.8.1`+); approving still happens in [`manage_console`](manage_console.md), which this otherwise supersedes |
| `/systems/` | the stack, and (eventually) whether it is up. A stub: it lists the services and reports "unknown" rather than guessing. Cluster-admin gated, 404 otherwise |
| `/admin/` | Django admin (break-glass account). **Never membership-gated:** `did:local:admin` is not on the roster and will never have a cache row, so a gate here would lock out the recovery account |

Membership enforcement is a per-view check in corliss, deliberately not
middleware — middleware covers every path by default, and `/manage/` and
`/admin/login/` are exactly the doors that must keep working when membership is
what is broken. A roster admin passes the gate with **no cache row**, which is
what lets a rebuilt cluster be entered at all; they still receive no tier, so
nobody gains an entitlement the registry never granted.

## Tasks

| Task | Module | Why |
| ---- | ------ | --- |
| Probe + create the `corliss` PG role | `command`/`shell` → `su - postgres -c psql`, `delegate_to: postgres` | Same idiom as litellm/open-webui/happyview: the bare postgres superuser is **peer-only**. Probe `pg_roles` → `CREATE ROLE` (else `ALTER ROLE` to sync the password). `no_log`. |
| Probe + create the `corliss` database | `command` → `su - postgres -c psql`, `delegate_to: postgres` | `CREATE DATABASE` can't run in a transaction → probe `pg_database`, then create `OWNER corliss`. |
| Create `corliss` group + user | `group`, `user` | Run the daemon unprivileged, no login shell. |
| Create home/src/config/keys dirs | `ansible.builtin.file` | `/opt/corliss` (+ `src/`, daemon-owned), `/etc/corliss` (+ `keys/`, root-owned, group-readable). |
| Ensure `git` is present | `apt` | Needed for the app checkout. |
| Mark the checkout `safe.directory` | `command` → `git config --system --replace-all safe.directory` | git runs as root but the tree is owned by the `corliss` service user, and git refuses a repo whose worktree belongs to someone else ("dubious ownership"). It bails before reading `.git/config`, so the symptom is the misleading `'origin' does not appear to be a git repository`. See [Notes](#notes). |
| Clone the app at its pinned ref | `ansible.builtin.git` (`version: {{ corliss_version }}`, `force: true`) | Checks out `Z-Space-Society/Corliss` into `{{ corliss_src }}`. Public repo over the host's NAT — no credentials, nothing in the vault. `force` discards any on-CT drift so the checkout is exactly the pinned tag (the guarantee rsync's `delete: true` used to give). Notifies restart. |
| Probe + install pinned `uv` | `command`, then `get_url`/`unarchive`/`copy` | Install uv reproducibly from the **pinned, checksummed** release tarball (not `curl \| sh`), same idiom as `open-webui`. Skipped when the installed `uv --version` already matches. |
| Sync the venv from `uv.lock` | `command` → `uv sync --locked --no-dev` (`chdir: {{ corliss_src }}`) | One task builds the whole environment: creates the venv, fetches a managed CPython of the version in the checkout's `.python-version`, and installs the exact tree in `uv.lock`. `--locked` **fails** the run if the lock is stale against `pyproject.toml` instead of quietly resolving something else — that's what makes `corliss_version` a pin of the whole dependency tree. `UV_PROJECT_ENVIRONMENT` + `UV_PYTHON_INSTALL_DIR` are load-bearing — see [Notes](#notes). Notifies restart. |
| Confirm the base interpreter is under `/opt/corliss` | `command` → `python -c 'sys._base_executable'` (`failed_when`) | Guards `UV_PYTHON_INSTALL_DIR`: an interpreter outside `corliss_home` is invisible to the daemon under `ProtectHome=true`. Fail loud at provision time, not as a cryptic unit-start failure. Same guard as `open-webui`. |
| Collect static assets | `command` → `manage.py collectstatic --noinput` | Populates `STATIC_ROOT` (whitenoise, installed above, serves it straight out of gunicorn — no separate nginx-for-statics box) with the login/account pages' `base.css` + vendored fonts. No DB/secrets needed, just a loadable settings module. Notifies restart. |
| Render the two signing keys | `copy` (`content:`, `0640` root:corliss, `no_log`) | EC P-256 (atproto DPoP/ES256) + RSA (OIDC id_token/RS256) PEMs from the cached `group_vars` secrets — see Secrets. Group-readable: Django reads these paths itself. Notifies restart. |
| Render the secret env file | `template` (`0600` root, `no_log`) | `DATABASE_URL`, `SECRET_KEY`, key paths, `PUBLIC_BASE_URL`, `OIDC_CLIENT_ID/SECRET`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `MEMBERSHIP_PUSH_TOKEN`, the outbound links (`CHAT_URL`, `API_URL`, `MANAGE_URL`), `SCN_SERVICE_DID`, the three `MEMBERSHIP_REGISTRY_*` reconciliation settings, and `OIDC_BACKCHANNEL_LOGOUT_URI`. Read by systemd via `EnvironmentFile`. Notifies restart. |
| Apply database migrations | `command` → `manage.py migrate --noinput` (`no_log`) | Provision-time, before the daemon ever starts — same principle as litellm's `prisma migrate deploy`. Idempotent (`changed_when` on "No migrations to apply"). Notifies restart. |
| Ensure the break-glass local admin exists | `command` → `manage.py ensure_admin` (`no_log`) | Idempotently creates a local username/password superuser (`admin`, no ATProto identity) as a way in if OIDC/ATProto login is ever broken. Only sets the password at creation (never rotates it on re-runs); re-asserts `is_staff`/`is_superuser` on every run so provisioning re-runs heal it if those flags ever get flipped off. See [Notes](#notes). |
| Chown the home to `corliss` | `ansible.builtin.file` (`recurse`) | clone/sync/migrate ran as root; the daemon reads the venv, the uv-managed interpreter and the cloned source. Runs *after* install/migrate, so it covers the interpreter too. |
| Install the systemd unit | `template` → `/etc/systemd/system/corliss.service` | Hardened (`ProtectSystem=strict`, `ReadWritePaths={{ corliss_home }}`); `ExecStart` runs gunicorn against `corliss.wsgi:application`. Notifies reload + restart. |
| Ensure started + enabled | `ansible.builtin.systemd` | Running now + on boot. |
| Flush handlers | `meta: flush_handlers` | Bring the daemon up with final config before the smoke test. |
| Wait for the port + health check | `wait_for` (`127.0.0.1:8000`) + `uri` (`/.well-known/openid-configuration`) | The discovery doc proves the app booted, reached the migrated DB, *and* the signing keys loaded (`signing.py` fails closed on a missing key) — not merely that the port is open. |

### Handlers

| Handler | Action |
| ------- | ------ |
| `reload systemd` | `systemd: daemon_reload=true` |
| `restart corliss` | `service: name=corliss state=restarted` |

## Variables

Defined in [`defaults/main.yml`](../../ansible/roles/corliss/defaults/main.yml):

| Variable | Default | Meaning |
| -------- | ------- | ------- |
| `corliss_repo_url` | `https://github.com/Z-Space-Society/Corliss.git` | Upstream app repo (public — the clone needs no credentials). |
| `corliss_version` | `v0.8.1` | The **pinned tag** cloned onto the CT. `v0.8.1` narrows the `/manage/` application queue to what is still awaiting a decision, and shows a signed-in non-member the Chat and API nav entries as disabled rather than as a live link the gate would refuse. `v0.8.0` makes the apply button work — it writes the application into the applicant's own PDS, needing no new env key and no migration, and **narrows the OAuth scope** from `transition:generic` to `repo:network.sharedcomputer.membership.request`, so every existing member re-consents on their next sign-in (old tokens still read; a member applying before re-authorising is told to sign in again). A pin below `v0.8.0` leaves the button inert, as it was. `v0.7.0` issues members' LiteLLM API keys from `/api/` and needs the three `LITELLM_*` keys in `corliss.env.j2`; a pin below it ignores them, and a pin at or above it with them unset leaves `/api/` saying it is not configured. `v0.6.1`/`v0.6.0` end the relying party's session on revocation (needs `OIDC_BACKCHANNEL_LOGOUT_URI` and the `redis` CT); `v0.5.0` closes GATE; `v0.4.1` presents the registry's public Host on the internal call, without which every reconcile fails 421; `v0.4.0` reconciles membership and serves `/manage/`, needing the `MEMBERSHIP_REGISTRY_*` keys; `v0.3.2` adds the home page, `/api/` and the nav's identity/admin menu, and reads the admin roster from `SCN_SERVICE_DID`; `v0.3.0` added atproto membership caching. Bumping this is how the app is upgraded — and since `v0.2.1` the deployed site's footer stamps the tag it's running (resolved by `git describe --tags` off the checkout), so the pin is verifiable from a browser. Floor is `v0.2.0`, the uv-project release (pyproject + `uv.lock`, Python 3.14, Django 6.1) this role's `uv sync --locked` requires. |
| `corliss_port` / `corliss_host` | `8000` / `0.0.0.0` | gunicorn's bind; `0.0.0.0` so Caddy can reach it from the proxy CT. |
| `corliss_gunicorn_workers` | `2` | gunicorn worker count. |
| `corliss_home` / `corliss_src` / `corliss_venv` | `/opt/corliss[/src,/venv]` | Cloned source + venv, all under the chowned tree. `uv sync` is pointed at `corliss_venv` via `UV_PROJECT_ENVIRONMENT` — see [Notes](#notes). |
| `corliss_python_install_dir` | `/opt/corliss/python` | Where uv puts the managed CPython — **under** `corliss_home` deliberately, so the recursive chown owns it and `ProtectHome=true` doesn't hide it. |
| `corliss_uv_http_timeout` | `180` | Per-request timeout (s) for uv's downloads; uv's 30s default can drop the interpreter tarball on the NAT'd link. Far below `open-webui`'s `900` — corliss has no torch-sized wheel. |
| `corliss_config_dir` / `corliss_keys_dir` | `/etc/corliss[/keys]` | Env file + the two signing-key PEMs. |
| `corliss_db_name` / `corliss_db_user` | `corliss` | The Postgres database + role this role creates. |
| `corliss_url` | `https://{{ cluster_domain }}` | `PUBLIC_BASE_URL` — the **apex**, anchoring the atproto `client_id`, OIDC issuer, and redirect/JWKS URLs. Changing it mints a new atproto client identity. |
| `corliss_allowed_hosts` | `[{{ cluster_domain }}, 127.0.0.1, <this CT's IP>]` | Django's `ALLOWED_HOSTS`. The apex for browser traffic through Caddy, loopback for the provision-time smoke test, and this CT's own internal address because the SCN registry pushes membership events **CT-to-CT over `vmbr1`**, so those POSTs carry a bare-IP `Host`. Django 400s an unlisted `Host` before the view runs — see [Notes](#notes). |
| `corliss_oidc_client_id` | `open-webui` | Local default — keep in sync with `open-webui`'s own `openwebui_oidc_client_id`. |
| `corliss_oidc_redirect_uris` | `[https://chat.{{ cluster_domain }}/oauth/oidc/callback]` | Open WebUI's OIDC callback, registered on this side. |
| `corliss_oidc_backchannel_logout_uri` | `http://{{ hostvars['open-webui'].ansible_host }}:8080/oauth/backchannel-logout` | Where corliss POSTs a signed `logout_token` on sign-out and on revocation — what makes revocation immediate instead of bounded by Open WebUI's own `JWT_EXPIRES_IN`. **Internal**, unlike the redirect URI above: that one is a URL a member's browser follows, this is a call between two CTs on one bridge (same distinction as `corliss_membership_registry_url`). Needs corliss ≥ v0.6.0 and the [`redis`](redis.md) CT, or Open WebUI cannot revoke the JWT it already issued. |
| `corliss_openwebui_port` | `8080` | Open WebUI's listen port — a local default (not read from that role's defaults); keep in sync with `openwebui_port`. |
| `corliss_chat_url` | `https://chat.{{ cluster_domain }}` | Drives the login/account pages' nav "Chat" link — same `cluster_domain` derivation as `corliss_url`, different subdomain. |
| `corliss_api_url` | `https://api.{{ cluster_domain }}` | Shown on `/api/` as the base URL for a **member** to point their client at — the same origin the `litellm` route serves. Not what Corliss itself calls; that is `corliss_litellm_url` below, and conflating them breaks key issuing silently. |
| `corliss_manage_url` | `https://manage.{{ cluster_domain }}` | The home page's "Manage Console" link (admins only) — the same origin the [`manage_console`](manage_console.md) route serves. |
| `corliss_happyview_url` | `https://view.{{ cluster_domain }}` | The Manage menu's "HappyView Admin" link (admins only). A browser href, so the public origin — **not** `corliss_membership_registry_url`, which is the internal address Corliss calls. |
| `corliss_proxmox_url` | `https://{{ proxmox_api_host }}:8006` | The Manage menu's "Proxmox Admin" link. Derived from the host's LAN address in the **vault** (recorded by `bootstrap.sh`), not from `cluster_domain`: the Proxmox UI runs on the host, not a CT, so it is not a `caddy_proxy_hosts` route and has no subdomain. The one admin link that points at the LAN rather than through the edge — expect a self-signed-cert warning, and no reachability from outside. Blank hides it. |
| `corliss_litellm_url` | `http://{{ hostvars['litellm'].ansible_host }}:4000` | Where **Corliss** reaches LiteLLM to provision members and mint their keys. Deliberately the **internal** address, not `corliss_api_url` — see below. |
| `corliss_litellm_port` | `4000` | Local mirror of `litellm_port`, not a reach into that role's vars (same convention as `corliss_openwebui_port`). Keep the two in sync. |
| `corliss_litellm_max_keys_per_member` | `5` | How many API keys one member may hold. LiteLLM enforces no per-user limit, so this cap is ours or there is none. |
| `corliss_litellm_provisioner_key` | *(file lookup)* | The `proxy_admin` virtual key minted by the [`litellm`](litellm.md) role, read from `/root/.zai-secrets/corliss_litellm_provisioner_key`. |
| `scn_service_did` | `""` | The SCN service DID whose repo holds the public admin roster; corliss reads it to decide who sees the admin block. **Not** a `corliss_*` var — it's the registry's identity, shared verbatim with `manage_console`, recorded once by `zai-set-console service_did <did>`. Blank is a working state (empty roster, nobody elevated). |
| `corliss_membership_registry_url` | `http://{{ hostvars['happyview'].ansible_host }}:3000` | The HappyView instance corliss reads membership back out of, for reconciliation. **Internal, over `vmbr1`** — not the public `view.<domain>` origin, which would route out through the host's NAT, across Cloudflare and back in via the proxy. This is the *recovery* path (it refills an empty cache at boot), so it must not depend on public DNS, Cloudflare and the proxy CT all being up. Derived from the inventory with the same `hostvars[...]` expression the Caddyfile routes with, so the CTID assignment stays the single source; same coupling `CORLISS_PUSH_URL` already accepts, degrading the same way. |
| `corliss_membership_registry_port` | `3000` | HappyView's listen port, matching the `view.<domain>` entry in `caddy_proxy_hosts`. |
| `corliss_membership_registry_host` | `view.{{ cluster_domain }}` | The `Host` header corliss presents on that internal call. happyview routes by virtual host and answers **HTTP 421 "Unknown host"** to a request whose Host is a bare `10.1.1.x` — the edge normally preserves the public name on the way through, so going direct means presenting it ourselves. This is the one job the proxy was doing that the bypass has to take over. |
| `console_client_key` | `""` | The console's public, origin-bound HappyView client key, passed through for corliss's registry reads — one key, recorded once by `zai-set-console client_key <hvc_…>`, not a second copy under a second name. **Optional**: verified 2026-08-18 that HappyView dispatches to a Lua script with no session and no client key, so blank does **not** block reconciliation. It must not, or a cluster rebuilt before the console was configured could not recover its membership. |

### Secrets

`corliss_db_password`, `corliss_secret_key`, `corliss_oidc_client_secret`,
`corliss_admin_password`, `corliss_membership_push_token` and
`corliss_membership_registry_token` follow the
standard `password` lookup pattern in
[`group_vars/all/main.yml`](../../ansible/group_vars/all/main.yml) — generated
on first run, persisted under `/root/.zai-secrets`, stable across rebuilds.
`corliss_admin_password` is the **DR-critical** break-glass admin's password
(see [Tasks](#tasks) above) — the only way into `/admin/` if ATProto/OIDC
login is ever broken, so it belongs alongside the two signing keys below on
any escrow checklist.

`corliss_membership_push_token` is the one secret here that has to travel
**outward**, and it is the only reason a deploy of this role is not
self-sufficient. It authenticates the SCN registry's push to
`/membership/events`, so the identical value must also be set as
`CORLISS_PUSH_TOKEN` in the scn-ops checkout's `.env` on whichever machine
runs `npm run deploy` — that script, not Ansible, installs it as a HappyView
script variable. Read it off the control node with:

```bash
cat /root/.zai-secrets/corliss_membership_push_token
```

Not DR-critical: losing it costs a regenerate on both sides, nothing more. A
half-configured pair fails **closed** — corliss answers every push with a 503
while its side is unset, and the registry's Lua logs the failure and completes
the approval anyway, because the space record is the membership event and
corliss only holds a cache of it.

`corliss_membership_registry_token` travels **outward** the same way, to the
same place, and is set as `RECONCILE_TOKEN` in scn-ops' `.env` before
`npm run deploy`:

```bash
cat /root/.zai-secrets/corliss_membership_registry_token
```

It authenticates the *other* direction — corliss reading membership back out of
the registry through the `syncMembers` service door, which is how the cache is
rebuilt after a flash. Deliberately a **separate secret** from the push token
rather than a reuse of it: this one *reads* (a leak exposes the member roll),
the push token only asserts a cache update. Separate secrets rotate
independently, and widening one door cannot widen the other. Also fails closed
both ways — the Lua refuses every call while `RECONCILE_TOKEN` is unset, and
corliss's console shows reconciliation as unconfigured rather than erroring.

Not DR-critical either, with one caveat worth knowing: **reconciliation is the
membership half of the prime directive.** A rebuilt corliss has witnessed no
pushes and cannot be told about events that already happened, so without a
working pair here a flashed cluster comes back with an empty
`MembershipCache` and no scripted way to refill it.

The two signing keys (`corliss_atproto_ec_key`, `corliss_oidc_rsa_key`) are
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

`corliss_litellm_provisioner_key` is different in kind from the two above: it
does **not** travel outward and it is not generated by a lookup here. The
[`litellm`](litellm.md) role mints it — a `proxy_admin` virtual key belonging to
the `corliss-provisioner` LiteLLM user — and persists it to
`/root/.zai-secrets/corliss_litellm_provisioner_key`; this role reads it with a
file lookup. **That is why the litellm play runs before the corliss play** in
[`provision.yml`](../../ansible/provision.yml): on a fresh cluster the file does
not exist until litellm has run once, and the reverse order renders an empty
`LITELLM_PROVISIONER_KEY`, leaving `/api/` permanently unable to issue anything.

```bash
cat /root/.zai-secrets/corliss_litellm_provisioner_key
```

Not the master key, deliberately — a compromised Corliss costs one revocation
rather than a proxy-wide rotation. Not DR-critical: losing the file makes the
next run mint a replacement, though the old `proxy_admin` key stays live in
LiteLLM until deleted by hand, so it is worth pruning when that happens.

## Dependencies

- **[`postgres`](postgres.md)** must be provisioned first — this role connects
  to the postgres CT (`delegate_to: postgres`) to create its role+database. A
  full [`provision.yml`](../../ansible/provision.yml) run guarantees the
  order; a `--limit corliss` run still needs postgres already up.
- **[`litellm`](litellm.md)** must have run at least once — this role reads the
  provisioner key that role mints, and the tier teams it creates are what
  Corliss scopes members' keys to. A full `provision.yml` run guarantees the
  order (the litellm play sits immediately above this one); a `--limit corliss`
  run on a cluster where litellm has never run fails the file lookup outright,
  which is the loud failure and the right one.
- **Outbound HTTPS from the CT** — the clone reaches github.com through the
  host's NAT (`gw=10.1.1.1`), like every other role's package downloads.
- **[`open-webui`](open-webui.md)** is the one OIDC relying party today. It
  doesn't block corliss's provisioning, but Open WebUI logins won't work
  until both are up and its `OPENID_PROVIDER_URL`/`OAUTH_CLIENT_*` are
  pointed at corliss (already wired in `open-webui`'s own defaults/template).
- **[`proxy`](proxy.md)** exposes it to the LAN via `caddy_proxy_hosts` at the
  apex `{{ cluster_domain }}`; set the domain once with `zai-set-domain`. The
  origin cert must cover the apex — a wildcard-only `*.<domain>` cert does
  **not**, and Cloudflare answers such a request with a 526.

## Verify

```bash
ssh root@10.1.1.<ctid> 'systemctl is-active corliss'
ssh root@10.1.1.<ctid> 'ss -ltnp | grep 8000'                              # listening?
curl -fs http://10.1.1.<ctid>:8000/.well-known/openid-configuration        # discovery doc
ssh root@<postgres-ip> "su - postgres -c 'psql -l'" | grep corliss        # DB present
ssh root@10.1.1.<ctid> 'git -C /opt/corliss/src describe --tags'          # deployed version
curl -s https://<domain>/ | grep -o 'Corliss v[^<]*'                       # same, from the footer
curl -s https://<domain>/.well-known/openid-configuration | grep issuer    # issuer == bare apex
curl -s https://<domain>/auth/client-metadata.json | grep client_id        # client_id on /auth/

# The LiteLLM leg — the one most likely to be built and inert. Run FROM the
# corliss CT, because that is the only place the internal route and the
# provisioner key are both true. 200 required; anything else and /api/ renders
# but can never issue a key:
ssh root@10.1.1.<ctid> 'curl -s -o /dev/null -w "%{http_code}\n" \
  "$(grep ^LITELLM_URL /etc/corliss/corliss.env | cut -d= -f2)/key/list" \
  -H "Authorization: Bearer $(grep ^LITELLM_PROVISIONER_KEY /etc/corliss/corliss.env | cut -d= -f2)"'

# LiteLLM's picture of membership matches the cache (and the repair if not):
ssh root@10.1.1.<ctid> 'cd /opt/corliss/src && \
  /opt/corliss/venv/bin/python manage.py sync_litellm --dry-run'
# end-to-end: browse https://chat.<domain>, click the ZAI OAuth button,
# log in with an ATProto handle, confirm sub/handle/email land in Open WebUI
# and that local signup/login are gone.
```

## Notes

- **The venv is a `uv sync` of the app's own lockfile, and two env vars hold it
  together.** Corliss is a uv project: `pyproject.toml` pins every direct
  dependency exactly and the committed `uv.lock` pins the full tree including
  transitives, so `uv sync --locked --no-dev` reproduces the exact environment
  the tag was tested with — and *fails* if the lock has drifted from
  `pyproject.toml` rather than resolving something else behind your back. Two
  environment variables on that task are load-bearing:
  - `UV_PROJECT_ENVIRONMENT={{ corliss_venv }}` — without it uv builds `.venv`
    **inside the source tree**, and every consumer that addresses
    `/opt/corliss/venv` by path (the unit's `ExecStart`, `collectstatic`,
    `migrate`, `ensure_admin`, the chown) silently misses it.
  - `UV_PYTHON_INSTALL_DIR={{ corliss_python_install_dir }}` — the app needs
    Python 3.14 (its committed `.python-version`) and Debian 13 ships 3.13, so
    uv fetches a managed interpreter. Left at uv's default it lands in
    `/root/.local/share/uv/python`, which `ProtectHome=true` hides from the
    daemon. The follow-up `sys._base_executable` check fails the run if it ever
    lands outside `corliss_home`.

  There is deliberately **no `corliss_python_version` variable**: the app's
  `.python-version` is the single source of truth and uv reads it off the
  checkout. A role var would just be a second copy to drift. (Contrast
  `open-webui`, which installs a PyPI release and so has nowhere else to state
  the version.)
- **`DEV_LOGIN_ENABLED` must never reach this CT.** Corliss has a local-dev
  sign-in (`/auth/dev-login`) that mints a session for any handle typed into a
  form. It defaults off and is deliberately absent from `corliss.env.j2` — not
  set to `false`, just never templated. Belt and braces: the app's own system
  check `corliss.E001` hard-fails `manage.py check`/`migrate` if it is set
  without `DEBUG`, so this role's migrate task would fail the whole run rather
  than deploy an auth bypass. No Ansible assertion needed.
- **Granting admin to an ATProto member: `zai-make-admin <handle>`.** Promotes
  a handle to `is_staff`/`is_superuser` keyed on its DID (`manage.py
  make_admin`, backed by [`make-admin.yml`](../../ansible/make-admin.yml)) —
  resolves the handle via DNS TXT then HTTPS well-known, verifies against the
  DID document's `alsoKnownAs`, then `get_or_create`s on `did`. Because
  `corliss.views._upsert_member` also keys exclusively on `did` and
  never touches `is_staff`/`is_superuser`, a member promoted this way (before
  or after their first login) always lands on the same row and keeps admin
  rights — one account, admin from first login. This account never gets a
  password (`set_unusable_password()`); it only ever authenticates via
  ATProto OAuth, unlike the break-glass `admin` account above.
- **`/logout` ends this device's corliss session only.** Now reachable from
  the Account page's "Sign out" button (a plain GET link — no CSRF risk beyond
  forcing a re-login), but still no discovery-doc `end_session_endpoint` or
  relying-party integration (a member logging out of Open WebUI doesn't end
  their corliss session — that gap is what motivated adding this route at
  all: OIDC relying parties ending their own local session leaves corliss's
  session alive, so re-authenticating silently re-issues a token with no
  prompt). RP-initiated logout is still follow-up work.
- **The app has no `email` field to add — it's already on `AbstractUser`.**
  Sourcing it (via `transition:email` + `com.atproto.server.getSession`
  against the member's own PDS — DPoP tokens can't be proxied) is documented
  in [Corliss's own README](https://github.com/Z-Space-Society/Corliss).
- **`ProtectSystem=strict` doesn't hide `/etc/corliss`.** Strict makes `/`
  *read-only*, not inaccessible — the env file and signing keys stay readable
  without needing `ReadWritePaths`; only `corliss_home` needs write access
  (nothing under `/etc` is written at runtime).
- **"'origin' does not appear to be a git repository" means ownership, not
  networking.** git refuses to operate on a repo whose worktree is owned by a
  different user than the one running it, and it aborts *before* parsing
  `.git/config` — so a perfectly good clone with a correct `origin` remote
  reports a remote-access error. `git -C {{ corliss_src }} remote -v` printing
  `detected dubious ownership` is the tell. The role sets `safe.directory` for
  this path; if you ever move the checkout, that setting has to move with it.
- **The atproto `client_id` IS a URL** (`<base>/auth/client-metadata.json`), so
  changing `corliss_url` *or* that path mints a new client identity and every
  member has to re-consent at their PDS. Bundle such moves into one cutover.
- **Upgrading the app is a version bump.** Tag a release in the Corliss repo
  (its `bin/release` bumps `pyproject.toml`, re-locks, tests and tags in one
  step), bump `corliss_version`, replay the role. `git pull` on CT 100 updates
  the blueprint but never the app — deliberate: the app's version is now a
  reviewable line in git, not a side effect of when provisioning last ran.
  Since `v0.2.1` the live footer reports the tag it's serving, so the bump can
  be confirmed from a browser rather than over SSH. That stamp is
  `git describe --tags` run *inside* the CT's checkout, which is why the clone
  is full and unshallow — adding `depth:` to the git task would degrade the
  footer to a bare SHA.
- For how the CT is assigned a CTID, created and reached, see
  [`provision.yml`](../../ansible/provision.yml) and the
  [main docs](../README.md#service-ctid-assignment).
