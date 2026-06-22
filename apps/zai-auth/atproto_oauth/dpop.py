"""DPoP (Demonstrating Proof-of-Possession) helpers — RFC 9449.

atproto binds OAuth tokens to a **per-session** EC key. We generate one per
login, persist it server-side (the tokens are bound to it), and use it to sign a
fresh DPoP proof for every request to the PDS / authorization server.
"""

import hashlib
import time
import uuid

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from zai_auth import signing


def generate_key() -> ec.EllipticCurvePrivateKey:
    """A fresh ephemeral DPoP key (EC P-256, as atproto requires)."""
    return ec.generate_private_key(ec.SECP256R1())


def key_to_pem(key: ec.EllipticCurvePrivateKey) -> str:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


def key_from_pem(pem: str) -> ec.EllipticCurvePrivateKey:
    return serialization.load_pem_private_key(pem.encode(), password=None)


def make_proof(
    key: ec.EllipticCurvePrivateKey,
    htm: str,
    htu: str,
    *,
    nonce: str | None = None,
    access_token: str | None = None,
) -> str:
    """Build a signed DPoP proof JWT for an `htm` request to `htu`.

    Includes the server-issued `nonce` when present, and the access-token hash
    (`ath`) when proving possession on a resource request.
    """
    payload = {
        "jti": uuid.uuid4().hex,
        "htm": htm,
        "htu": htu,
        "iat": int(time.time()),
    }
    if nonce:
        payload["nonce"] = nonce
    if access_token:
        payload["ath"] = signing.b64url(
            hashlib.sha256(access_token.encode()).digest()
        )
    return jwt.encode(
        payload,
        key,
        algorithm="ES256",
        headers={"typ": "dpop+jwt", "jwk": signing.ec_public_jwk(key.public_key())},
    )
