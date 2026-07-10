from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from . import dpop
from .models import AtprotoToken
from .views import SESSION_PREFIX

User = get_user_model()

DID = "did:plc:ewvi7nxzyoun6zhxrhs64oiz"


def _seed_pending(test_client, state, *, did=DID, handle="alice.bsky.social"):
    """Put a pending-flow record into the session, as `login` would have."""
    session = test_client.session
    session[SESSION_PREFIX + state] = {
        "code_verifier": "verifier",
        "dpop_pem": dpop.key_to_pem(dpop.generate_key()),
        "dpop_nonce": "nonce",
        "issuer": "https://auth.example",
        "token_endpoint": "https://auth.example/token",
        "did": did,
        "pds_url": "https://pds.example.com",
        "handle": handle,
    }
    session.save()


class LoginViewTests(TestCase):
    def test_login_get_renders_form(self):
        resp = self.client.get(reverse("atproto_oauth:login"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "name=\"handle\"")


class CallbackViewTests(TestCase):
    def test_unknown_state_is_rejected(self):
        resp = self.client.get(
            reverse("atproto_oauth:callback"), {"state": "bogus", "code": "x"}
        )
        self.assertEqual(resp.status_code, 400)

    def test_missing_state_is_rejected(self):
        resp = self.client.get(reverse("atproto_oauth:callback"), {"code": "x"})
        self.assertEqual(resp.status_code, 400)

    @patch("atproto_oauth.views.client.fetch_session_email")
    @patch("atproto_oauth.views.client.exchange_code")
    def test_successful_callback_creates_member_and_session(
        self, mock_exchange, mock_email
    ):
        mock_exchange.return_value = (
            {"sub": DID, "access_token": "AT", "refresh_token": "RT"},
            "n2",
        )
        mock_email.return_value = ("", False)
        _seed_pending(self.client, "state1")
        resp = self.client.get(
            reverse("atproto_oauth:callback"), {"state": "state1", "code": "code", "iss": "https://auth.example"}
        )
        self.assertRedirects(
            resp, reverse("atproto_oauth:landing"), fetch_redirect_response=False
        )
        user = User.objects.get(did=DID)
        self.assertEqual(user.username, "alice.bsky.social")
        self.assertEqual(user.pds_url, "https://pds.example.com")
        self.assertIsNotNone(user.last_seen)
        # Authenticated Django session established.
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)
        # Tokens + DPoP key stored server-side.
        token = AtprotoToken.objects.get(user=user)
        self.assertEqual(token.refresh_token, "RT")
        self.assertTrue(token.dpop_private_pem)

    @patch("atproto_oauth.views.client.fetch_session_email")
    @patch("atproto_oauth.views.client.exchange_code")
    def test_callback_persists_email_from_pds(self, mock_exchange, mock_email):
        mock_exchange.return_value = (
            {"sub": DID, "access_token": "AT", "refresh_token": "RT"},
            "n2",
        )
        mock_email.return_value = ("alice@example.com", True)
        _seed_pending(self.client, "state_email")
        self.client.get(
            reverse("atproto_oauth:callback"),
            {"state": "state_email", "code": "code", "iss": "https://auth.example"},
        )
        user = User.objects.get(did=DID)
        self.assertEqual(user.email, "alice@example.com")
        self.assertTrue(user.email_confirmed)

    @patch("atproto_oauth.views.client.fetch_session_email")
    @patch("atproto_oauth.views.client.exchange_code")
    def test_existing_member_handle_is_refreshed(self, mock_exchange, mock_email):
        User.objects.create_user(username="old.handle", did=DID)
        mock_exchange.return_value = (
            {"sub": DID, "access_token": "AT", "refresh_token": "RT"},
            "n2",
        )
        mock_email.return_value = ("", False)
        _seed_pending(self.client, "state2", handle="new.handle")
        self.client.get(
            reverse("atproto_oauth:callback"), {"state": "state2", "code": "code", "iss": "https://auth.example"}
        )
        user = User.objects.get(did=DID)
        self.assertEqual(user.username, "new.handle")  # refreshed
        self.assertIsNotNone(user.last_seen)
        self.assertEqual(User.objects.filter(did=DID).count(), 1)  # no duplicate

    def test_issuer_mismatch_is_rejected(self):
        _seed_pending(self.client, "state_iss")
        resp = self.client.get(
            reverse("atproto_oauth:callback"),
            {"state": "state_iss", "code": "code", "iss": "https://evil.example"},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(User.objects.filter(did=DID).exists())

    @patch("atproto_oauth.views.client.exchange_code")
    def test_missing_sub_is_rejected(self, mock_exchange):
        # Token response without `sub` must not fall back to the resolved DID.
        mock_exchange.return_value = ({"access_token": "AT"}, "n2")
        _seed_pending(self.client, "state_nosub")
        resp = self.client.get(
            reverse("atproto_oauth:callback"),
            {"state": "state_nosub", "code": "code", "iss": "https://auth.example"},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(User.objects.filter(did=DID).exists())

    @patch("atproto_oauth.views.client.exchange_code")
    def test_did_mismatch_is_rejected(self, mock_exchange):
        mock_exchange.return_value = (
            {"sub": "did:plc:somebodyelse", "access_token": "AT"},
            "n2",
        )
        _seed_pending(self.client, "state3", did=DID)
        resp = self.client.get(
            reverse("atproto_oauth:callback"), {"state": "state3", "code": "code", "iss": "https://auth.example"}
        )
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(User.objects.filter(did=DID).exists())
