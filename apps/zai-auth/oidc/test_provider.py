import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from zai_auth import signing
from . import provider
from .models import OidcAuthCode

User = get_user_model()

DID = "did:plc:ewvi7nxzyoun6zhxrhs64oiz"
CLIENT_ID = "open-webui"
CLIENT_SECRET = "test-secret"
REDIRECT_URI = "https://chat.example.test/oauth/oidc/callback"


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


@override_settings(
    PUBLIC_BASE_URL="https://auth.zai.test",
    OIDC_CLIENT_ID=CLIENT_ID,
    OIDC_CLIENT_SECRET=CLIENT_SECRET,
    OIDC_REDIRECT_URIS=[REDIRECT_URI],
)
class OidcProviderTests(TestCase):
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
        self.user = User.objects.create_user(
            username="alice.bsky.social", did=DID, pds_url="https://pds.example.com"
        )

    def tearDown(self):
        self._ov.disable()
        signing._load_private_key.cache_clear()

    # --- discovery --------------------------------------------------------

    def test_discovery_document(self):
        resp = self.client.get(reverse("oidc:openid_configuration"))
        self.assertEqual(resp.status_code, 200)
        doc = resp.json()
        self.assertEqual(doc["issuer"], "https://auth.zai.test")
        self.assertEqual(
            doc["jwks_uri"], "https://auth.zai.test/.well-known/jwks.json"
        )
        self.assertEqual(doc["id_token_signing_alg_values_supported"], ["RS256"])
        self.assertIn("sub", doc["claims_supported"])
        self.assertIn("handle", doc["claims_supported"])
        self.assertIn("email", doc["claims_supported"])
        self.assertIn("email", doc["scopes_supported"])

    # --- id_token mint + verify against JWKS ------------------------------

    def test_id_token_verifies_against_jwks_with_expected_claims(self):
        token = provider.mint_id_token(self.user, client_id=CLIENT_ID, nonce="n0")
        # Verify exactly as a relying party would: fetch JWKS, pick the key.
        jwks = signing.jwks()
        rsa_jwk = next(k for k in jwks["keys"] if k["kty"] == "RSA")
        key = jwt.PyJWK.from_dict(rsa_jwk).key
        claims = jwt.decode(
            token, key=key, algorithms=["RS256"], audience=CLIENT_ID,
            issuer="https://auth.zai.test",
        )
        self.assertEqual(claims["sub"], DID)
        self.assertEqual(claims["handle"], "alice.bsky.social")
        self.assertEqual(claims["nonce"], "n0")
        self.assertEqual(claims["aud"], CLIENT_ID)
        self.assertIn("exp", claims)

    def test_id_token_omits_email_when_none_on_file(self):
        token = provider.mint_id_token(self.user, client_id=CLIENT_ID)
        jwks = signing.jwks()
        rsa_jwk = next(k for k in jwks["keys"] if k["kty"] == "RSA")
        claims = jwt.decode(
            token,
            key=jwt.PyJWK.from_dict(rsa_jwk).key,
            algorithms=["RS256"],
            audience=CLIENT_ID,
        )
        self.assertNotIn("email", claims)
        self.assertNotIn("email_verified", claims)

    def test_id_token_carries_email_when_pds_supplied_one(self):
        self.user.email = "alice@example.com"
        self.user.email_confirmed = True
        self.user.save(update_fields=["email", "email_confirmed"])
        token = provider.mint_id_token(self.user, client_id=CLIENT_ID)
        jwks = signing.jwks()
        rsa_jwk = next(k for k in jwks["keys"] if k["kty"] == "RSA")
        claims = jwt.decode(
            token,
            key=jwt.PyJWK.from_dict(rsa_jwk).key,
            algorithms=["RS256"],
            audience=CLIENT_ID,
        )
        self.assertEqual(claims["email"], "alice@example.com")
        self.assertTrue(claims["email_verified"])

    def test_id_token_header_advertises_kid(self):
        token = provider.mint_id_token(self.user, client_id=CLIENT_ID)
        header = jwt.get_unverified_header(token)
        self.assertEqual(header["alg"], "RS256")
        self.assertEqual(header["kid"], signing.oidc_kid())

    # --- authorize endpoint ----------------------------------------------

    def _authorize_params(self, **overrides):
        params = {
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "scope": "openid profile",
            "state": "rp-state",
            "nonce": "rp-nonce",
        }
        params.update(overrides)
        return params

    def test_authorize_requires_login_then_issues_code(self):
        # Unauthenticated → bounced to atproto login.
        resp = self.client.get(reverse("oidc:authorize"), self._authorize_params())
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse("atproto_oauth:login"), resp.url)

        # Authenticated → redirected back to the RP with a code + state.
        self.client.force_login(self.user)
        resp = self.client.get(reverse("oidc:authorize"), self._authorize_params())
        self.assertEqual(resp.status_code, 302)
        parsed = urlparse(resp.url)
        self.assertTrue(resp.url.startswith(REDIRECT_URI))
        qs = parse_qs(parsed.query)
        self.assertEqual(qs["state"], ["rp-state"])
        self.assertTrue(OidcAuthCode.objects.filter(code=qs["code"][0]).exists())

    def test_authorize_rejects_unknown_client(self):
        self.client.force_login(self.user)
        resp = self.client.get(
            reverse("oidc:authorize"), self._authorize_params(client_id="evil")
        )
        self.assertEqual(resp.status_code, 400)

    def test_authorize_rejects_unregistered_redirect(self):
        self.client.force_login(self.user)
        resp = self.client.get(
            reverse("oidc:authorize"),
            self._authorize_params(redirect_uri="https://evil.test/cb"),
        )
        self.assertEqual(resp.status_code, 400)

    # --- token endpoint ---------------------------------------------------

    def _get_code(self):
        return provider.issue_code(
            self.user,
            client_id=CLIENT_ID,
            redirect_uri=REDIRECT_URI,
            nonce="rp-nonce",
            scope="openid",
        )

    def test_token_exchange_returns_verifiable_id_token(self):
        code = self._get_code()
        resp = self.client.post(
            reverse("oidc:token"),
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT_URI,
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
            },
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("id_token", body)
        self.assertEqual(body["token_type"], "Bearer")
        rsa_jwk = next(k for k in signing.jwks()["keys"] if k["kty"] == "RSA")
        claims = jwt.decode(
            body["id_token"],
            key=jwt.PyJWK.from_dict(rsa_jwk).key,
            algorithms=["RS256"],
            audience=CLIENT_ID,
        )
        self.assertEqual(claims["sub"], DID)
        self.assertEqual(claims["nonce"], "rp-nonce")

    def test_token_rejects_bad_client_secret(self):
        code = self._get_code()
        resp = self.client.post(
            reverse("oidc:token"),
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT_URI,
                "client_id": CLIENT_ID,
                "client_secret": "wrong",
            },
        )
        self.assertEqual(resp.status_code, 401)

    def test_token_code_is_single_use(self):
        code = self._get_code()
        post = lambda: self.client.post(
            reverse("oidc:token"),
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT_URI,
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
            },
        )
        self.assertEqual(post().status_code, 200)
        # Second redemption of the same code is rejected.
        self.assertEqual(post().status_code, 400)
