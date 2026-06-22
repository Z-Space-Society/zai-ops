"""ATProto OAuth client HTTP endpoints.

- `client_metadata` (Task 2): serves the client metadata at the `client_id` URL.
- `login` (Task 3): handle form → resolve → discover → PAR → redirect to the PDS.
- `callback` (Task 3): validate `state` → DPoP-bound token exchange → upsert the
  member, store tokens server-side, establish the Django session.
- `landing`: the authenticated page a member lands on.
"""

import secrets

from django.contrib.auth import get_user_model
from django.contrib.auth import login as auth_login
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseBadRequest
from django.shortcuts import redirect, render

from . import client, config, dpop
from .models import AtprotoToken

User = get_user_model()

SESSION_PREFIX = "atproto_oauth:"


def client_metadata(request):
    """Serve the ATProto OAuth client metadata at the `client_id` URL."""
    from django.http import JsonResponse

    return JsonResponse(config.client_metadata())


def login(request):
    if request.method != "POST":
        return render(request, "atproto_oauth/login.html")

    handle = request.POST.get("handle", "").strip()
    if not handle:
        return render(
            request, "atproto_oauth/login.html", {"error": "Enter a handle."}
        )

    try:
        did = client.resolve_handle_to_did(handle)
        doc = client.fetch_did_document(did)
        pds_url = client.pds_endpoint_from_doc(doc)
        meta = client.discover_auth_server(pds_url)
        dpop_key = dpop.generate_key()
        verifier, challenge = client.pkce_pair()
        state = secrets.token_urlsafe(32)
        request_uri, nonce = client.pushed_authorization_request(
            meta,
            dpop_key=dpop_key,
            state=state,
            code_challenge=challenge,
            login_hint=handle,
        )
    except client.OAuthError as exc:
        return render(
            request, "atproto_oauth/login.html", {"error": str(exc)}
        )

    # Pending-flow state lives in the server-side Django session, keyed by the
    # opaque `state` we just minted (validated on callback — CSRF defense).
    request.session[SESSION_PREFIX + state] = {
        "code_verifier": verifier,
        "dpop_pem": dpop.key_to_pem(dpop_key),
        "dpop_nonce": nonce,
        "issuer": meta["issuer"],
        "token_endpoint": meta["token_endpoint"],
        "did": did,
        "pds_url": pds_url,
        "handle": handle,
    }
    return redirect(client.authorization_url(meta, request_uri))


def callback(request):
    state = request.GET.get("state")
    pending = (
        request.session.pop(SESSION_PREFIX + state, None) if state else None
    )
    # Unknown/expired/missing state → reject (CSRF / replay protection).
    if not state or pending is None:
        return HttpResponseBadRequest("Invalid or expired authorization state.")

    if request.GET.get("error"):
        return render(
            request,
            "atproto_oauth/login.html",
            {"error": f"Authorization denied: {request.GET.get('error')}"},
        )

    code = request.GET.get("code")
    if not code:
        return HttpResponseBadRequest("Missing authorization code.")

    dpop_key = dpop.key_from_pem(pending["dpop_pem"])
    meta = {
        "issuer": pending["issuer"],
        "token_endpoint": pending["token_endpoint"],
    }
    try:
        token, nonce = client.exchange_code(
            meta,
            code=code,
            code_verifier=pending["code_verifier"],
            dpop_key=dpop_key,
            nonce=pending["dpop_nonce"],
        )
    except client.OAuthError as exc:
        return render(request, "atproto_oauth/login.html", {"error": str(exc)})

    # The token's `sub` is the authenticated DID; it must match who we resolved.
    did = token.get("sub") or pending["did"]
    if pending["did"] and did != pending["did"]:
        return HttpResponseBadRequest("DID mismatch in token response.")

    user = _upsert_member(
        did=did, handle=pending["handle"], pds_url=pending["pds_url"]
    )
    _store_tokens(user, token, dpop_key, nonce, pending)

    auth_login(
        request, user, backend="django.contrib.auth.backends.ModelBackend"
    )
    user.touch_last_seen()

    # Resume an in-progress OIDC authorize (Open WebUI) if one bounced us here.
    # Only honour a safe same-site path (single leading slash, no scheme/host).
    next_url = request.session.pop("post_login_redirect", None)
    if next_url and next_url.startswith("/") and not next_url.startswith("//"):
        return redirect(next_url)
    return redirect("atproto_oauth:landing")


@login_required
def landing(request):
    return render(request, "atproto_oauth/landing.html")


def _upsert_member(*, did, handle, pds_url):
    """Create the member on first login; refresh handle/pds_url thereafter."""
    user, created = User.objects.get_or_create(
        did=did, defaults={"username": handle, "pds_url": pds_url}
    )
    if not created:
        user.username = handle
        user.pds_url = pds_url
        user.save(update_fields=["username", "pds_url"])
    return user


def _store_tokens(user, token, dpop_key, nonce, pending):
    AtprotoToken.objects.update_or_create(
        user=user,
        defaults={
            "pds_url": pending["pds_url"],
            "issuer": pending["issuer"],
            "token_endpoint": pending["token_endpoint"],
            "access_token": token.get("access_token", ""),
            "refresh_token": token.get("refresh_token", ""),
            "dpop_private_pem": dpop.key_to_pem(dpop_key),
            "dpop_nonce": nonce or "",
        },
    )
