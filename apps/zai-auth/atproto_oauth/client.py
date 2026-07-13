"""ATProto OAuth client: identity resolution → PDS discovery → PAR → token exchange.

Implemented directly on `requests` + `PyJWT` (see requirements.txt for why). Each
network step is a small, separately testable function so the flow can be unit
tested with mocked HTTP. The DPoP dance (a 401 with `use_dpop_nonce` + a
`DPoP-Nonce` header, retried once) is handled in `_post_with_dpop`.
"""

import hashlib
import secrets
import time
import uuid

import dns.resolver
import requests

from . import config, dpop
from zai_auth import signing

CLIENT_ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
TIMEOUT = 10


class OAuthError(Exception):
    """Any failure resolving identity or talking to the PDS/auth server."""


# --- Identity resolution --------------------------------------------------

def _resolve_via_well_known(handle: str) -> str | None:
    """The HTTPS well-known method: GET https://{handle}/.well-known/atproto-did."""
    try:
        r = requests.get(
            f"https://{handle}/.well-known/atproto-did", timeout=TIMEOUT
        )
        if r.ok and r.text.strip().startswith("did:"):
            return r.text.strip()
    except requests.RequestException:
        pass
    return None


def _resolve_via_dns_txt(handle: str) -> str | None:
    """The DNS TXT method: a `_atproto.{handle}` TXT record of `did=did:...`."""
    try:
        answers = dns.resolver.resolve(f"_atproto.{handle}", "TXT", lifetime=TIMEOUT)
    except dns.exception.DNSException:
        return None
    for rdata in answers:
        txt = b"".join(rdata.strings).decode("utf-8", "replace")
        if txt.startswith("did="):
            return txt[len("did="):]
    return None


