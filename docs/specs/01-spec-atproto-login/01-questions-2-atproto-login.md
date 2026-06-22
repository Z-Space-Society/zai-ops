# 01 Questions Round 2 - ZAI Auth: ATProto Login (Django)

Round 1 locked: app lives in `apps/zai-auth/` (subdir of zai-ops); this spec is
**app-only** (runnable locally, deployment is a later spec); Open WebUI and
Postgres are **assumed prerequisites**; public hostname/TLS is an **Open
Question** with a placeholder; library choice stays an **Open Question**; all the
deferred items remain **Non-Goals**.

Three things left to pin. I've recommended a default on each — check it (or pick
another / add notes).

---

## 1. Signing-key storage (the old Q6, re-explained)

The app has one keypair. The **private** half signs DPoP proofs, the client
assertion, and the id_token. The **public** half is published at JWKS so others
can verify. Where does the private key live?

- [x] (A) **(Recommended)** App **loads the private key from a configurable
      path/env var** — generated out-of-band, never committed; JWKS serves the
      public half. *Where the file physically lives in production* (on-box file
      vs. Ansible Vault) is deferred to the deployment spec.
- [ ] (B) App **generates the keypair on first run** and stores it (e.g. in the
      DB) if absent; simpler local dev, but key rotation/portability is fuzzier.
- [ ] (C) Other (describe)

## 2. id_token claims (the old Q9, re-explained)

Which identity fields does the minted id_token carry so Open WebUI can
create/recognize the account?

- [] (A) **(Recommended)** `sub` = DID, plus `handle`, `name`, and a
      **synthesized `email`** (e.g. `handle@<placeholder-domain>`), since OIDC
      clients commonly require `email` to provision an account. Exact required
      set is **verified against Open WebUI** during implementation (Open
      Question).
- [x] (B) **Minimal** — `sub` = DID + `handle` only, no email (only if we can
      confirm Open WebUI accepts accounts without one).
- [ ] (C) Other (describe which claims)

## 3. How does `Member` relate to Django's built-in user?

Django ships its own `User`/auth + sessions. Your `Member` (keyed by DID) is the
cluster identity. Two ways to model it:

- [ ] (A) **(Recommended)** `Member` **is a custom Django user model** keyed by
      DID — one object is both the auth user and the identity record; sessions
      attach to it directly. Cleanest for "Django-native session" + DID-as-pk.
- [ ] (B) Keep Django's default `User` and add a **separate `Member` profile**
      linked one-to-one. More moving parts; only worth it if you expect non-DID
      users.
- [X] (C) Other (describe)
Custom Django user model keyed by DID but naemd User.  So A with a different name.  

**Confirm the field list either way** — `did` (pk), `handle`, `pds_url`, `tier`
(default `Play10`), `created_at`, `last_seen`. Add/remove any:

from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """
    A ZAI cluster user, authenticated via ATProto OAuth.

    `did` is the stable identifier we trust and key on — it never changes.
    `username` holds the handle for display only; it's mutable and gets
    refreshed on each login.
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

    # No password auth — login is via ATProto. email stays unused
    # (atproto doesn't expose it), so don't rely on it as an identifier.

    def __str__(self):
        return self.username or self.did
        
> (notes here)
