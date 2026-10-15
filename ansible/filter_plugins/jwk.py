# Render signing keys for the services that take JWKs (docs/decisions/0011).
#
#   pem | private_jwk      -> a private JWK dict with alg, use and kid
#   didkey | didkey_to_pem -> a PDS key-store did:key as a PKCS8 PEM
#
# The kid is the RFC 7638 thumbprint of the public key: what Corliss and the
# PDS already publish, so the same key has the same kid in every JWKS that
# shows it. It is a property of the key, so it cannot drift while the PEM on
# the control node is unchanged, and that is what keeps a confidential
# atproto client's sessions refreshable (the authorization server ties each
# session to the key that authenticated it).
#
# EC only. P-256 renders as ES256, the algorithm atproto requires of clients;
# secp256k1 as ES256K, which only a PDS key from an old build can be. RSA is
# refused: the one RSA key on the cluster is Corliss's OIDC key, which Corliss
# reads as a PEM and publishes itself.

import base64
import hashlib
import json

from ansible.errors import AnsibleFilterError
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

_CURVES = {
    "secp256r1": ("P-256", "ES256"),
    "secp256k1": ("secp256k1", "ES256K"),
}

# Multicodec prefixes of a private did:key, as atproto-identity writes them.
_DIDKEY_PREFIXES = {
    bytes([0x86, 0x26]): ec.SECP256R1,
    bytes([0x81, 0x26]): ec.SECP256K1,
}

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _b64url(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _int_bytes(n):
    return n.to_bytes(32, "big")


def private_jwk(pem):
    try:
        key = serialization.load_pem_private_key(str(pem).encode(), password=None)
    except (ValueError, TypeError) as exc:
        raise AnsibleFilterError(f"private_jwk: not a PEM private key: {exc}")
    if not isinstance(key, ec.EllipticCurvePrivateKey):
        raise AnsibleFilterError("private_jwk: only EC keys render as JWKs here")
    if key.curve.name not in _CURVES:
        raise AnsibleFilterError(f"private_jwk: unsupported curve {key.curve.name}")
    crv, alg = _CURVES[key.curve.name]
    pub = key.public_key().public_numbers()
    public = {
        "crv": crv,
        "kty": "EC",
        "x": _b64url(_int_bytes(pub.x)),
        "y": _b64url(_int_bytes(pub.y)),
    }
    canonical = json.dumps(public, separators=(",", ":"), sort_keys=True)
    kid = _b64url(hashlib.sha256(canonical.encode()).digest())
    return {
        **public,
        "d": _b64url(_int_bytes(key.private_numbers().private_value)),
        "alg": alg,
        "use": "sig",
        "kid": kid,
    }


def _b58decode(text):
    n = 0
    for ch in text:
        i = _B58.find(ch)
        if i < 0:
            raise AnsibleFilterError("didkey_to_pem: not base58btc")
        n = n * 58 + i
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    leading = len(text) - len(text.lstrip("1"))
    return b"\x00" * leading + raw


def didkey_to_pem(didkey):
    text = str(didkey).strip()
    if text.startswith("did:key:"):
        text = text[len("did:key:"):]
    if not text.startswith("z"):
        raise AnsibleFilterError("didkey_to_pem: expected a multibase base58btc key")
    raw = _b58decode(text[1:])
    curve = _DIDKEY_PREFIXES.get(raw[:2])
    if curve is None or len(raw) != 34:
        raise AnsibleFilterError(
            "didkey_to_pem: not a private P-256 or secp256k1 did:key"
        )
    key = ec.derive_private_key(int.from_bytes(raw[2:], "big"), curve())
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


class FilterModule:
    def filters(self):
        return {"private_jwk": private_jwk, "didkey_to_pem": didkey_to_pem}
