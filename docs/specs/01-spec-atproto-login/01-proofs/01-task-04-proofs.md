# Proof Artifacts — Task 4.0: OIDC provider endpoint (id_token for Open WebUI)

Spec: [`01-spec-atproto-login.md`](../01-spec-atproto-login.md) · Task:
[`01-tasks-atproto-login.md`](../01-tasks-atproto-login.md) §4.0

## CLI Output — OIDC discovery document (`runserver`)

`curl localhost:8000/.well-known/openid-configuration`:

```json
{
    "issuer": "http://localhost:8000",
    "authorization_endpoint": "http://localhost:8000/oidc/authorize",
    "token_endpoint": "http://localhost:8000/oidc/token",
    "jwks_uri": "http://localhost:8000/.well-known/jwks.json",
    "response_types_supported": ["code"],
    "grant_types_supported": ["authorization_code"],
    "subject_types_supported": ["public"],
    "id_token_signing_alg_values_supported": ["RS256"],
    "scopes_supported": ["openid", "profile"],
    "token_endpoint_auth_methods_supported": ["client_secret_post", "client_secret_basic"],
    "claims_supported": ["sub", "handle", "preferred_username", "iss", "aud", "exp", "iat", "nonce"]
}
```

## CLI Output — id_token minted + verified against the live JWKS endpoint

A freshly minted `id_token` is verified exactly as a relying party would: fetch
the running JWKS endpoint over HTTP, select the RSA key by `kid`, verify RS256 +
issuer + audience.

```text
id_token header: {'alg': 'RS256', 'kid': 'sBUxAmAEI8oQWoxAcj8pWrLByiaXTi3138wzP_C8M0M', 'typ': 'JWT'}
verified claims: {
  'iss': 'http://localhost:8000',
  'sub': 'did:plc:ewvi7nxzyoun6zhxrhs64oiz',   # DID is the subject
  'aud': 'open-webui',
  'handle': 'atproto.com',
  'preferred_username': 'atproto.com',
  'nonce': 'rp-nonce'                          # RP nonce echoed
}
```

## Test Results — full suite (38 tests)

```text
$ .venv/bin/python manage.py test
Found 38 test(s).
......................................
----------------------------------------------------------------------
Ran 38 tests in 1.180s

OK
```

Task 4 tests (`oidc/test_provider.py`):

- Discovery document shape (issuer, jwks_uri, RS256, claims).
- `id_token` **verifies against JWKS** with expected claims (`sub`=DID, `handle`,
  `nonce`, `aud`); header advertises the correct `kid`.
- **authorize**: unauthenticated → bounced to atproto login; authenticated →
  redirect to the RP with `code` + `state`; unknown client and unregistered
  redirect URI rejected (400).
- **token**: code exchange returns a verifiable `id_token`; bad client secret
  rejected (401); **codes are single-use** (replay rejected).

## Documentation — Open WebUI OIDC config

[`apps/zai-auth/README.md`](../../../apps/zai-auth/README.md) "Open WebUI OIDC
configuration" documents the exact RP env vars (`OPENID_PROVIDER_URL`,
`OAUTH_CLIENT_ID/SECRET`, `OAUTH_SCOPES`, `OAUTH_USERNAME_CLAIM=handle`), the
endpoint table, the claim set, and the **no-`email` open question** (spec #2).

## Verification

| Requirement (spec Unit 4) | Evidence |
| ------------------------- | -------- |
| OIDC endpoints (discovery + authorize + token) an RP can complete a flow against | discovery curl; authorize/token tests |
| Mint `id_token` (RS256) with `sub`=DID + handle + iss/aud/exp | minted-claims output; `test_id_token_verifies_against_jwks_*` |
| `id_token` verifiable against the JWKS endpoint | live JWKS verification above |
| Reuse the member's Django session | `authorize` requires `request.user`; bounces to login otherwise |
| Document Open WebUI OIDC config | README section |

## Security check

No secrets in this file. The `kid` and claims are public; the client secret used
in the live demo (`demo-secret`) is a throwaway dev value, not committed —
production secrets come from the environment (`OIDC_CLIENT_SECRET`).
