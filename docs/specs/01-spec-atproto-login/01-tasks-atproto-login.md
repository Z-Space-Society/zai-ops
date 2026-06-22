# 01 Tasks - ZAI Auth: ATProto Login (Django)

Derived from [`01-spec-atproto-login.md`](01-spec-atproto-login.md). Scope is
**app-only** (runnable locally via `manage.py runserver`); deployment, Open WebUI,
and Postgres are prerequisites/Non-Goals. Parent tasks map 1:1 to the spec's four
demoable units and must be built in order (1 → 2 → 3 → 4).

## Relevant Files

- `apps/zai-auth/manage.py` - Django entrypoint for the app.
- `apps/zai-auth/pyproject.toml` (or `requirements.txt`) - Pinned dependencies
  (Django, Postgres driver, JWT/JOSE + crypto libs, the chosen ATProto OAuth
  library, env loader, test tooling).
- `apps/zai-auth/.env.example` - All required env vars with placeholders (no
  secrets): database connection, `SECRET_KEY`, signing-key path, public hostname,
  OIDC client id/secret. Mirrors what `settings.py` reads.
- `apps/zai-auth/.gitignore` - Ignore real `.env`, the private key file, and
  local SQLite/build artifacts.
- `apps/zai-auth/README.md` - Run-locally instructions, env vars, local-dev
  `client_id` strategy, and the Open WebUI OIDC config.
- `apps/zai-auth/zai_auth/settings.py` - Env-driven settings; sets
  `AUTH_USER_MODEL` before first migration.
- `apps/zai-auth/zai_auth/urls.py` - Root URL routing to the apps below.
- `apps/zai-auth/zai_auth/signing.py` - Shared key loading + JWK/`kid` derivation
  and JWT signing helpers (DPoP, client assertion, id_token).
- `apps/zai-auth/zai_auth/test_signing.py` - Tests for key loading + JWKS safety.
- `apps/zai-auth/accounts/models.py` - Custom `User(AbstractUser)` keyed by DID.
- `apps/zai-auth/accounts/admin.py` - Admin registration for `User`.
- `apps/zai-auth/accounts/migrations/` - Initial migration for the user model.
- `apps/zai-auth/accounts/test_models.py` - Identity-model tests.
- `apps/zai-auth/atproto/client.py` - Handle/DID resolution, PDS discovery, PAR,
  DPoP-bound token exchange with `private_key_jwt`.
- `apps/zai-auth/atproto/views.py` - Login (handle form) + callback views;
  `client-metadata.json` view.
- `apps/zai-auth/atproto/urls.py` - Routes for login, callback, client-metadata.
- `apps/zai-auth/atproto/templates/` - Login form + authenticated landing page.
- `apps/zai-auth/atproto/test_client.py` - Resolution/discovery/PAR/DPoP unit
  tests (mocked PDS, redacted tokens).
- `apps/zai-auth/atproto/test_views.py` - Callback success + `state`-mismatch
  rejection; create-vs-update user logic.
- `apps/zai-auth/oidc/provider.py` - id_token (RS256) minting with DID/handle
  claims.
- `apps/zai-auth/oidc/views.py` - OIDC discovery, authorize, token, and JWKS
  endpoints.
- `apps/zai-auth/oidc/urls.py` - OIDC routes (incl. `/.well-known/`).
- `apps/zai-auth/oidc/test_provider.py` - id_token sign/verify-against-JWKS +
  discovery-document tests.

### Notes

- This is a **new Python/Django subtree** — establish conventions here: pinned
  deps, env-driven settings, tests alongside the code they cover.
- Use the repository's "validate before done" rule: initial migrations must apply
  on a fresh database and the full test suite must pass before a parent task is
  considered complete. Run tests with the project's command (e.g.
  `cd apps/zai-auth && pytest` or `manage.py test`).
- **No secrets committed.** The private signing key and real `.env` are
  git-ignored; only `.env.example` (placeholders) is committed. Redact tokens/keys
  from any screenshot or fixture used as a proof artifact.
