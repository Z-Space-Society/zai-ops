"""Derived OAuth-client URLs and the published client metadata document.

Everything anchors on `settings.PUBLIC_BASE_URL` so local and cluster runs differ
only by that one value. The `client_id` *is* the URL of the client-metadata
document (an atproto requirement), and must be public HTTPS in production.
"""

from django.conf import settings
from django.urls import reverse


def base_url() -> str:
    return settings.PUBLIC_BASE_URL.rstrip("/")


def client_id() -> str:
    # atproto identifies the client by the URL that serves its metadata.
    return base_url() + reverse("atproto_oauth:client_metadata")


def redirect_uri() -> str:
    return base_url() + reverse("atproto_oauth:callback")


def jwks_uri() -> str:
    return base_url() + reverse("oidc:jwks")


def client_metadata() -> dict:
    """ATProto OAuth client metadata (served at the `client_id` URL).

    Confidential web client: `private_key_jwt` auth with an ES256 key and
    DPoP-bound access tokens, per the atproto OAuth profile.
    """
    return {
        "client_id": client_id(),
        "client_name": "ZAI Auth",
        "client_uri": base_url(),
        "application_type": "web",
        "dpop_bound_access_tokens": True,
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "redirect_uris": [redirect_uri()],
        "scope": "atproto transition:generic",
        "token_endpoint_auth_method": "private_key_jwt",
        "token_endpoint_auth_signing_alg": "ES256",
        "jwks_uri": jwks_uri(),
    }
