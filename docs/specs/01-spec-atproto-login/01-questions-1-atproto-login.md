# 01 Questions Round 1 - ZAI Auth: ATProto Login (Django)

Please answer each question below (check one or more options, or add your own
notes). Feel free to add additional context under any question. When you've saved
your answers, tell me and I'll read the file and generate the spec.

Context: zai-ops is an Ansible/Proxmox IaC repo whose prime directive is full
reproducibility. A Django app is a new kind of artifact here, and several things
your input depends on (Open WebUI / CT 104, Postgres / CT 102, public HTTPS/TLS)
are not yet built — they exist only as inventory placeholders. These questions
mostly pin down the boundaries so the spec stays a clean, demoable auth-only
slice.

---

## 1. Where does the Django application source live?

The reproducibility directive means *how* the code is deployed matters as much as
the code itself.

- [ ] (A) **New separate git repo** (e.g. `Z-Space-Society/zai-auth`); a zai-ops
      Ansible role clones + deploys it. Keeps zai-ops purely IaC.
- [X] (B) **Subdirectory of zai-ops** (e.g. `apps/zai-auth/`); an Ansible role
      deploys from the local checkout. One repo to rebuild from.
- [ ] (C) **Separate repo, app-only spec** — this spec covers the Django
      application; the deploy role/CT is a later, separate spec.
- [ ] (D) Other (describe)

## 2. What does THIS spec deliver — app, deployment, or both?

- [x] (A) **App only** — the Django app + tests, runnable locally (`runserver`);
      CT/role/nginx assumed or stubbed, deployment is a later spec.
- [ ] (B) **App + deployment** — also a new CT (e.g. 105 `zai-auth`), an Ansible
      role, and an nginx vhost, demonstrated end-to-end on the cluster.
- [ ] (C) Other (describe)

## 3. How do we satisfy the "lands in Open WebUI authenticated" success criterion?

Open WebUI (CT 104) isn't deployed yet. For this spec's end-to-end proof:

- [ ] (A) **Stand up Open WebUI** as part of this spec (expands scope notably).
- [x ] (B) **Assume Open WebUI is a prerequisite** (its own spec); prove against a
      running instance, document the OIDC config it needs.
- [] (C) **Prove OIDC against a minimal test relying-party** (a throwaway OIDC
      client) so the slice is self-contained; defer real Open WebUI wiring.
- [ ] (D) Other (describe)

## 4. Where is the Member identity stored for Phase 1?

Postgres (CT 102) isn't deployed yet; the `Member` model (did pk, handle,
pds_url, tier, created_at, last_seen) needs a datastore.

- [ ] (A) **SQLite for Phase 1**, migrate to Postgres in a later spec.
- [ ] (B) **Require/stand up Postgres** (CT 102) now.
- [x] (C) Other (describe)
Lets assume its there and I'll get it there before moving forward on this. 

## 5. Public HTTPS endpoint for `client-metadata.json` + JWKS

These (and the OIDC issuer) must be reachable over public HTTPS. nginx today
listens on `:80` only and is internal-LAN-facing.

- [ ] (A) **New public DNS name fronted by nginx (CT 101)** with Let's Encrypt
      TLS — this spec adds the TLS/vhost.
- [ ] (B) **Behind an existing gateway / Cloudflare tunnel** that already
      terminates TLS — this spec just declares the routes.
- [x] (C) **Hostname/TLS undecided** — capture as an Open Question, assume a
      placeholder hostname for the spec.
- [ ] (D) Other (specify the public hostname, e.g. `auth.zspace.example`)

## 6. Signing & DPoP key storage at rest

The keypair backs DPoP proofs, the client assertion, and id_token signing.

- [ ] (A) **Generated at deploy, stored on-box** (root-only file) on the auth CT;
      never in git. JWKS published from it.
- [ ] (B) **Generated into Ansible Vault**, injected at deploy (fits the existing
      vault trust model).
- [ ] (C) **Generated at deploy into the DB**, encrypted at rest.
- [X ] (D) Other (describe)
I don't understand this question. 

## 7. ATProto OAuth library decision (you flagged this as open)

No turnkey Django package is confirmed; `requests_oauth2client` was reported to
cover DPoP, and Bluesky ships a Flask "hard way" demo.

- [ ] (A) **First demoable unit is a short library-selection spike** — evaluate
      candidates for PAR + DPoP + PKCE, then build on the chosen one.
- [ ] (B) **Pin a library now** in the spec as a technical constraint (specify
      which).
- [X] (C) **Leave as an Open Question**, decide during implementation.
- [ ] (D) Other (describe)

## 8. Confirm the deferred items stay out of scope for Phase 1

Your input defers: LiteLLM tier/key issuance; Bluesky List-driven membership; SSO
to services beyond Open WebUI; knowledge base / notes; community-PDS
(non-technical) onboarding.

- [X] (A) **Yes — all deferred**, listed as Non-Goals.
- [ ] (B) **Pull one or more into Phase 1** (specify which).
- [ ] (C) Other (describe)

## 9. OIDC id_token claims (confirm the minimal set)

- [ ] (A) **DID (`sub`) + handle** only, plus standard issuer/audience/exp — the
      minimal set to create/identify an Open WebUI account.
- [ ] (B) Add **email-shaped claim** synthesized from the handle (some OIDC
      clients require `email`) — note if Open WebUI needs this.
- [ ] (C) Other (describe which claims)
I don't understand.  