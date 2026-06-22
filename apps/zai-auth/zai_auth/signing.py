"""Signing-key management for ZAI Auth.

One JWKS, **two keys**, because the protocols demand different algorithms:

- **ES256 (EC P-256)** — atproto OAuth *mandates* this for DPoP proofs and the
  `private_key_jwt` client assertion.
- **RS256 (RSA)** — the OIDC `id_token` (spec choice; broad OIDC-client compat).

(The spec's "one keypair" line is a simplification: RSA can't produce ES256, so
both keys exist and are published together. Documented deviation.)

Private keys are loaded from configurable PEM paths (never committed). The public
halves are published at the JWKS endpoint. Loading **fails closed**: a missing or
unconfigured key raises `ImproperlyConfigured` rather than silently degrading.
"""

import base64
import hashlib
import json
from functools import lru_cache
from pathlib import Path

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from jwt.algorithms import ECAlgorithm, RSAAlgorithm


def b64url(raw: bytes) -> str:
    """URL-safe base64 without padding (the JOSE encoding)."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def ec_public_jwk(public_key) -> dict:
    """Minimal public EC JWK (`kty`/`crv`/`x`/`y`) — shared by JWKS + DPoP."""
    full = json.loads(ECAlgorithm.to_jwk(public_key))
    return {"kty": "EC", "crv": full["crv"], "x": full["x"], "y": full["y"]}


@lru_cache(maxsize=8)
def _load_private_key(path: str, setting_name: str):
    """Load a PEM private key from `path`, failing closed if absent.

    Cached by path so repeated signing doesn't re-read the file; call
    `_load_private_key.cache_clear()` in tests that swap key files.
    """
    if not path:
        raise ImproperlyConfigured(
            f"{setting_name} is not set — generate keys with "
            "`manage.py generate_keys` and point the env var at the PEM file."
        )
    p = Path(path)
    if not p.is_absolute():
        p = Path(settings.BASE_DIR) / p
    if not p.exists():
        raise ImproperlyConfigured(f"{setting_name}: key file not found at {p}")
    return serialization.load_pem_private_key(p.read_bytes(), password=None)


def atproto_private_key() -> ec.EllipticCurvePrivateKey:
    return _load_private_key(
        settings.ATPROTO_EC_PRIVATE_KEY_PATH, "ATPROTO_EC_PRIVATE_KEY_PATH"
    )


def oidc_private_key() -> rsa.RSAPrivateKey:
    return _load_private_key(
        settings.OIDC_RSA_PRIVATE_KEY_PATH, "OIDC_RSA_PRIVATE_KEY_PATH"
    )


def _jwk_thumbprint(jwk: dict) -> str:
    """RFC 7638 JWK thumbprint — a stable `kid` derived from the public key."""
    if jwk["kty"] == "EC":
        members = {"crv": jwk["crv"], "kty": "EC", "x": jwk["x"], "y": jwk["y"]}
    elif jwk["kty"] == "RSA":
        members = {"e": jwk["e"], "kty": "RSA", "n": jwk["n"]}
    else:  # pragma: no cover - we only mint EC/RSA
        raise ValueError(f"unsupported kty: {jwk['kty']}")
    canonical = json.dumps(members, separators=(",", ":"), sort_keys=True)
    return b64url(hashlib.sha256(canonical.encode()).digest())


def _public_jwk(private_key, alg: str) -> dict:
    """Public-only JWK (no private `d`) with `use`/`alg`/`kid` filled in."""
    public_key = private_key.public_key()
    to_jwk = ECAlgorithm.to_jwk if alg == "ES256" else RSAAlgorithm.to_jwk
    jwk = json.loads(to_jwk(public_key))
    # Defensive: never publish private material even if a backend leaked it.
    for private_field in ("d", "p", "q", "dp", "dq", "qi"):
        jwk.pop(private_field, None)
    jwk["use"] = "sig"
    jwk["alg"] = alg
    jwk["kid"] = _jwk_thumbprint(jwk)
    return jwk


def atproto_public_jwk() -> dict:
    return _public_jwk(atproto_private_key(), "ES256")


def oidc_public_jwk() -> dict:
    return _public_jwk(oidc_private_key(), "RS256")


def atproto_kid() -> str:
    return atproto_public_jwk()["kid"]


def oidc_kid() -> str:
    return oidc_public_jwk()["kid"]


def jwks() -> dict:
    """The published JWK Set: atproto (ES256) + OIDC (RS256) public keys."""
    return {"keys": [atproto_public_jwk(), oidc_public_jwk()]}


def sign_es256(payload: dict, *, headers: dict | None = None) -> str:
    """Sign a JWT with the atproto EC key (DPoP proofs, client assertion)."""
    h = {"kid": atproto_kid()}
    if headers:
        h.update(headers)
    return jwt.encode(payload, atproto_private_key(), algorithm="ES256", headers=h)


def sign_rs256(payload: dict, *, headers: dict | None = None) -> str:
    """Sign a JWT with the OIDC RSA key (the `id_token`)."""
    h = {"kid": oidc_kid()}
    if headers:
        h.update(headers)
    return jwt.encode(payload, oidc_private_key(), algorithm="RS256", headers=h)
