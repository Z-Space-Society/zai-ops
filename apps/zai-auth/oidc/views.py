"""OIDC provider HTTP endpoints.

Task 2 lands the JWKS endpoint (shared with the atproto client). The discovery,
authorize, and token endpoints arrive in Task 4.
"""

from django.http import JsonResponse

from zai_auth import signing


def jwks(request):
    """Publish the public halves of the signing keys (ES256 + RS256)."""
    return JsonResponse(signing.jwks())
