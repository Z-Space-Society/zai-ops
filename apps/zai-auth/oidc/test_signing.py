import json
import tempfile
from pathlib import Path

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from django.core.exceptions import ImproperlyConfigured
from django.test import TestCase, override_settings

from zai_auth import signing


def _write_key(path: Path, key):
    path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )


class SigningTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._tmp = tempfile.TemporaryDirectory()
        d = Path(cls._tmp.name)
        cls.ec_path = d / "ec.pem"
        cls.rsa_path = d / "rsa.pem"
        _write_key(cls.ec_path, ec.generate_private_key(ec.SECP256R1()))
        _write_key(
            cls.rsa_path, rsa.generate_private_key(public_exponent=65537, key_size=2048)
        )

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()
        super().tearDownClass()

    def setUp(self):
        signing._load_private_key.cache_clear()
        self._override = override_settings(
            ATPROTO_EC_PRIVATE_KEY_PATH=str(self.ec_path),
            OIDC_RSA_PRIVATE_KEY_PATH=str(self.rsa_path),
        )
        self._override.enable()

    def tearDown(self):
        self._override.disable()
        signing._load_private_key.cache_clear()

    def test_jwks_has_two_public_keys(self):
        keys = signing.jwks()["keys"]
        self.assertEqual(len(keys), 2)
        self.assertEqual({k["kty"] for k in keys}, {"EC", "RSA"})

    def test_jwks_contains_no_private_material(self):
        blob = json.dumps(signing.jwks())
        for field in ("d", "p", "q", "dp", "dq", "qi"):
            for key in signing.jwks()["keys"]:
                self.assertNotIn(field, key)
        self.assertNotIn('"d"', blob)

    def test_public_jwks_match_configured_keys(self):
        ec_jwk = signing.atproto_public_jwk()
        rsa_jwk = signing.oidc_public_jwk()
        self.assertEqual(ec_jwk["alg"], "ES256")
        self.assertEqual(ec_jwk["use"], "sig")
        self.assertEqual(ec_jwk["crv"], "P-256")
        self.assertEqual(rsa_jwk["alg"], "RS256")
        # kid is the RFC7638 thumbprint and is stable across calls.
        self.assertEqual(ec_jwk["kid"], signing.atproto_kid())
        self.assertEqual(rsa_jwk["kid"], signing.oidc_kid())

    def test_sign_verifies_against_published_jwks(self):
        token = signing.sign_rs256({"hello": "world"})
        key = jwt.PyJWK.from_dict(signing.oidc_public_jwk()).key
        decoded = jwt.decode(token, key=key, algorithms=["RS256"])
        self.assertEqual(decoded["hello"], "world")

        token_es = signing.sign_es256({"foo": "bar"})
        key_es = jwt.PyJWK.from_dict(signing.atproto_public_jwk()).key
        self.assertEqual(jwt.decode(token_es, key=key_es, algorithms=["ES256"])["foo"], "bar")

    def test_missing_key_fails_closed(self):
        with override_settings(ATPROTO_EC_PRIVATE_KEY_PATH=""):
            signing._load_private_key.cache_clear()
            with self.assertRaises(ImproperlyConfigured):
                signing.atproto_public_jwk()

    def test_jwks_endpoint(self):
        resp = self.client.get("/.well-known/jwks.json")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data["keys"]), 2)
        self.assertNotIn('"d"', resp.content.decode())
