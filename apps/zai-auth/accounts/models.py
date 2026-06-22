from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone


class User(AbstractUser):
    """A ZAI cluster user, authenticated via ATProto OAuth.

    `did` is the stable identifier we trust and key on — it never changes, so
    everything downstream (sessions, OIDC `sub`) references it. `username` holds
    the handle for display only; it's mutable and gets refreshed on each login
    (handles can change; DIDs cannot). Password auth is unused — login is via
    ATProto — and `email` stays unused (atproto doesn't expose it), so it must
    not be relied on as an identifier.
    """

    # Stable atproto identifier — the thing everything actually references.
    did = models.CharField(
        max_length=255,
        unique=True,
        editable=False,
        help_text="Permanent atproto DID, e.g. did:plc:ewvi7nxzyoun6zhxrhs64oiz",
    )

    # Current PDS, resolved from the DID document. Needed for token refresh.
    pds_url = models.URLField(blank=True)

    last_seen = models.DateTimeField(null=True, blank=True)

    def touch_last_seen(self, *, save=True):
        """Stamp the current time as this member's last activity."""
        self.last_seen = timezone.now()
        if save:
            self.save(update_fields=["last_seen"])

    def __str__(self):
        return self.username or self.did
