from django.conf import settings
from django.db import models


class AtprotoToken(models.Model):
    """Server-side storage of a member's atproto OAuth tokens + DPoP key.

    Held server-side (never sent to the browser) so the session cookie stays the
    only client-side credential. The `dpop_private_pem` is the *per-session*
    ephemeral DPoP key the tokens are bound to — needed to make authenticated PDS
    calls and to refresh.

    NOTE: stored as-is here; encryption-at-rest is a deployment-spec decision
    (spec Open Question #4).
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="atproto_token",
    )
    pds_url = models.URLField()
    issuer = models.URLField()
    token_endpoint = models.URLField()

    access_token = models.TextField()
    refresh_token = models.TextField(blank=True)
    dpop_private_pem = models.TextField()
    dpop_nonce = models.CharField(max_length=512, blank=True)

    expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"AtprotoToken({self.user})"