- **Local-dev `client_id`**: ATProto requires `client_id` to be a public HTTPS
  URL. For local `runserver` testing use ATProto's localhost client-development
  convention or a temporary tunnel; the real public hostname/TLS is deferred
  (spec Open Question #3/#5).
- **No-email risk**: claims are `sub`=DID + `handle` only; verify Open WebUI can
  provision an account without an `email` claim (spec Open Question #2) when
  wiring Task 4.

## Tasks

### [x] 1.0 Project skeleton + DID-keyed identity model

#### 1.0 Proof Artifact(s)

- CLI: `manage.py makemigrations && manage.py migrate` on a fresh database
  succeeds — demonstrates the custom user model applies cleanly from scratch.
- Test: a model test creating a `User` with a DID and asserting DID uniqueness
  passes — demonstrates the identity contract.
- Screenshot: Django admin showing a `User` with `did` / `username` (handle) /
  `pds_url` / `last_seen` — demonstrates the persisted identity shape.
- Diff: `apps/zai-auth/.env.example` showing required env vars (no secrets) —
  demonstrates env-driven, reproducible config.

#### 1.0 Tasks

- [x] 1.1 Scaffold the Django project under `apps/zai-auth/` (`manage.py`, the
      `zai_auth/` project package) and pin dependencies (Django + Postgres driver
      + env loader + test tooling) in `pyproject.toml`/`requirements.txt`.
- [x] 1.2 Make `settings.py` env-driven: database connection string, `SECRET_KEY`,
      signing-key path, public hostname, `DEBUG`, `ALLOWED_HOSTS` — all read from
      the environment.
- [x] 1.3 Create `.env.example` (placeholders, no secrets) covering every var
      `settings.py` reads, and a `.gitignore` for the real `.env`, the key file,
      and local artifacts.
- [x] 1.4 Create the `accounts` app and the custom `User(AbstractUser)` model
      (`did` unique/immutable, `username`=handle, `pds_url`, `last_seen`); set
      `AUTH_USER_MODEL` in settings **before** the first migration.
- [x] 1.5 Register `User` in Django admin (list/detail showing
      `did`/`username`/`pds_url`/`last_seen`).
- [x] 1.6 Generate and apply the initial migration against the configured
      Postgres.
- [x] 1.7 Set up the test harness and write model tests: `User` creation with a
      DID, DID-uniqueness constraint, handle stored in `username`.
- [x] 1.8 Start `apps/zai-auth/README.md` (run-locally + env-var section).

### [ ] 2.0 Signing key + published client metadata & JWKS

#### 2.0 Proof Artifact(s)

- CLI: `curl localhost:8000/<jwks-path>` returns a JWKS containing only the
  public key — demonstrates verifiers can fetch the key with no private material.
- CLI: `curl localhost:8000/client-metadata.json` returns a schema-valid ATProto
  client metadata document — demonstrates the client is publishable.
- Test: a test asserting the served JWKS matches the configured key and exposes
  no private fields passes — demonstrates safe key publication.
- Log: app fails clearly on startup when the signing key path is absent —
  demonstrates fail-closed key loading.

#### 2.0 Tasks

- [ ] 2.1 Implement `zai_auth/signing.py` to load the RSA private key from the
      configured path/env, failing closed with a clear error if missing.
- [ ] 2.2 Derive the public JWK (with a stable `kid`) from the private key for
      publication and for `id_token`/client-assertion signing.
- [ ] 2.3 Implement the JWKS endpoint (in `oidc/views.py`) returning only public
      key(s); wire its URL.
- [ ] 2.4 Implement the `client-metadata.json` view at the `client_id` URL per the
      ATProto client-metadata schema (`client_id`, `jwks_uri`, `redirect_uris`,
      `scope`, `token_endpoint_auth_method=private_key_jwt`,
      `dpop_bound_access_tokens=true`, …), driven by the configured hostname.
- [ ] 2.5 Add a dev key-generation helper/management command and document it
      (key generated out-of-band, never committed); add the key path to
      `.env.example` and `.gitignore`.
- [ ] 2.6 Tests: JWKS exposes no private fields and matches the configured key;
      `client-metadata.json` is schema-valid; missing-key startup fails clearly.

### [ ] 3.0 ATProto OAuth login flow (handle → authenticated session)

#### 3.0 Proof Artifact(s)

- Screenshot/recording: full login via `runserver` against a real handle, landing
  on an authenticated page with a session cookie set — demonstrates end-to-end
  auth (handle → DID resolution → PDS authorize → DPoP-bound token exchange).
- CLI: post-login `manage.py shell`/admin query showing the `User` row with the
  resolved DID and an updated `last_seen` — demonstrates identity persistence and
  first-login create / subsequent-login update.
- Test: callback tests for the success path **and** the CSRF/`state`-mismatch
  rejection path pass (tokens redacted in fixtures) — demonstrates the flow and
  its guardrails (PKCE + `state`).

#### 3.0 Tasks

- [ ] 3.1 Implement handle → DID resolution and DID-document fetch in
      `atproto/client.py`; extract the PDS endpoint.
- [ ] 3.2 Implement PDS authorization-server discovery
      (`/.well-known/oauth-authorization-server`).
- [ ] 3.3 Implement DPoP proof generation (per-request JWT signed with the app
      key, including server-nonce handling).
- [ ] 3.4 Implement the PAR request with PKCE (`code_challenge`) + `state`, and
      build the authorize redirect URL.
- [ ] 3.5 Implement the login view (handle form) and the callback view: validate
      `state`, then do the DPoP-bound token exchange with the `private_key_jwt`
      client assertion.
- [ ] 3.6 On callback success, create-or-update the `User` (key on DID; refresh
      `username`/`pds_url`/`last_seen`), establish the Django session, and store
      the refresh token + DPoP key material **server-side**.
- [ ] 3.7 Add the login template + authenticated landing page and handle error
      states (unresolvable handle, denied authorization).
- [ ] 3.8 Tests: callback success path (mocked PDS, redacted tokens) and
      `state`-mismatch rejection; create-vs-update user logic.

### [ ] 4.0 OIDC provider endpoint (id_token for Open WebUI)

#### 4.0 Proof Artifact(s)

- CLI/Test: a script/test fetching JWKS and verifying a freshly minted RS256
  `id_token`'s signature and claims (`sub`=DID, `handle`, plus `iss`/`aud`/`exp`)
  passes — demonstrates a valid, verifiable token.
- Doc: `apps/zai-auth/README.md` section with the exact Open WebUI OIDC client
  configuration (issuer URL, client id/secret, scopes, claim mapping) —
  demonstrates the integration is reproducible by an operator.
- Test: id_token claim/signature test passes — demonstrates the provider contract.

#### 4.0 Tasks

- [ ] 4.1 Implement `id_token` minting in `oidc/provider.py` (RS256, signed via
      `signing.py` with the published `kid`): `sub`=DID, `handle`, plus
      `iss`/`aud`/`exp`.
- [ ] 4.2 Implement the OIDC discovery endpoint
      (`/.well-known/openid-configuration`) advertising issuer, `jwks_uri`,
      authorize/token endpoints, and supported claims/scopes.
- [ ] 4.3 Implement the OIDC authorize endpoint reusing the member's Django
      session (redirect to login if unauthenticated).
- [ ] 4.4 Implement the OIDC token endpoint issuing the `id_token` to the OIDC
      client (Open WebUI).
- [ ] 4.5 Document the exact Open WebUI OIDC client config in `README.md` (issuer
      URL, client id/secret, scopes, claim mapping); note the no-`email`
      open-question/risk.
- [ ] 4.6 Tests: mint an `id_token` and verify its signature + claims against
      JWKS; assert the discovery document is correct.
