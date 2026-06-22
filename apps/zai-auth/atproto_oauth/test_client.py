import base64
import hashlib
import tempfile
from pathlib import Path
from unittest.mock import patch

import jwt
import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from django.test import TestCase, override_settings

from zai_auth import signing
from . import client, config, dpop


class FakeResp:
    def __init__(self, *, status=200, json_data=None, text="", headers=None):
        self.status_code = status
        self._json = json_data
        self.text = text
        self.headers = headers or {}
        self.ok = 200 <= status < 300

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json

    def raise_for_status(self):
        if not self.ok:
            raise requests.HTTPError(f"status {self.status_code}")


def _write_keys(d: Path):
    ec_path, rsa_path = d / "ec.pem", d / "rsa.pem"
    ec_path.write_bytes(
        ec.generate_private_key(ec.SECP256R1()).private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    rsa_path.write_bytes(
        rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return ec_path, rsa_path


class ResolutionTests(TestCase):
    def test_did_passes_through(self):
        self.assertEqual(
            client.resolve_handle_to_did("did:plc:abc"), "did:plc:abc"
        )

    @patch("atproto_oauth.client.requests.get")
    def test_handle_resolves_via_wellknown(self, mock_get):
        mock_get.return_value = FakeResp(text="did:plc:xyz\n")
        self.assertEqual(
            client.resolve_handle_to_did("alice.bsky.social"), "did:plc:xyz"
        )

    @patch("atproto_oauth.client.requests.get")
    def test_handle_resolver_fallback(self, mock_get):
        mock_get.side_effect = [
            FakeResp(status=404, text="nope"),  # well-known miss
            FakeResp(json_data={"did": "did:plc:fallback"}),  # public resolver
        ]
        self.assertEqual(
            client.resolve_handle_to_did("alice.example.com"), "did:plc:fallback"
        )

    def test_pds_endpoint_from_doc(self):
        doc = {
            "id": "did:plc:abc",
            "service": [
                {
                    "id": "#atproto_pds",
                    "type": "AtprotoPersonalDataServer",
                    "serviceEndpoint": "https://pds.example.com/",
                }
            ],
        }
        self.assertEqual(
            client.pds_endpoint_from_doc(doc), "https://pds.example.com"
        )

    def test_pds_endpoint_missing_raises(self):
        with self.assertRaises(client.OAuthError):
            client.pds_endpoint_from_doc({"service": []})

    @patch("atproto_oauth.client.requests.get")
    def test_discover_auth_server(self, mock_get):
        mock_get.side_effect = [
            FakeResp(json_data={"authorization_servers": ["https://auth.example/"]}),
            FakeResp(
                json_data={
                    "issuer": "https://auth.example",
                    "pushed_authorization_request_endpoint": "https://auth.example/par",
                    "authorization_endpoint": "https://auth.example/authorize",
                    "token_endpoint": "https://auth.example/token",
                }
            ),
        ]
        meta = client.discover_auth_server("https://pds.example.com")
        self.assertEqual(meta["issuer"], "https://auth.example")
        self.assertIn("token_endpoint", meta)


class PkceDpopTests(TestCase):
    def test_pkce_pair_is_valid_s256(self):
        verifier, challenge = client.pkce_pair()
        expected = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        self.assertEqual(challenge, expected)

    def test_dpop_proof_structure_and_signature(self):
        key = dpop.generate_key()
        proof = dpop.make_proof(
            key, "POST", "https://auth.example/par", nonce="nonce123"
        )
        header = jwt.get_unverified_header(proof)
        self.assertEqual(header["typ"], "dpop+jwt")
        self.assertEqual(header["alg"], "ES256")
        self.assertEqual(header["jwk"]["crv"], "P-256")
        self.assertNotIn("d", header["jwk"])  # public only
        # The proof verifies against its embedded public JWK.
        pub = jwt.PyJWK.from_dict({**header["jwk"], "alg": "ES256"}).key
        payload = jwt.decode(proof, key=pub, algorithms=["ES256"])
        self.assertEqual(payload["htm"], "POST")
        self.assertEqual(payload["htu"], "https://auth.example/par")
        self.assertEqual(payload["nonce"], "nonce123")
        self.assertIn("jti", payload)


@override_settings(PUBLIC_BASE_URL="https://auth.zai.test")
class ClientAssertionAndFlowTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._tmp = tempfile.TemporaryDirectory()
        cls.ec_path, cls.rsa_path = _write_keys(Path(cls._tmp.name))

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()
        super().tearDownClass()

    def setUp(self):
        signing._load_private_key.cache_clear()
        self._ov = override_settings(
            ATPROTO_EC_PRIVATE_KEY_PATH=str(self.ec_path),
            OIDC_RSA_PRIVATE_KEY_PATH=str(self.rsa_path),
        )
        self._ov.enable()

    def tearDown(self):
        self._ov.disable()
        signing._load_private_key.cache_clear()

    def test_client_assertion_claims(self):
        assertion = client.build_client_assertion("https://auth.example")
        header = jwt.get_unverified_header(assertion)
        self.assertEqual(header["alg"], "ES256")
        self.assertIn("kid", header)
        pub = jwt.PyJWK.from_dict(signing.atproto_public_jwk()).key
        payload = jwt.decode(
            assertion, key=pub, algorithms=["ES256"], audience="https://auth.example"
        )
        self.assertEqual(payload["iss"], config.client_id())
        self.assertEqual(payload["sub"], config.client_id())

    @patch("atproto_oauth.client.requests.post")
    def test_par_uses_dpop_nonce_retry(self, mock_post):
        # First POST is rejected asking for a nonce; retry succeeds.
        mock_post.side_effect = [
            FakeResp(
                status=400,
                json_data={"error": "use_dpop_nonce"},
                headers={"DPoP-Nonce": "server-nonce"},
            ),
            FakeResp(
                json_data={"request_uri": "urn:ietf:params:oauth:request_uri:abc"},
                headers={"DPoP-Nonce": "server-nonce"},
            ),
        ]
        meta = {
            "issuer": "https://auth.example",
            "pushed_authorization_request_endpoint": "https://auth.example/par",
        }
        request_uri, nonce = client.pushed_authorization_request(
            meta,
            dpop_key=dpop.generate_key(),
            state="st",
            code_challenge="cc",
            login_hint="alice.bsky.social",
        )
        self.assertTrue(request_uri.startswith("urn:ietf:params:oauth:request_uri"))
        self.assertEqual(nonce, "server-nonce")
        self.assertEqual(mock_post.call_count, 2)

    @patch("atproto_oauth.client.requests.post")
    def test_exchange_code_returns_token(self, mock_post):
        mock_post.return_value = FakeResp(
            json_data={
                "access_token": "AT",
                "refresh_token": "RT",
                "sub": "did:plc:abc",
                "token_type": "DPoP",
            },
            headers={"DPoP-Nonce": "n2"},
        )
        meta = {
            "issuer": "https://auth.example",
            "token_endpoint": "https://auth.example/token",
        }
        token, nonce = client.exchange_code(
            meta, code="code", code_verifier="v", dpop_key=dpop.generate_key()
        )
        self.assertEqual(token["sub"], "did:plc:abc")
        self.assertEqual(token["refresh_token"], "RT")
