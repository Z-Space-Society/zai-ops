"""OIDC provider HTTP endpoints.

- `jwks` (Task 2): published public keys.
- `openid_configuration` (Task 4): discovery document.
- `authorize` (Task 4): the RP (Open WebUI) sends the member here; if signed in we
  issue an auth code and redirect back, otherwise we bounce through atproto login.
- `token` (Task 4): the RP redeems the code (with its client secret) for an
  `id_token`.
"""

import base64
from urllib.parse import urlencode

from django.http import JsonResponse
from django.shortcuts import redirect
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from zai_auth import signing

from . import provider
from .models import OidcAuthCode

# Key in the Django session for "where to resume after atproto login".
POST_LOGIN_REDIRECT = "post_login_redirect"


def jwks(request):
    """Publish the public halves of the signing keys (ES256 + RS256)."""
    return JsonResponse(signing.jwks())


def openid_configuration(request):
    return JsonResponse(provider.discovery_document())


def _error(error, description, status=400):
    return JsonResponse({"error": error, "error_description": description}, status=status)


@require_http_methods(["GET"])
def authorize(request):
    from django.conf import settings

    client_id = request.GET.get("client_id")
    redirect_uri = request.GET.get("redirect_uri")
    response_type = request.GET.get("response_type")
    scope = request.GET.get("scope", "")
    state = request.GET.get("state", "")
    nonce = request.GET.get("nonce", "")

    # Validate the relying party before doing anything else.
    if client_id != settings.OIDC_CLIENT_ID:
        return _error("unauthorized_client", "unknown client_id")
    if redirect_uri not in settings.OIDC_REDIRECT_URIS:
        return _error("invalid_request", "redirect_uri not registered")
    if response_type != "code":
        return _error("unsupported_response_type", "only 'code' is supported")
    if "openid" not in scope.split():
        return _error("invalid_scope", "missing 'openid' scope")

    # Member must be signed in (atproto). If not, bounce through login and resume.
    if not request.user.is_authenticated:
        request.session[POST_LOGIN_REDIRECT] = request.get_full_path()
        return redirect("atproto_oauth:login")

    code = provider.issue_code(
        request.user,
        client_id=client_id,
        redirect_uri=redirect_uri,
        nonce=nonce,
        scope=scope,
    )
    params = {"code": code}
    if state:
        params["state"] = state
    return redirect(f"{redirect_uri}?{urlencode(params)}")


def _client_credentials(request):
    """Read client_id/secret from POST body or HTTP Basic (the two RP methods)."""
    cid = request.POST.get("client_id")
    secret = request.POST.get("client_secret")
    auth = request.META.get("HTTP_AUTHORIZATION", "")
    if not cid and auth.startswith("Basic "):
        try:
            decoded = base64.b64decode(auth[6:]).decode()
            cid, secret = decoded.split(":", 1)
        except (ValueError, UnicodeDecodeError):
            return None, None
    return cid, secret


@csrf_exempt  # token endpoint is machine-to-machine, authenticated by client secret
@require_http_methods(["POST"])
def token(request):
    from django.conf import settings

    cid, secret = _client_credentials(request)
    if cid != settings.OIDC_CLIENT_ID or secret != settings.OIDC_CLIENT_SECRET:
        return _error("invalid_client", "client authentication failed", status=401)

    if request.POST.get("grant_type") != "authorization_code":
        return _error("unsupported_grant_type", "only authorization_code")

    code_value = request.POST.get("code", "")
    redirect_uri = request.POST.get("redirect_uri", "")

    try:
        code = OidcAuthCode.objects.select_related("user").get(code=code_value)
    except OidcAuthCode.DoesNotExist:
        return _error("invalid_grant", "unknown code")

    if not code.is_valid():
        return _error("invalid_grant", "code expired or already used")
    if code.client_id != cid or code.redirect_uri != redirect_uri:
        return _error("invalid_grant", "code/client/redirect mismatch")

    # Single-use: burn the code before issuing the token.
    code.used = True
    code.save(update_fields=["used"])

    id_token = provider.mint_id_token(
        code.user, client_id=cid, nonce=code.nonce
    )
    return JsonResponse(
        {
            "access_token": id_token,  # we don't issue a separate RP access token
            "token_type": "Bearer",
            "expires_in": provider.ID_TOKEN_TTL_SECONDS,
            "id_token": id_token,
        }
    )
