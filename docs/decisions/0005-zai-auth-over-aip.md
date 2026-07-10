# ADR-0005: zai-auth, not AIP, is the cluster's login bridge

Status: Accepted
Date: 2026-07-09

## Context

Open WebUI (and every future cluster app) needs members to sign in with their
ATProto handle rather than a separate password. atproto OAuth and OIDC are
explicitly different protocols — OIDC needs a pre-established client↔provider
relationship the decentralized PDS model doesn't have — so a translation
**bridge** is structurally required regardless of which software runs it.

Two candidates were researched in depth:

- **zai-auth** (`apps/zai-auth/`) — a from-scratch Django app in this repo:
  hand-rolled ATProto OAuth client (PAR/PKCE/DPoP/`private_key_jwt`) plus an
  OIDC provider minting `id_token`s. Code-complete against its spec
  ([`docs/specs/01-spec-atproto-login/`](../specs/01-spec-atproto-login/)) —
  DID-keyed `User` model, full login flow, 38 tests passing as of the spec's
  validation.
- **AIP** (`github.com/graze-social/aip`) — Graze Social's maintained Rust
  ATProto→OIDC bridge. Verified from source to be a complete OIDC provider
  (discovery document, `id_token`, userinfo) including PDS email-sourcing via
  `transition:email` — the exact gap zai-auth had open. MIT-licensed, built by
  Nick Gerakines (Graze CTO), production-proven against Discourse/WordPress/
  Matrix, and already the auth layer CO/CORE runs.

Both were technically viable. AIP briefly became the leading candidate once
its OIDC surface was confirmed from source — it meant zero bespoke ATProto
OAuth code, the riskiest part of the whole bridge, delegated to a maintained
implementation.

## Decision

Ship **zai-auth** as the production login bridge. AIP is not deployed
anywhere in this repo or cluster — it was researched, never integrated, and
this ADR records why, closing the item the second Fable security review (F7)
flagged as outstanding.

Reasons, in order of weight:

1. **One less runtime, one less external release cadence in the critical
   path.** Every other native service in this repo (litellm, Open WebUI,
   HappyView, Postgres) is Python or Rust already-adopted for a specific
   reason; AIP would have been the *second* Rust service purely for this one
   role, adding a language/toolchain to the operational surface for no gain
   proportional to the cost. zai-auth is Python — the same runtime as the
   rest of the identity-adjacent stack (Open WebUI) and the eventual control
   app (`apps/zai-auth/` is the first feature of a broader control app this
   repo is expected to grow — see the project vault's ADR-002).
2. **Everything rebuilds from this repo, with nothing to fetch and pin from
   elsewhere.** The prime directive (`CLAUDE.md`) is "flash Proxmox, run one
   script, and the stack rebuilds itself from this repo." zai-auth already
   lives in-tree; deploying it needed a role, not a new external dependency
   to track, pin, and verify a release cadence for (AIP's last tagged release
   was ~6 months stale at research time — not disqualifying on its own, but a
   real ongoing cost zai-auth doesn't carry).
3. **The one real gap — no `email` claim — was closable by porting AIP's
   approach, not by adopting AIP wholesale.** AIP's email-sourcing
   (`transition:email` scope + `com.atproto.server.getSession` directly
   against the member's PDS) is a technique, not code that needed AIP's
   runtime to use. zai-auth's `atproto_oauth` client already had the DPoP/PKCE
   machinery this needed — implementing it was a small, testable addition
   (`atproto_oauth.client.fetch_session_email`), not new infrastructure.
4. **Full control over the surface members log in through.** zai-auth is a
   few hundred lines this project owns outright — every claim, every scope,
   every edge case is legible and changeable without waiting on an upstream
   maintainer's roadmap.

## Alternatives considered

- **AIP as full IdP** (Topology A in the research). Rejected per the reasons
  above — the maintained-software win didn't outweigh adding a second
  language/runtime and an external release cadence to the critical login
  path, given zai-auth was already most of the way there.
- **AIP underneath a thin zai-auth OIDC shim** (Topology B). Rejected for the
  same reasons as A, with less upside — still adds the Rust service, just
  with a smaller surface on top of it.
- **Shared/hosted AIP instance** (not self-hosted). Rejected outright,
  independent of the above — makes cluster login depend on another party's
  uptime and trust, breaking the "keeps working on your own hardware" promise
  the rest of this repo is built around.

## Consequences

Positive:
- No new runtime/language on the cluster; the operational surface stays
  Python + Rust (litellm/Open WebUI/HappyView's existing split), not
  Python + Rust + a second Rust service.
- The login bridge rebuilds from this repo exactly like everything else —
  `ansible-playbook provision.yml --limit zai-auth` after a `git pull`, no
  external package/release to fetch and trust.
- Every claim/scope/edge case in the login path is in-repo, tested, and
  directly modifiable.

Negative / tradeoffs:
- This project carries the maintenance burden AIP would have absorbed: any
  future atproto OAuth spec changes (new scopes, DPoP revisions) are ours to
  track and implement, not a `pip`/binary bump.
- zai-auth has seen far less production traffic than AIP (which Discourse,
  WordPress and Matrix integrations have exercised) — the confidence AIP's
  maturity would have bought is instead earned incrementally, starting with
  the Open WebUI login pilot this deployment enables.
- If ATProto OAuth interop with CO/CORE (shared client registrations, common
  token semantics) becomes a real goal later, running different bridge
  software is a real, if soft, interop cost — revisit if that need surfaces.

## References

- [`docs/specs/01-spec-atproto-login/`](../specs/01-spec-atproto-login/) — the
  zai-auth spec and its proof artifacts.
- [`docs/roles/zai-auth.md`](../roles/zai-auth.md) — the deployment role this
  decision unblocks.
- Fable Security Review 2, finding F7 (project vault,
  `03_Projects/Z-Space AI/ZAI-Ops/Fable Security Review 2`) — flagged this ADR
  as outstanding.
- Project vault: `Research/AIP as Auth Layer`, `Research/Identity & Auth
  Stack`, `Research/AIP Deployment Reference` — the full comparative research
  this decision is drawn from.
