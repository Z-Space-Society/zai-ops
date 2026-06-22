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

Requires Python 3.13 and a reachable Postgres.

```bash
cd apps/zai-auth
python3.13 -m venv .venv
.venv/bin/pip install -r requirements.txt

cp .env.example .env            # then edit DATABASE_URL etc.
createdb zai_auth               # or point DATABASE_URL at an existing DB

.venv/bin/python manage.py migrate
.venv/bin/python manage.py runserver
```

### Environment

All configuration is env-driven; see [`.env.example`](.env.example) for the full
list. `.env` is git-ignored — **never commit secrets or private keys**.

### Tests

```bash
.venv/bin/python manage.py test
```

## Open questions / known limitations

- **Local-dev `client_id`**: atproto requires `client_id` to be a public HTTPS
  URL hosting `client-metadata.json`. Local `runserver` testing relies on
  atproto's localhost client-development convention or a temporary tunnel; the
  real public hostname/TLS is a deployment-spec decision.
- **OIDC claims**: the `id_token` carries `sub`=DID + `handle` only (no `email`).
  Verify Open WebUI can provision an account without `email`; if not, a
  synthesized claim will be added.

## Open WebUI OIDC configuration

ZAI Auth is a standard OIDC provider. Point Open WebUI at it with these
environment variables (substitute `PUBLIC_BASE_URL` for the real public origin):

```bash
ENABLE_OAUTH_SIGNUP=true
OAUTH_CLIENT_ID=open-webui                       # must equal OIDC_CLIENT_ID here
OAUTH_CLIENT_SECRET=<shared secret>              # must equal OIDC_CLIENT_SECRET here
OPENID_PROVIDER_URL=https://PUBLIC_BASE_URL/.well-known/openid-configuration
OAUTH_PROVIDER_NAME=ZAI
OAUTH_SCOPES=openid profile
OAUTH_USERNAME_CLAIM=handle                      # we emit `handle` (= the atproto handle)
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
`preferred_username`), plus `iss`/`aud`/`exp`/`iat`/`nonce`. RS256, verifiable via
JWKS.

> **Open Question (spec #2):** the `id_token` carries **no `email`**. Some Open
> WebUI versions require an email to provision an account (`OAUTH_EMAIL_CLAIM`).
> Verify against the target Open WebUI version; if it can't sign up without one, a
> synthesized email claim will be added here.
