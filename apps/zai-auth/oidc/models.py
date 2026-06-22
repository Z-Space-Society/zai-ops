from django.conf import settings
from django.db import models
from django.utils import timezone


class OidcAuthCode(models.Model):
    """A short-lived OIDC authorization code issued to the relying party.

    Bound to the member, the requesting client, the redirect URI, and the
    `nonce` so the token endpoint can validate the exchange and echo the nonce
    into the `id_token`. Single-use: marked `used` once redeemed.
    """

    code = models.CharField(max_length=128, unique=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    client_id = models.CharField(max_length=255)
    redirect_uri = models.URLField()
    nonce = models.CharField(max_length=255, blank=True)
    scope = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used = models.BooleanField(default=False)

    def is_valid(self) -> bool:
        return not self.used and timezone.now() < self.expires_at

    def __str__(self):
        return f"OidcAuthCode({self.user}, used={self.used})"
