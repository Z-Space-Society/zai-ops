from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone


class User(AbstractUser):
    """A ZAI cluster user, authenticated via ATProto OAuth.

    `did` is the stable identifier we trust and key on — it never changes, so
    everything downstream (sessions, OIDC `sub`) references it. `username` holds
    the handle for display only; it's mutable and gets refreshed on each login
    (handles can change; DIDs cannot). Password auth is unused — login is via
    ATProto.

    `email` is sourced from the member's PDS on each login (the
    `transition:email` scope + `com.atproto.server.getSession`, see
    `atproto_oauth.client.fetch_session_email`) — it is best-effort and may be
    blank if the member declined the scope or has none on file. It must still
    not be relied on as an identifier: DID is the only stable key.
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

    # Whether the PDS reported this email as confirmed (`emailConfirmed` from
    # getSession). `email` itself is inherited from AbstractUser.
    email_confirmed = models.BooleanField(default=False)

    last_seen = models.DateTimeField(null=True, blank=True)

    def touch_last_seen(self, *, save=True):
        """Stamp the current time as this member's last activity."""
        self.last_seen = timezone.now()
        if save:
            self.save(update_fields=["last_seen"])

    def __str__(self):
        return self.username or self.did