def resolve_handle_to_did(handle: str) -> str:
    """Resolve a handle to a DID (or pass a DID straight through).

    Tries the HTTPS well-known method first, then the public resolver XRPC.
    """
    handle = handle.strip().lstrip("@")
    if handle.startswith("did:"):
        return handle
    did = _resolve_via_well_known(handle)
    if did:
        return did
    try:
        r = requests.get(
            "https://public.api.bsky.app/xrpc/com.atproto.identity.resolveHandle",
            params={"handle": handle},
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        return r.json()["did"]
    except (requests.RequestException, KeyError) as exc:
        raise OAuthError(f"could not resolve handle {handle!r}") from exc


def resolve_handle_for_admin(handle: str) -> str:
    """Resolve a handle to a DID for admin-granting: only the two atproto-spec
    methods (DNS TXT, then HTTPS well-known) — deliberately no fallback to the
    third-party public resolver, since granting admin shouldn't trust it."""
    handle = handle.strip().lstrip("@")
    did = _resolve_via_dns_txt(handle) or _resolve_via_well_known(handle)
    if not did:
        raise OAuthError(
            f"could not resolve handle {handle!r} via DNS TXT or well-known"
        )
    return did


def fetch_did_document(did: str) -> dict:
    if did.startswith("did:plc:"):
        url = f"https://plc.directory/{did}"
    elif did.startswith("did:web:"):
        domain = did[len("did:web:"):]
        url = f"https://{domain}/.well-known/did.json"
    else:
        raise OAuthError(f"unsupported DID method: {did!r}")
    try:
        r = requests.get(url, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as exc:
        raise OAuthError(f"could not fetch DID document for {did}") from exc


def pds_endpoint_from_doc(doc: dict) -> str:
    for svc in doc.get("service", []):
        if (
            svc.get("id") in ("#atproto_pds", f"{doc.get('id', '')}#atproto_pds")
            or svc.get("type") == "AtprotoPersonalDataServer"
        ):
            endpoint = svc.get("serviceEndpoint")
            if not endpoint:
                raise OAuthError("PDS service entry has no serviceEndpoint")
            return endpoint.rstrip("/")
    raise OAuthError("no atproto PDS endpoint in DID document")


def handle_from_doc(doc: dict) -> str | None:
    for aka in doc.get("alsoKnownAs", []):
        if aka.startswith("at://"):
            return aka[len("at://"):]
    return None


# --- Authorization-server discovery ---------------------------------------

def discover_auth_server(pds_url: str) -> dict:
    """Resolve the PDS to its authorization-server metadata document."""
    try:
        pr = requests.get(
            f"{pds_url}/.well-known/oauth-protected-resource", timeout=TIMEOUT
        )
        pr.raise_for_status()
        issuer = pr.json()["authorization_servers"][0].rstrip("/")
    except (requests.RequestException, KeyError, IndexError) as exc:
        raise OAuthError(f"PDS {pds_url} exposed no authorization server") from exc
    try:
        meta = requests.get(
            f"{issuer}/.well-known/oauth-authorization-server", timeout=TIMEOUT
        )
        meta.raise_for_status()
        return meta.json()
    except requests.RequestException as exc:
        raise OAuthError(f"could not fetch auth-server metadata at {issuer}") from exc


# --- PKCE + client assertion ----------------------------------------------

def pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) for the S256 method."""
    verifier = secrets.token_urlsafe(64)
    challenge = signing.b64url(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge


def build_client_assertion(issuer: str) -> str:
    """A `private_key_jwt` proving the client to the auth server (ES256)."""
    now = int(time.time())
    payload = {
        "iss": config.client_id(),
        "sub": config.client_id(),
        "aud": issuer,
        "jti": uuid.uuid4().hex,
        "iat": now,
        "exp": now + 300,
    }
    return signing.sign_es256(payload)


# --- DPoP-bound POST with one-shot nonce retry ----------------------------

def _post_with_dpop(url, data, dpop_key, nonce=None):
    def _send(use_nonce):
        proof = dpop.make_proof(dpop_key, "POST", url, nonce=use_nonce)
        return requests.post(
            url, data=data, headers={"DPoP": proof}, timeout=TIMEOUT
        )

    resp = _send(nonce)
    if resp.status_code in (400, 401):
        server_nonce = resp.headers.get("DPoP-Nonce")
        err = None
        try:
            err = resp.json().get("error")
        except ValueError:
            pass
        if server_nonce and err == "use_dpop_nonce":
            resp = _send(server_nonce)
            return resp, resp.headers.get("DPoP-Nonce", server_nonce)
    return resp, resp.headers.get("DPoP-Nonce", nonce)


def _get_with_dpop(url, access_token, dpop_key, nonce=None):
    """GET a DPoP-bound resource (e.g. the PDS's `getSession`) with one-shot
    nonce retry, mirroring `_post_with_dpop`."""

    def _send(use_nonce):
        proof = dpop.make_proof(
            dpop_key, "GET", url, nonce=use_nonce, access_token=access_token
        )
        return requests.get(
            url,
            headers={"DPoP": proof, "Authorization": f"DPoP {access_token}"},
            timeout=TIMEOUT,
        )

    resp = _send(nonce)
    if resp.status_code in (400, 401):
        server_nonce = resp.headers.get("DPoP-Nonce")
        err = None
        try:
            err = resp.json().get("error")
        except ValueError:
            pass
        if server_nonce and err == "use_dpop_nonce":
            resp = _send(server_nonce)
            return resp, resp.headers.get("DPoP-Nonce", server_nonce)
    return resp, resp.headers.get("DPoP-Nonce", nonce)


# --- Email sourcing (transition:email) -------------------------------------

def fetch_session_email(pds_url, access_token, *, dpop_key, nonce=None):
    """Read `email`/`emailConfirmed` from the member's PDS via `getSession`.

    Email is optional data — the member may have declined the scope, or the
    PDS may not expose it. This never raises; a failure or missing field
    simply yields `("", False)` so login isn't blocked on it (mirrors AIP's
    `fetch_email_from_pds`, adapted to zai-auth's own DPoP client).
    """
    url = f"{pds_url}/xrpc/com.atproto.server.getSession"
    try:
        resp, _ = _get_with_dpop(url, access_token, dpop_key, nonce=nonce)
        if not resp.ok:
            return "", False
        data = resp.json()
    except (requests.RequestException, ValueError):
        return "", False
    return data.get("email") or "", bool(data.get("emailConfirmed"))


# --- PAR + token exchange -------------------------------------------------

def pushed_authorization_request(
    meta, *, dpop_key, state, code_challenge, login_hint
):
    """Push the authorization request; return (request_uri, dpop_nonce)."""
    data = {
        "client_id": config.client_id(),
        "response_type": "code",
        "redirect_uri": config.redirect_uri(),
        "scope": config.SCOPE,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "login_hint": login_hint,
        "client_assertion_type": CLIENT_ASSERTION_TYPE,
        "client_assertion": build_client_assertion(meta["issuer"]),
    }
    resp, nonce = _post_with_dpop(
        meta["pushed_authorization_request_endpoint"], data, dpop_key
    )
    if not resp.ok:
        raise OAuthError(f"PAR failed ({resp.status_code}): {resp.text[:200]}")
    try:
        request_uri = resp.json()["request_uri"]
    except (ValueError, KeyError) as exc:
        raise OAuthError("PAR response missing request_uri") from exc
    return request_uri, nonce


def authorization_url(meta, request_uri: str) -> str:
    from urllib.parse import urlencode

    qs = urlencode({"client_id": config.client_id(), "request_uri": request_uri})
    return f"{meta['authorization_endpoint']}?{qs}"


def exchange_code(meta, *, code, code_verifier, dpop_key, nonce=None):
    """Exchange the auth code for DPoP-bound tokens; return (token, nonce)."""
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": config.redirect_uri(),
        "code_verifier": code_verifier,
        "client_id": config.client_id(),
        "client_assertion_type": CLIENT_ASSERTION_TYPE,
        "client_assertion": build_client_assertion(meta["issuer"]),
    }
    resp, new_nonce = _post_with_dpop(
        meta["token_endpoint"], data, dpop_key, nonce=nonce
    )
    if not resp.ok:
        raise OAuthError(
            f"token exchange failed ({resp.status_code}): {resp.text[:200]}"
        )
    return resp.json(), new_nonce
