from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase

User = get_user_model()

DID = "did:plc:ewvi7nxzyoun6zhxrhs64oiz"


class UserModelTests(TestCase):
    def test_create_user_with_did(self):
        """A member is created keyed by DID, with the handle in username."""
        user = User.objects.create_user(
            username="alice.bsky.social",
            did=DID,
            pds_url="https://pds.example.com",
        )
        self.assertEqual(user.did, DID)
        self.assertEqual(user.username, "alice.bsky.social")
        self.assertEqual(user.pds_url, "https://pds.example.com")
        self.assertIsNone(user.last_seen)

    def test_did_is_unique(self):
        """Two members cannot share a DID — it's the primary identity."""
        User.objects.create_user(username="alice.bsky.social", did=DID)
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                User.objects.create_user(username="alice2.bsky.social", did=DID)

    def test_handle_lives_in_username_and_is_mutable(self):
        """The handle (username) can change while the DID stays fixed."""
        user = User.objects.create_user(username="old.handle", did=DID)
        user.username = "new.handle"
        user.save(update_fields=["username"])
        user.refresh_from_db()
        self.assertEqual(user.username, "new.handle")
        self.assertEqual(user.did, DID)

    def test_touch_last_seen(self):
        """touch_last_seen stamps activity."""
        user = User.objects.create_user(username="alice.bsky.social", did=DID)
        self.assertIsNone(user.last_seen)
        user.touch_last_seen()
        user.refresh_from_db()
        self.assertIsNotNone(user.last_seen)

    def test_str_prefers_handle_then_did(self):
        user = User.objects.create_user(username="alice.bsky.social", did=DID)
        self.assertEqual(str(user), "alice.bsky.social")
