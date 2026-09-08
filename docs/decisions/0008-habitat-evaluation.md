# ADR-0008 — Habitat runs here as a time-boxed evaluation, wired to nothing

> Status: **Accepted** (2026-09-08). Adds a role; supersedes nothing.

## Context

Habitat is an open-source data-ownership layer for organizations, built on AT
Protocol. Its vendor is actively asking this project to build on it, and the
overlap is real: their permission model has a delegable `manager` role, which is
precisely the capability HappyView withholds from everyone but a space
authority. That gap is why the question keeps coming back.

It has come back three times and been declined three times. The most recent
assessment concluded that the remaining objections were **project management,
not capability**: storage, blobs, packaging and feeds all resolved, leaving
"Habitat fits, and adopting it means replacing a working production system with
a younger one." That is a judgement call, and judgement calls made from reading
documentation are the ones that keep getting remade.

A specification of what this cluster would need was sent to the vendor, and
answered the same day with "there should be a path forward for all the
requests." Useful, but two items were not answered at all:

1. **Durable scoped service credentials.** An application credential Corliss
   and the sync relay could hold, plus a read path that does not refuse a
   caller who is not itself a member. This is the item that blocks the relay.
2. **Revocation timing.** Whether credentials are revoked *before* a member
   removal completes.

Meanwhile the capability that motivated the whole exercise has moved backwards
in their documentation: roles and groups are now described as work in progress,
"developing in conversation with other teams," modelled on OpenFGA tuples. So
the `manager` tier may or may not exist on any given version.

Reading more will not settle any of this. Every remaining question is about
runtime behaviour, and the vendor's proposed next step was a working session to
get self-hosting running.

Two forces shape how, rather than whether.

- **The prime directive forbids Docker on LXC**, and a container is the only
  packaging Habitat supports. But this is precedented rather than novel:
  [`happyview`](../roles/happyview.md) already builds Rust plus a Next.js
  frontend from source on its CT for the same reason, and Habitat's own
  Dockerfile is a readable, reproducible recipe.
- **Two live membership authorities is a known failure mode** for this project,
  and the reason the previous assessment framed adoption as "replacement or
  nothing." Standing up a second one beside HappyView would reintroduce exactly
  the problem the guardrail exists to prevent.

## Decision

**Deploy Habitat as a `habitat` role and CT, as a time-boxed evaluation
instance that is consumed by nothing.**

- **Membership is unchanged.** Cluster membership stays in the registry space on
  HappyView; Workspace membership stays in Corliss. This ADR touches neither
  decision, and the instance holds no cluster members.
- **Isolation is the load-bearing part, not a detail.** No `corliss_*` variable
  points at it, it gets no row on `/systems/`, and the relay does not read it.
  That isolation is what makes "run it alongside HappyView" something other than
  a second source of truth: an authority nothing asks is not an authority.
  Wiring anything to it supersedes ADR-0003/ADR-0006 and is a new decision, not
  a configuration change.
- **Built from source under systemd**, reproducing the two stages of upstream's
  own `build/debian/pear/Dockerfile`. No Docker, no exception to the prime
  directive.
- **It gets a public Caddy route**, unlike the sync relay. Org DIDs are minted
  against `HABITAT_DOMAIN` and PDS OAuth plus external DID resolution must reach
  it from the internet, so an internal-only instance could not answer the
  questions it exists to answer. The name is therefore effectively immutable.
- **Pinned to a pre-release tag**, because there is no other kind. Habitat
  publishes no releases and tags only `v0.0.2-testing-N`.

### What the instance is for

Concrete questions, to be answered against a running server and carried into the
working session:

1. Do durable scoped app credentials exist, and can a non-member service caller
   read membership? (Unanswered; blocks the relay.)
2. Are credentials revoked before a member removal completes? (Unanswered.)
3. Is the delegable `manager` tier real on a version we can run? It is the one
   capability HappyView lacks and the reason this keeps being reconsidered.
4. Does an external, spaces-compatible PDS work? "The self-hosted instance can
   broker identity itself" is not the ask. This cluster runs its own PDS and
   wants member accounts on it.
5. Does a bare-binary build stay viable, or is the container the only path they
   will keep working?
6. What is the intended pinning story? An evaluation against a moving `latest`
   is not an evaluation.

## Consequences

Positive:

- The recurring Habitat question gets decided on runtime behaviour instead of on
  a fourth reading of the same documentation.
- The two unanswered specification items become testable rather than pending.
- The role is generic (ADR-0001), so it stands up on any cluster, including a
  staging box, with no committed host facts.
- Two upstream gaps were found while writing it and are now documented rather
  than waiting to be hit at runtime. See "Findings" below.

Negative:

- A service pinned to a `v0.0.2-testing-N` tag now exists in the blueprint. It
  is isolated and disposable, but it is real surface, and a stale evaluation CT
  is worse than none.
- The public hostname is effectively permanent once an org is created, so a
  declined evaluation still leaves a name spent.
- The build reproduces a Dockerfile rather than consuming a supported artifact,
  so it is exposed to upstream refactors that a container user would not notice.

### Findings that changed the implementation

Both were discovered from source while writing the role, and both are gaps in
upstream's self-hosting path rather than in this one:

- **`s3://` blob storage does not work on this build.** The flag advertises it,
  but `cmd/pear/main.go` blank-imports no gocloud blob driver and
  `aws-sdk-go-v2/service/s3` is absent from `cmd/pear/go.mod`, so `s3blob` is
  not linked. Blobs go to local disk; the Garage wiring is written and guarded
  behind a flag, and the role asserts at build time which drivers are actually
  linked rather than letting this surface as a runtime error.
- **`HABITAT_SPACE_SIGNING_KEY` is required and nothing upstream generates it.**
  `cmd/keygen` produces the wrong shape, `cmd/didgen` produces the wrong curve,
  and their own container entrypoint never sets it, so the published container
  cannot start either without it supplied from outside. The role mints it with
  indigo's `atcrypto` at the version `cmd/pear` pins.

Details of both are in [`docs/roles/habitat.md`](../roles/habitat.md).

## Revisit when

Any one of:

- The questions above are answered. Then this either becomes a migration,
  superseding ADR-0003 and ADR-0006 with a real plan, or the role and CT are
  removed. **Those are the only two acceptable end states.** An evaluation
  instance left running and unwired past that point is drift, not a decision.
- `opensocial.community` reaches a stable version, or a second implementation of
  those lexicons appears. That is what would make their vocabulary portable
  across hosts rather than vendor-specific.
- HappyView is being replaced as space host for unrelated reasons, in which
  case Habitat is a candidate rather than an addition, and the "replacement or
  nothing" framing stops being a cost.

## Alternatives considered

- **Keep reading and decide from documentation.** Rejected: this is the fourth
  pass, the last one explicitly concluded the research could not settle it, and
  the two blocking items are runtime behaviour.
- **Run the upstream container with Podman.** Least work and upstream-supported,
  but it breaks the prime directive for an *evaluation*, which is the worst
  reason to break it. The happyview role already shows source builds are the
  house answer to "no binary published."
- **Prototype the member registry on it in parallel.** Rejected for now: it
  collides with "replacement or nothing" and puts migration work in front of the
  questions that decide whether to migrate at all.
- **Wire Corliss to it read-only, just to try the integration.** Rejected. This
  is the tempting one, and it is exactly the two-identity-stores failure with a
  reassuring adjective attached.
