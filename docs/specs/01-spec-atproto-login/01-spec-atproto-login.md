# 01-spec-atproto-login.md

> Status: Draft spec (not yet an ADR). Companion to: Research/ATProto (conceptual
> overview). Scope: **authentication only** — knowledge base / notes /
> collaborative editing are parked separately ("Open WebUI Native Capabilities",
> TBD).

## Introduction/Overview

ZAI members today need a separate Open WebUI account. This feature lets them sign
in with their **ATProto handle** instead: the app runs its own ATProto OAuth
client (handle → PDS authorize → authenticated) and mints an OIDC `id_token` that
Open WebUI consumes, so one DID-keyed identity is reusable across cluster
services. The primary goal of **this** spec is a self-contained Django
application — living at `apps/zai-auth/` and runnable locally — that takes a
member from a handle to an authenticated session with a persisted identity, ready
to be consumed by Open WebUI as an OIDC provider. We are deliberately building
our own ATProto OAuth client rather than delegating to a bridge (e.g. `aip`); if
that proves too costly, delegation is reconsidered later.

## Goals

- A member authenticates to the cluster using only their ATProto handle (no
  cluster-local password), ending in an authenticated Django session.
- A single identity is persisted, **keyed by DID** (stable across handle
  changes), reusable across services starting with Open WebUI.
- The app publishes a valid ATProto OAuth client (`client-metadata.json`) and a
  JWKS endpoint, and mints OIDC `id_token`s verifiable against that JWKS.
- The full login flow works **end-to-end locally** (`manage.py runserver`)
  against a real PDS, with the identity created/updated on each login.
- The implementation is our own ATProto OAuth client covering PAR + DPoP + PKCE +
  `private_key_jwt` — not a third-party bridge.

## User Stories

- **As a cluster member**, I want to log in with my ATProto handle so that I
  don't have to manage a separate Open WebUI account.
- **As a cluster member**, I want one identity that works across cluster services
  so that signing in once is enough.
- **As an operator**, I want identities keyed by DID rather than handle so that a
  member renaming their handle never orphans or duplicates their account.
- **As an operator**, I want member sessions held server-side (Django session
  cookie, not a browser-held JWT) so that revoking access is simple and less
  token material is exposed in the browser.

## Demoable Units of Work

Four vertical slices, each building on the last (1 → 2 → 3 → 4). Because this
spec is **app-only**, proof artifacts are local: test runs, `runserver` requests,
and screenshots — not cluster URLs.

### Unit 1: Project skeleton + DID-keyed identity model

**Purpose:** Establish the `apps/zai-auth/` Django project and the custom user
model everything else references. Serves operators (a clean, reproducible app
subtree) and is the foundation for persistence.

**Functional Requirements:**
- The system shall provide a Django project under `apps/zai-auth/` configured via
  environment variables (database, secret/key paths) with no secrets committed.
- The system shall define a custom user model named `User` (subclassing
  `AbstractUser`, wired as `AUTH_USER_MODEL`) with: `did` (unique, immutable,
  the identifier everything keys on), `username` (the handle, mutable, refreshed
  on login), `pds_url`, and `last_seen`. Password auth is unused.
- The system shall persist and migrate this model against the configured database
  (Postgres via config; see Technical Considerations).
- The system shall expose the model in Django admin for inspection.

**Proof Artifacts:**
- CLI: `manage.py makemigrations && manage.py migrate` on a fresh database
  succeeds — demonstrates the custom user model applies cleanly from scratch.
- Test: a model test creating a `User` with a DID and asserting DID uniqueness
  passes — demonstrates the identity contract.
- Screenshot: Django admin showing the `User` with `did`/`username`/`pds_url`
  fields — demonstrates the persisted identity shape.

### Unit 2: Signing key + published client metadata & JWKS

**Purpose:** Stand up the cryptographic identity of the OAuth client: one keypair
whose private half signs DPoP / client assertion / id_token, and whose public
half is published so the PDS and Open WebUI can verify. Prerequisite for both the
login flow and the OIDC provider.

**Functional Requirements:**
- The system shall load its private signing key from a configurable path/env var
  (generated out-of-band, never committed) and fail clearly if absent.
- The system shall serve a `client-metadata.json` document at the `client_id` URL
  describing the OAuth client per the ATProto client-metadata schema.
- The system shall serve a JWKS endpoint exposing only the **public** half of the
  signing key.

**Proof Artifacts:**
- CLI: `curl localhost:8000/<jwks-path>` returns a JWKS containing the public key
  (no private material) — demonstrates verifiers can fetch the key.
