# 0001 Questions Round 1 - ZAI Baseline Platform

Please answer each question below (select one or more options, or add your own
notes). Feel free to add additional context under any question.

Context: this spec corrects and formalizes the "ZAI Baseline Platform" draft
(written in Claude Desktop) against decisions already made and shipped —
[ADR-0005](../../decisions/0005-zai-auth-over-aip.md) (zai-auth over AIP,
deployed), OIDC into Open WebUI live as the only login, the F1 scoped-key fix,
`zai-litellm-key`, and HappyView on the cluster.

## 1. Spec shape — umbrella milestone vs. implementable feature

The Baseline is a milestone spanning several builds (notes app, identity
passthrough, key self-service, white-label pass). That's far bigger than one
SDD-sized feature spec. How should `0001` be structured?

- [ ] (A) **Umbrella milestone spec.** `0001` captures the Baseline definition,
      current state (built/decided/remaining), platform constraints, and the
      sequence. Each remaining build item becomes its own numbered child spec
      (`0004+`) when its turn comes; `0001`'s "demoable units" are the milestone
      checkpoints, proven by the child specs' validations.
- [ ] (B) Force everything into one giant implementable spec with tasks for the
      notes app, passthrough, self-service, and branding all inside it.
- [ ] (C) Skip the platform spec; go straight to child specs and keep the
      milestone definition only in the project vault.
- [ ] (D) Other (describe)

**Recommended answer(s):** [(A)]

**Why these are recommended:**

- `(A)` keeps the milestone document in-repo as the single source of truth
  (matching the docs-first convention here) while keeping each child spec
  SDD-sized — the workflow's task/audit/validation machinery only works on
  right-sized specs.
- `(B)` would produce an unmanageable task list spanning four unrelated builds;
  `(C)` loses the corrected milestone definition from the repo, which is the
  whole point of this exercise.

## 2. API key self-service placement (draft's open decision #5)

The draft proposed "a page in the notes app or a small standalone app." Since
then, ADR-0005 explicitly framed zai-auth as "the first feature of a broader
control app," and zai-auth now has a member-facing Account page at
`account.{{ cluster_domain }}` with the design-handoff template, the member's
DID in session, and an admin model. Where should members create/revoke keys and
view usage?

- [ ] (A) **A page in zai-auth's account app.** The account page grows a "API
      keys" section backed by LiteLLM's admin API (same API `zai-litellm-key`
      already uses).
