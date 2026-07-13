# ZAI Auth — ATProto Login (Django)

Members sign into the cluster with their **ATProto handle** instead of a separate
Open WebUI account. This Django app runs its own ATProto OAuth client (handle →
PDS authorize → authenticated), persists a DID-keyed identity, and exposes an
OIDC provider that Open WebUI consumes.

See the spec: [`docs/specs/01-spec-atproto-login/`](../../docs/specs/01-spec-atproto-login/).

> **Scope:** authentication only, **app-only** (runs locally). Deployment
> (CT/role/nginx/TLS), Open WebUI, and Postgres are prerequisites / later specs.

## Architecture

| App | Responsibility |
| --- | -------------- |
| `accounts` | DID-keyed custom `User` model (handle in `username`, `pds_url`, `last_seen`). |
| `atproto_oauth` | ATProto OAuth client: DID/handle resolution → PDS discovery → PAR → DPoP-bound token exchange; serves `client-metadata.json`. |
| `oidc` | OIDC provider (discovery, authorize, token) minting an RS256 `id_token`; serves the JWKS endpoint. |
| `zai_auth.signing` | Loads the signing keys and builds the JWKS (shared by both clients). |

Two signing keys, one JWKS: **ES256 (P-256)** for atproto DPoP + client assertion
(atproto mandates it) and **RS256 (RSA)** for the OIDC `id_token` (broad OIDC
client compatibility).

## Run locally

Requires [uv](https://docs.astral.sh/uv/) and a reachable Postgres. uv builds
the venv against Python 3.13 (Django 5.2's ceiling, and what the cluster's
Debian 13 CTs ship natively — see `docs/roles/zai-auth.md`), fetching that
interpreter itself if your machine doesn't have one.

```bash
cd apps/zai-auth
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -r requirements.txt

cp .env.example .env            # then edit DATABASE_URL etc.
createdb zai_auth               # or point DATABASE_URL at an existing DB
.venv/bin/python manage.py generate_keys   # dev signing keys, written to ./keys/

.venv/bin/python manage.py migrate
.venv/bin/python manage.py runserver
```

### Environment

All configuration is env-driven; see [`.env.example`](.env.example) for the full
list. `.env` is git-ignored — **never commit secrets or private keys**.
`CHAT_URL` (Open WebUI's public origin) drives the nav's "Chat" link — leave it
blank locally if you have no Open WebUI to point at.

### UI / static assets

The login and account pages share `templates/base.html` (nav + footer chrome)
and `static/css/base.css` (design tokens, ported from
[`docs/design_handoff_auth_page/`](../../docs/design_handoff_auth_page/)).
Fonts (Inter, IBM Plex Mono) are vendored under `static/fonts/` — no
`fonts.googleapis.com` call at runtime. Run `manage.py collectstatic` before
serving in production (whitenoise serves the collected output; see
`ansible/roles/zai-auth`'s task for the deployment step).

### Tests

```bash
.venv/bin/python manage.py test
```

## Open questions / known limitations

- **Local-dev `client_id`**: atproto requires `client_id` to be a public HTTPS
  URL hosting `client-metadata.json`. Local `runserver` testing relies on
  atproto's localhost client-development convention or a temporary tunnel; the
  real public hostname/TLS is a deployment-spec decision.
- **OIDC claims — email (resolved)**: the `id_token` now carries `email` +
  `email_verified` whenever the member's PDS supplied one. Sourced via the
  `transition:email` scope + `com.atproto.server.getSession` against the
  member's PDS directly (`atproto_oauth.client.fetch_session_email`) — the
  same approach Graze's AIP uses. It's best-effort: a member who declines the
  scope or has no email on their PDS simply gets no `email` claim.

## Open WebUI OIDC configuration

ZAI Auth is a standard OIDC provider. Point Open WebUI at it with these
environment variables (substitute `PUBLIC_BASE_URL` for the real public origin):

```bash
ENABLE_OAUTH_SIGNUP=true
OAUTH_CLIENT_ID=open-webui                       # must equal OIDC_CLIENT_ID here
OAUTH_CLIENT_SECRET=<shared secret>              # must equal OIDC_CLIENT_SECRET here
OPENID_PROVIDER_URL=https://PUBLIC_BASE_URL/.well-known/openid-configuration
OAUTH_PROVIDER_NAME=ZAI
OAUTH_SCOPES=openid email profile
OAUTH_USERNAME_CLAIM=handle                      # we emit `handle` (= the atproto handle)
OAUTH_EMAIL_CLAIM=email                          # present when the member's PDS supplied one
```

Register Open WebUI's redirect URI in this app's `OIDC_REDIRECT_URIS`
(e.g. `https://chat.example.com/oauth/oidc/callback`).

| Provider endpoint | Path |
| ----------------- | ---- |
| Discovery | `/.well-known/openid-configuration` |
| JWKS | `/.well-known/jwks.json` |
| Authorize | `/oidc/authorize` |
| Token | `/oidc/token` |

**id_token claims:** `sub` = DID, `handle` = atproto handle (also as
`preferred_username`), plus `iss`/`aud`/`exp`/`iat`/`nonce`, and `email` +
`email_verified` when the member's PDS supplied one. RS256, verifiable via
JWKS.

## Deployment

Cluster deployment (CT, systemd unit, secrets, Caddy route, Open WebUI wiring)
is handled by the [`zai-auth` Ansible role](../../docs/roles/zai-auth.md) — see
`docs/decisions/0005-zai-auth-over-aip.md` for why this app, not AIP, is the
cluster's login bridge.