- CLI: `curl localhost:8000/client-metadata.json` returns a schema-valid client
  metadata document — demonstrates the client is publishable.
- Test: a test asserting the served JWKS matches the configured key and contains
  no private fields — demonstrates safe key publication.

### Unit 3: ATProto OAuth login flow (handle → session)

**Purpose:** The core slice — a member enters a handle and ends up in an
authenticated Django session with their identity persisted. Serves members
directly.

**Functional Requirements:**
- The system shall accept a handle, resolve it to a DID, fetch the DID document,
  and discover the PDS authorization server
  (`/.well-known/oauth-authorization-server`).
- The system shall initiate authorization via **PAR** with **PKCE**, redirect the
  member to their PDS, and handle the callback.
- The system shall perform a **DPoP-bound** token exchange authenticating to the
  token endpoint with a **`private_key_jwt`** client assertion.
- The system shall create the `User` on first login (keyed by DID) and update
  `username` (handle), `pds_url`, and `last_seen` on every login.
- The system shall establish a **Django session** for the member and hold refresh
  tokens + DPoP key material **server-side**.

**Proof Artifacts:**
- Screenshot/recording: full login via `runserver` against a real handle, landing
  on an authenticated page with a session cookie set — demonstrates end-to-end
  auth.
- CLI: post-login `manage.py shell` / admin query showing the `User` row with the
  resolved DID and an updated `last_seen` — demonstrates identity persistence.
- Test: callback handling tests for the success path and the CSRF/`state`-mismatch
  rejection path pass — demonstrates the flow and its guardrails (tokens redacted
  in fixtures).

### Unit 4: OIDC provider endpoint (id_token for Open WebUI)

**Purpose:** Expose the authenticated identity to Open WebUI as a standard OIDC
provider, so Open WebUI's OAuth signup can create/recognize the account.

**Functional Requirements:**
- The system shall expose OIDC provider endpoints (discovery + authorize + token)
  that an OIDC client (Open WebUI) can complete a flow against, reusing the
  member's Django session.
- The system shall mint an `id_token` (JWT, **RS256**) carrying `sub` = DID and
  the `handle`, plus standard `iss`/`aud`/`exp` claims.
- The system shall make the `id_token` verifiable against the Unit 2 JWKS
  endpoint.
- The system shall document the exact Open WebUI OIDC client configuration
  (issuer URL, client id/secret, scopes, claim mapping) needed to consume it.

**Proof Artifacts:**
- CLI: a script/test fetching JWKS and verifying a freshly minted `id_token`'s
  signature and claims (`sub`=DID, `handle`) passes — demonstrates a valid,
  verifiable token.
- Doc: `apps/zai-auth/README.md` section with the Open WebUI OIDC settings —
  demonstrates the integration is reproducible by an operator.
- Test: id_token claim/signature test passes — demonstrates the provider contract.

## Non-Goals (Out of Scope)

1. **Deployment to the cluster**: no new CT, Ansible role, nginx vhost, or TLS in
   this spec — the app runs locally (`runserver`). Deployment is a later spec.
2. **Standing up prerequisites**: Open WebUI (CT 104) and Postgres (CT 102) are
   **assumed to exist**; this spec neither provisions nor configures them.
3. **LiteLLM tier/key issuance**: no `tier` field or tier-driven provisioning in
   Phase 1 (deferred with the rest of LiteLLM integration).
4. **Bluesky List-driven membership**: no allow/deny based on a Bluesky list.
5. **SSO beyond Open WebUI**: only Open WebUI is targeted as an OIDC consumer.
6. **Knowledge base / notes / collaborative editing**: parked separately.
7. **Community-PDS (non-technical) onboarding**: authorizing against the
   community's own PDS ties to tiered onboarding and is deferred.

## Design Considerations

Minimal UI: a single **handle-entry login page**, the PDS redirect (rendered by
the PDS, not us), and a post-login authenticated landing page. No bespoke visual
design is required beyond a functional login form and clear error states (e.g.
unresolvable handle, denied authorization). No mockups exist; follow Django's
default templating unless a cluster style emerges.

## Repository Standards

- **Reproducibility first** (repo prime directive): the app lives at
  `apps/zai-auth/`, is configured entirely via environment variables, and commits
  **no secrets** — keys and tokens load from configurable paths/env. A committed
  `.env.example` documents required variables.
- **New Python/Django subtree**: establish conventions here — pinned dependencies
  (e.g. `requirements.txt`/`pyproject.toml`), settings driven by env, and a test
  suite runnable with one command. Match the existing repo ethos: comments
  explain *why*, not *what*.
