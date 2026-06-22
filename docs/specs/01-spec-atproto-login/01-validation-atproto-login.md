# Validation Report — Spec 01: ATProto Login (Django)

Spec: [`01-spec-atproto-login.md`](01-spec-atproto-login.md) · Tasks:
[`01-tasks-atproto-login.md`](01-tasks-atproto-login.md) · Proofs:
[`01-proofs/`](01-proofs/)

## 1) Executive Summary

- **Overall: PASS** — no gates tripped (one MEDIUM caveat, below).
- **Implementation Ready: Yes** (for the spec's **app-only** scope). The one
  proof that could not be produced headlessly — the interactive browser login
  screenshot — has strong substitute evidence (live DID/PDS resolution against
  `bsky.social` + full mocked-flow tests), and depends on the deferred public
  HTTPS `client_id` anyway.
- **Key metrics:**
  - Requirements verified: **16 / 16 (100%)**
  - Proof artifacts working: **15 / 16 functional, 1 partial (manual)**
  - Files changed vs expected: all 23 "Relevant Files" present; extra files are
    standard Django scaffolding justified in commit messages.

**Gates:** A (no CRITICAL/HIGH) ✅ · B (no Unknown FRs) ✅ · C (proof artifacts
accessible) ✅* · D (changed files listed/justified) ✅ · E (repo standards) ✅ ·
F (no secrets) ✅. *C: one artifact is a documented manual step (MEDIUM).

## 2) Coverage Matrix

### Functional Requirements

| Requirement | Status | Evidence |
| --- | --- | --- |
| FR1.1 Django project `apps/zai-auth/`, env-driven, no secrets committed | Verified | `zai_auth/settings.py` (env reads); `.env.example`; `.gitignore`; commit `a671eb1` |
| FR1.2 Custom `User` (AbstractUser, `AUTH_USER_MODEL`) did/username/pds_url/last_seen | Verified | `accounts/models.py`; `settings.py:108`; `accounts/test_models.py` (5 tests) |
| FR1.3 Persist + migrate against configured DB | Verified | `migrate` on fresh Postgres (proof-01); `makemigrations --check` → "No changes detected" |
| FR1.4 Expose model in Django admin | Verified | `accounts/admin.py`; admin changelist/detail capture (proof-01) |
| FR2.1 Load signing key from path/env; fail closed | Verified | `zai_auth/signing.py`; `ImproperlyConfigured` log (proof-02); `test_missing_key_fails_closed` |
| FR2.2 Serve `client-metadata.json` per atproto schema | Verified | curl output (proof-02); `test_metadata_endpoint_is_schema_shaped` |
| FR2.3 Serve JWKS, public keys only | Verified | curl + `grep -c '"d"' == 0` (proof-02); `test_jwks_contains_no_private_material` |
| FR3.1 handle→DID→DID-doc→PDS discovery | Verified | live resolution vs `bsky.social` (proof-03); `test_discover_auth_server` |
| FR3.2 PAR + PKCE; redirect to PDS | Verified* | `client.pushed_authorization_request`/`authorization_url`; `test_par_uses_dpop_nonce_retry`, `test_pkce_pair_is_valid_s256`. *Interactive redirect = manual step. |
| FR3.3 DPoP-bound token exchange w/ `private_key_jwt` | Verified | `client.exchange_code`, `dpop.py`; `test_exchange_code_returns_token`, `test_client_assertion_claims` |
| FR3.4 Create on first login; refresh handle/pds_url/last_seen | Verified | `views._upsert_member`; `test_successful_callback_*`, `test_existing_member_handle_is_refreshed` |
| FR3.5 Django session; refresh + DPoP key server-side | Verified | `auth_login`; `AtprotoToken`; `test_successful_callback_creates_member_and_session` |
| FR4.1 OIDC discovery + authorize + token endpoints | Verified | discovery curl (proof-04); authorize/token tests |
| FR4.2 Mint `id_token` RS256 (`sub`=DID, handle, iss/aud/exp) | Verified | minted claims (proof-04); `test_id_token_verifies_against_jwks_*` |
| FR4.3 `id_token` verifiable against JWKS | Verified | live JWKS verification (proof-04); `test_token_exchange_returns_verifiable_id_token` |
| FR4.4 Document Open WebUI OIDC config | Verified | `apps/zai-auth/README.md` "Open WebUI OIDC configuration" |

### Repository Standards

| Standard Area | Status | Evidence & Notes |
| --- | --- | --- |
| Reproducibility / no committed secrets | Verified | env-driven settings; `.env`+`keys/` git-ignored (`git check-ignore`); no `.pem` tracked |
| Testing patterns | Verified | Django `manage.py test`; 38 tests pass; tests alongside code |
| Validate-before-done (CLAUDE.md) | Verified | migrations apply from scratch; `makemigrations --check` clean; `manage.py check` 0 issues |
| Commit conventions | Verified | one commit per parent task, `Related to T# in Spec 01`, Co-Authored-By trailer |
| Comments explain *why* | Verified | docstrings/comments across `signing.py`, `client.py`, `dpop.py`, `models.py` |
| Docs maintenance (docs/ tree) | N/A | app-only; no role/playbook/networking change. App has its own README. |

### Proof Artifacts

| Unit/Task | Proof Artifact | Status | Verification Result |
| --- | --- | --- | --- |
| T1 | `migrate` on fresh DB; model tests; admin; `.env.example` | Verified | migrate OK; 5 tests pass; admin 200 with `did` column |
| T2 | curl `client-metadata.json` + JWKS; fail-closed log; tests | Verified | both 200, schema-valid, no private `d`; `ImproperlyConfigured` raised |
| T3 | live resolution; login form; mocked-flow tests | Verified* | live pipeline reaches `bsky.social` endpoints; 17 tests pass. *Interactive browser screenshot = manual (see Issue M1) |
| T4 | discovery curl; id_token verified vs JWKS; README; tests | Verified | discovery 200; id_token RS256 verifies with `sub`=DID/handle/nonce; 9 tests pass |

## 3) Validation Issues

| Severity | Issue | Impact | Recommendation |
| --- | --- | --- | --- |
| MEDIUM (M1) | Interactive login screenshot not produced. Spec/Tasks Unit 3 list "Screenshot/recording: full login via `runserver` against a real handle". It requires a public-HTTPS `client_id` (atproto rejects a non-fetchable localhost metadata URL) + human consent — not possible headlessly. Evidence: documented in `01-task-03-proofs.md` "Manual step"; substitute evidence = live resolution to `bsky.social` + mocked callback tests. | Verification (one user-facing artifact); functionality logic is otherwise demonstrated. | Before production, run the flow through a tunnel / atproto localhost dev-client and capture the screenshot. Tied to deferred Open Q #3/#5 (public hostname/TLS). |
| LOW (L1) | "Relevant Files" naming drift. Tasks doc lists `atproto/client.py` etc.; actual app is `atproto_oauth/` (renamed during impl to avoid colliding with the `atproto` PyPI package). Evidence: `git diff --name-only` shows `atproto_oauth/*`. | Traceability cosmetic only. | Optional: update the tasks "Relevant Files" paths to `atproto_oauth/`. |
| LOW (L2) | Extra changed files beyond "Relevant Files" (e.g. `oidc/management/commands/generate_keys.py`, `oidc/test_signing.py`, `atproto_oauth/test_metadata.py`, templates, migrations, `apps.py`/`__init__.py`/`wsgi.py`). Evidence: `git diff --name-only`. | None — standard Django scaffolding. | Justified in commit messages (keygen command, templates, migrations). No action required. |

No CRITICAL/HIGH issues. No real credentials found (GATE F clean).

## 4) Evidence Appendix

### Commits analyzed

```
a671eb1 feat: scaffold zai-auth Django app + DID-keyed identity model      (T1)
cb29722 feat: signing keys, JWKS, and ATProto client-metadata endpoint     (T2)
6e130cb feat: ATProto OAuth login flow (handle -> DPoP-bound session)       (T3)
20c95f7 feat: OIDC provider (discovery, authorize, token) ... id_token      (T4)
```

### Commands executed

```text
$ manage.py test
Ran 38 tests ... OK

$ manage.py makemigrations --check --dry-run
No changes detected

$ manage.py check
System check identified no issues (0 silenced).

$ git ls-files | grep '\.pem$'          -> none
$ grep -rl "BEGIN.*PRIVATE KEY" docs/specs/01-spec-atproto-login/  -> none
$ git check-ignore apps/zai-auth/.env apps/zai-auth/keys/atproto_ec_private.pem
apps/zai-auth/.env
apps/zai-auth/keys/atproto_ec_private.pem
```

### File integrity

- 23/23 "Relevant Files" exist (existence check passed).
- Extra files are Django scaffolding/tests/migrations, justified in commits (L2).

### Documented implementation deviations (verified, not defects)

1. **Two signing keys, one JWKS** (ES256 atproto + RS256 OIDC) vs the spec's
   single-keypair line — RSA can't produce ES256. Documented in `signing.py`,
   tasks §2.1, proof-02.
2. **OAuth library (Open Q #1)** resolved to a direct `requests`+`PyJWT`
   implementation. Documented in `requirements.txt`, proof-03.
3. **`tier` omitted** from the Phase-1 `User` (deferred with LiteLLM) — matches
   spec Non-Goal #3.

### Carried-forward Open Questions (deployment spec)

Public hostname/TLS (#3), production key/token storage-at-rest (#4), local-dev
`client_id` (#5), and Open WebUI accepting accounts **without `email`** (#2 — the
`id_token` currently emits `sub`+`handle` only).

---

**Validation Completed:** 2026-06-22
**Validation Performed By:** Claude Opus 4.8 (1M context)
