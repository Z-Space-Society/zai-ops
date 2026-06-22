# Proof Artifacts — Task 2.0: Signing key + published client metadata & JWKS

Spec: [`01-spec-atproto-login.md`](../01-spec-atproto-login.md) · Task:
[`01-tasks-atproto-login.md`](../01-tasks-atproto-login.md) §2.0

> **Design note (documented deviation):** atproto *mandates ES256 (EC P-256)* for
> DPoP + the `private_key_jwt` client assertion, while the OIDC `id_token` uses
> RS256. RSA can't produce ES256, so ZAI Auth holds **two keys published in one
> JWKS**, not the spec's single "keypair". See `zai_auth/signing.py`.

## CLI Output — key generation (mode 0600, git-ignored)

```text
$ manage.py generate_keys
wrote .../apps/zai-auth/keys/atproto_ec_private.pem
wrote .../apps/zai-auth/keys/oidc_rsa_private.pem

$ ls -l keys/
-rw-------  1 jacob  staff   241 atproto_ec_private.pem
-rw-------  1 jacob  staff  1704 oidc_rsa_private.pem

$ git check-ignore apps/zai-auth/keys/atproto_ec_private.pem
apps/zai-auth/keys/atproto_ec_private.pem   -> git-ignored
```

## CLI Output — `curl localhost/client-metadata.json` (schema-valid)

```json
{
    "client_id": "http://localhost:8000/client-metadata.json",
    "client_name": "ZAI Auth",
    "client_uri": "http://localhost:8000",
    "application_type": "web",
    "dpop_bound_access_tokens": true,
    "grant_types": ["authorization_code", "refresh_token"],
    "response_types": ["code"],
    "redirect_uris": ["http://localhost:8000/oauth/callback"],
    "scope": "atproto transition:generic",
    "token_endpoint_auth_method": "private_key_jwt",
    "token_endpoint_auth_signing_alg": "ES256",
    "jwks_uri": "http://localhost:8000/.well-known/jwks.json"
}
```

## CLI Output — `curl localhost/.well-known/jwks.json` (public only)

```json
{
    "keys": [
        {"kty": "EC", "crv": "P-256", "x": "iKb7Yx...", "y": "489rQC...",
         "use": "sig", "alg": "ES256", "kid": "cG4Aick0sSCGUgLky4iDGOT1L-tDipsI8YA2-uwdqIg"},
        {"kty": "RSA", "key_ops": ["verify"], "n": "ummsyP8E...", "e": "AQAB",
         "use": "sig", "alg": "RS256", "kid": "sBUxAmAEI8oQWoxAcj8pWrLByiaXTi3138wzP_C8M0M"}
    ]
}
```

```text
# assert no private material is published:
$ curl -s .../.well-known/jwks.json | grep -c '"d"'
0   (no private 'd'/'p'/'q' fields)
```

## Log — fail-closed key loading

```text
$ ATPROTO_EC_PRIVATE_KEY_PATH="" python -c "...signing.atproto_public_jwk()"
ImproperlyConfigured: ATPROTO_EC_PRIVATE_KEY_PATH is not set — generate keys with
`manage.py generate_keys` and point the env var at the PEM file.
```

## Test Results

`.venv/bin/python manage.py test` (full suite — model + signing + metadata):

```text
Found 12 test(s).
............
----------------------------------------------------------------------
Ran 12 tests in 0.492s

OK
```

Task 2 tests (`oidc/test_signing.py`, `atproto_oauth/test_metadata.py`) cover:
JWKS has exactly the EC+RSA public keys; **no private fields** in the JWKS; public
JWKs match the configured keys with correct `alg`/`use`/`kid` (RFC 7638
thumbprint); a signed JWT verifies against the published JWKS (ES256 and RS256);
missing key raises `ImproperlyConfigured`; the JWKS HTTP endpoint and
`client-metadata.json` return the expected shapes.

## Verification

| Requirement (spec Unit 2) | Evidence |
| ------------------------- | -------- |
| Load private key from configurable path/env; fail closed if absent | `signing.py`; fail-closed log above; `test_missing_key_fails_closed` |
| Serve `client-metadata.json` at the `client_id` URL per atproto schema | curl output; `test_metadata_endpoint_is_schema_shaped` |
| Serve JWKS exposing only the public key(s) | curl output; `grep -c '"d"' == 0`; `test_jwks_contains_no_private_material` |

## Security check

No real keys or secrets in this file: the JWKS values are **public** key material
(truncated), and `x`/`n` are public coordinates by design. Private PEMs live only
in the git-ignored `keys/` directory.