- **Validate before declaring done** (CLAUDE.md): migrations must apply from
  scratch and the test suite must pass before any unit is considered complete.
- **App-level docs**: maintain `apps/zai-auth/README.md` (run locally, env vars,
  Open WebUI OIDC config). The `docs/` tree + roles table are updated when the
  *deployment* spec adds a role/CT — out of scope here, but flagged so it isn't
  forgotten.

## Technical Considerations

- **Token strategy** (the *how* behind the units):
  - **DPoP proofs** (JWT) signed per-request with the app's key — required by
    ATProto.
  - **Client assertion** (`private_key_jwt`) to authenticate the confidential web
    client to the PDS token endpoint.
  - **OIDC `id_token`** (JWT, **RS256**) minted by Django, consumed by Open WebUI,
    validated via the JWKS endpoint.
  - **Member session** = Django's native session cookie (not a JWT) for simple
    revocation and reduced browser-side token exposure.
- **Custom user model** must be set as `AUTH_USER_MODEL` **before the first
  migration** — switching later is painful, so Unit 1 lands it first.
- **Database**: Postgres, reached via a configurable connection string; the
  instance is assumed provisioned (the operator stands it up before this work).
- **Key loading**: private key from a configurable path/env; JWKS derived from it.
  *Where the key physically lives in production* (on-box file vs. Ansible Vault)
  is a deployment-spec decision.
- **Server-side token handling**: refresh tokens and DPoP key material are stored
  server-side, never sent to the browser.
- **Local-dev `client_id` constraint**: ATProto requires `client_id` to be a
  public HTTPS URL hosting `client-metadata.json`. Local `runserver` testing
  therefore relies on ATProto's localhost client-development convention or a
  temporary public tunnel; the real public hostname/TLS is deferred (Open
  Question).
- **OAuth library**: undecided (Open Question). Candidates: `requests_oauth2client`
  (reported to cover DPoP) and Bluesky's Flask "hard way" demo as a reference
  implementation. Whatever is chosen must support **PAR + DPoP + PKCE**.

## Security Considerations

- **Private signing key** is never committed; it loads from a configurable
  path/env and the app fails closed if it's missing.
- **Refresh tokens + DPoP keys** are held server-side only, not exposed to the
  browser; the session cookie should be `HttpOnly`/`Secure` (Secure enforced once
  TLS exists in deployment).
- **CSRF/interception defenses**: PKCE + a `state` parameter validated on
  callback; mismatches are rejected (covered by a Unit 3 test).
- **No password auth**; `email` is unused and must **not** be treated as an
  identifier (DID is the identity).
- **Proof artifacts must not leak secrets**: redact real tokens, keys, and DPoP
  material from screenshots, logs, and test fixtures before committing.
- Only a **placeholder domain** appears in committed config until the real
  hostname is decided.

## Success Metrics

1. **End-to-end login**: a member logs in via handle on `runserver`, a Django
   session is established, and a DID-keyed `User` row is created on first login
   and `last_seen`-updated on subsequent logins.
2. **Publishable client**: `client-metadata.json` and JWKS endpoints return
   schema-valid documents, and a minted `id_token` verifies (signature + `sub`,
   `handle` claims) against the JWKS.
3. **Reproducible from scratch**: migrations apply cleanly on an empty database
   and the full test suite passes with a single command.

## Open Questions

1. **OAuth library**: which Python library supports PAR + DPoP + PKCE cleanly in
   Django? Verify (`requests_oauth2client`, Bluesky Flask demo as reference)
   before committing.
2. **Open WebUI without `email`**: confirm Open WebUI's OAuth signup can
   provision/recognize an account from `sub`=DID + `handle` **without** an
   `email` claim. If it cannot, revisit and synthesize an email-shaped claim.
3. **Public hostname + TLS**: where `client-metadata.json` / JWKS / the OIDC
   issuer are publicly served (and how TLS terminates) — a deployment-spec
   decision; placeholder hostname used for now.
4. **Production storage at rest**: where the signing key and the server-side
   refresh/DPoP material live in production (on-box file vs. Vault vs. encrypted
   DB) — deployment-spec decision.
5. **Local-dev `client_id`**: confirm the approach for satisfying ATProto's
   public-HTTPS `client_id` requirement during local `runserver` testing
   (localhost dev convention vs. tunnel).
6. **`tier` field**: confirmed omitted from Phase 1 (deferred with LiteLLM); note
   if a present-but-unused column is wanted earlier.
7. **Community-PDS onboarding**: authorizing non-technical members against the
   community's own PDS — deferred, ties to tiered onboarding in Research/ATProto.