- [ ] (B) A page in the notes app (the draft's lean) — couples key self-service
      to the notes app's timeline.
- [ ] (C) A small standalone app — a third deployment for one page.
- [ ] (D) Leave it open for the design spike.

**Recommended answer(s):** [(A)]

**Why these are recommended:**

- `(A)` is the option most consistent with decisions already made: ADR-0005's
  "broader control app" framing, an already-deployed member-facing surface with
  the member's DID in session (exactly the identity a key must be bound to),
  and the design template already applied.
- `(A)` also decouples key self-service from the notes app entirely — it can
  ship before a line of the notes app exists, unlike `(B)`.
- `(C)` adds a CT/deploy/backup surface for one page; `(D)` defers a decision
  the ADR trail has effectively already made.

## 3. Gateway tier model — how much ships in Baseline?

The draft claimed tiers as "built," but the repo has no tier/team automation —
what exists is the gateway, `zai-litellm-key` (operator-run, on CT 100), and
the F1 scoped key for Open WebUI. The tier design (a tier = a LiteLLM team,
per-member RPM on the membership) lives in the project vault only. What does
Baseline require?

- [ ] (A) **Single default member tier.** Self-service mints every member key
      into one "member" LiteLLM team with a default budget/RPM; multi-tier
      automation and per-member overrides are post-Baseline.
- [ ] (B) Full tier model in Baseline: multiple teams, per-member RPM,
      membership-driven tier assignment, all automated.
- [ ] (C) Keys stay operator-CLI-only for Baseline; self-service moves
      post-Baseline (note: this contradicts Baseline definition #5).
- [ ] (D) Other (describe)

**Recommended answer(s):** [(A)]

**Why these are recommended:**

- `(A)` keeps the member-visible promise (create/revoke/usage without touching
  LiteLLM) while cutting the part nothing yet needs — nobody is differentiated
  by tier today, and LiteLLM teams make adding tiers later an additive change,
  not a rework.
- `(B)` builds membership-driven automation before the membership store
  decision (question 4) is even settled — wrong order.
- `(C)` quietly deletes one of the five Baseline definition points; if that's
  intended, the definition itself should change.

## 4. Membership store and gating (draft's open decision #4, Gate B)

**Current state, verified in the repo:** there is no gating anywhere — zai-auth
upserts a `User` for *any* ATProto handle that completes OAuth, and Open WebUI
auto-creates an account for any successful OIDC login (`ENABLE_OAUTH_SIGNUP=true`,
default role `user`). Today, any Bluesky user who finds the URL is a "member."
What is the authoritative membership source for Baseline, and does Baseline
include gating?

- [ ] (A) **zai-auth's DID-keyed User table is the roster, plus an approval
      flag.** Baseline adds a `is_member`-style flag (set by an admin action
      like `zai-make-admin`'s pattern) that gates OIDC token issuance; a
      Bluesky List could later *feed* it, but the private Postgres roster is
      authoritative (keeps membership non-public, satisfying the Gate B
      privacy concern).
- [ ] (B) A public Bluesky List is the source of truth; zai-auth checks it at
      login (membership becomes publicly enumerable — the Gate B concern).
- [ ] (C) A separate control-plane roster service (new component, new spec).
- [ ] (D) No gating in Baseline — anyone with an ATProto handle can join; the
      membership decision stays open.

**Recommended answer(s):** [(A)]

**Why these are recommended:**

- `(A)` resolves the draft's open decision with infrastructure that already
  exists: the DID-keyed table *is* a roster, it's private by default, every
  service already reads identity from the same OIDC issuer, and "services read
  membership but never own it" holds — zai-auth is the identity layer, not an
  app.
- `(D)` is the status quo, but it means the notes ACL model ("access granted to
  members by DID") sits on an unbounded population, and gateway keys become
  mintable by anyone — Baseline's access story stops meaning anything.
- `(B)` trades away privacy for convenience before anyone asked for a public
  member list; `(C)` invents a fourth service where a flag suffices.

## 5. White-label depth for Open WebUI in Baseline

OIDC wiring is done; what remains of "white-labeled chat client" is the visual
pass. The design system now exists in-repo (the design handoff landed as
zai-auth's login/account base template). How deep does the Baseline pass go?

- [ ] (A) **Config-and-CSS pass.** `WEBUI_NAME`/logo/favicon, hide OWUI's own
      branding and irrelevant features via its env/config surface, plus custom
      CSS aligned with the zai-auth design handoff. No fork.
- [ ] (B) Deep theme or maintained fork of the OWUI frontend — full design
      control, at the cost of tracking upstream releases with a patched tree.
- [ ] (C) Defer all branding post-Baseline; ship OWUI stock.
- [ ] (D) Other (describe)

**Recommended answer(s):** [(A)]

**Why these are recommended:**

- `(A)` matches the draft's own MVP stance ("ride Open WebUI until it
  demonstrably can't do what we need") — a fork (`(B)`) is exactly the
  commitment the draft deferred to the eventual custom thin client, and it
  would fight every `pip` upgrade.
- `(C)` gives up the "first visible win" the draft's sequence counts on, and
  the unified-design goal is a stated part of the Baseline definition.

## 6. Single logout — in or out of Baseline?

Known gap, documented in the zai-auth role notes: logging out of Open WebUI
doesn't end the zai-auth session, so re-authenticating silently re-issues a
token with no prompt (`/logout` on the account page ends only zai-auth's own
session; there's no `end_session_endpoint` / RP-initiated logout). The draft's
"one login for everything" implies but never states "one logout."

- [ ] (A) **Out of Baseline.** Document it as a known limitation in the spec's
      non-goals; RP-initiated logout is follow-up work.
- [ ] (B) In Baseline: implement `end_session_endpoint` + wire Open WebUI's
      logout to it, as part of the white-label/login polish.
- [ ] (C) Other (describe)

**Recommended answer(s):** [(A)]

**Why these are recommended:**

- `(A)` keeps Baseline scope on the five definition points; on shared-computer
  risk, members can sign out from the account page today, and the gap is
  honestly documented rather than silently ignored.
- `(B)` is reasonable polish, but it's the only item in this list that adds
  *new* protocol surface to zai-auth for a scenario (shared browsers) nobody
  has raised — better as a fast-follow than a Baseline gate.
