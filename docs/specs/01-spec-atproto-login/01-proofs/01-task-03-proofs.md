# Proof Artifacts — Task 3.0: ATProto OAuth login flow (handle → session)

Spec: [`01-spec-atproto-login.md`](../01-spec-atproto-login.md) · Task:
[`01-tasks-atproto-login.md`](../01-tasks-atproto-login.md) §3.0

> **Library decision (spec Open Question #1):** implemented directly on
> `requests` + `PyJWT` rather than a turnkey OAuth library — atproto's DID/PDS
> resolution and ES256 specifics make a transparent, unit-testable flow simpler.
>
> **Correctness note:** atproto uses a **per-session ephemeral DPoP key** (the
> tokens are bound to it), distinct from the client-assertion key. That key +
> refresh token are stored server-side (`AtprotoToken`), satisfying "store refresh
> tokens + DPoP key material server-side".

## Live proof — resolution + PDS discovery against production atproto

Real network calls (no `client_id` needed for these steps), via
`atproto_oauth.client`:

```text
handle=atproto.com
  did=did:plc:ewvi7nxzyoun6zhxrhs64oiz
  pds=https://enoki.us-east.host.bsky.network
  issuer=https://bsky.social
  par_endpoint=https://bsky.social/oauth/par
  authorization_endpoint=https://bsky.social/oauth/authorize
  token_endpoint=https://bsky.social/oauth/token
```

This exercises `resolve_handle_to_did` → `fetch_did_document` →
`pds_endpoint_from_doc` → `discover_auth_server` against live infrastructure.

## CLI Output — login form + auth gating (`runserver`)

```text
$ curl -s localhost:8000/login            # 200, renders the handle form
... <form method="post" action="/login"> ... name="handle" ... </form>

$ curl -s -o /dev/null -w "%{http_code} %{redirect_url}" localhost:8000/
302 http://localhost:8000/login?next=/    # @login_required gates the landing page
```

## Manual step — interactive browser login (cannot be captured headlessly)

The full screenshot proof (handle → PDS consent screen → authenticated landing)
requires a **public HTTPS `client_id`** (atproto won't accept a non-fetchable
localhost metadata URL from `bsky.social`) and an interactive browser consent.
To reproduce locally, expose the app via a tunnel (or atproto's localhost
dev-client convention), set `PUBLIC_BASE_URL`, then visit `/login`. The
non-interactive steps above + the mocked-flow tests below cover the logic
end-to-end.

## Test Results — full suite (29 tests)

```text
$ .venv/bin/python manage.py test
Found 29 test(s).
.............................
----------------------------------------------------------------------
Ran 29 tests in 0.673s

OK
```

Task 3 tests:

- `atproto_oauth/test_client.py` — DID pass-through; handle resolution via
  well-known **and** resolver fallback; PDS endpoint parsing; auth-server
  discovery; **PKCE** `S256` correctness; **DPoP** proof structure + signature
  (`typ=dpop+jwt`, embedded public JWK, `htm`/`htu`/`nonce`/`jti`); **DPoP nonce
  retry** on `use_dpop_nonce`; `private_key_jwt` **client assertion** claims;
  token exchange.
- `atproto_oauth/test_views.py` — login form renders; **`state` mismatch and
  missing `state` rejected (400)** — CSRF/replay guard; successful callback
  **creates the member, stores tokens + DPoP key, establishes the Django
  session**, redirects to landing; **existing member's handle refreshed** with no
  duplicate; **DID-mismatch rejected**.

## Verification

| Requirement (spec Unit 3) | Evidence |
| ------------------------- | -------- |
| Handle → DID → DID doc → PDS discovery (`oauth-authorization-server`) | live proof above; `test_discover_auth_server` |
| PAR + PKCE; redirect to PDS | `pushed_authorization_request`/`authorization_url`; `test_par_uses_dpop_nonce_retry`, `test_pkce_pair_is_valid_s256` |
| DPoP-bound token exchange w/ `private_key_jwt` | `exchange_code`; `test_exchange_code_returns_token`, `test_client_assertion_claims` |
| Create on first login; update handle/pds_url/last_seen on every login | `_upsert_member`; `test_successful_callback_*`, `test_existing_member_handle_is_refreshed` |
| Django session; refresh + DPoP key stored server-side | `auth_login`; `AtprotoToken`; `test_successful_callback_creates_member_and_session` |
| `state` guard (CSRF) | `test_unknown_state_is_rejected`, `test_missing_state_is_rejected` |

## Security check

No real tokens or keys in this file. The live DID/PDS values are public directory
data. Mocked tests use dummy `AT`/`RT` strings; the ephemeral DPoP key in tests
is generated in-memory.
