# ADR-0006 — Corliss becomes its own repo and owns the apex domain

> Status: **Accepted** (2026-08-07). Supersedes the deployment shape (not the
> choice) recorded in [ADR-0005](0005-zai-auth-over-aip.md).

## Context

`zai-auth` was built as a sub-app of this repo (`apps/zai-auth/`), deployed by
rsyncing the control node's own checkout onto the service CT. That made sense
while the app and the cluster were one project. Three things have changed:

1. **The app is a product, not a cluster detail.** An ATProto→OIDC bridge is
   useful to anyone running services behind atproto identity; nothing about it
   is specific to this cluster. Keeping it inside an infrastructure repo means
   it can never be used, forked, or contributed to on its own.
2. **The deployment was the odd one out.** Every other role installs a *pinned
   upstream release*. This one alone had "whatever is in the control node's
   working tree right now" as its version, which is unreviewable and makes a
   rollback mean reverting the infra repo.
3. **The OIDC issuer wanted the apex.** The app was served at
   `account.<domain>` and nothing served `<domain>` at all. An OIDC issuer of
   `https://example.com` must serve its discovery document at
   `https://example.com/.well-known/openid-configuration` — so a clean,
   memorable issuer (the bare domain) requires the app to own the apex.

There are **no members yet**, so every cost below is currently zero. That is
precisely why all three changes land together now rather than separately later.

## Decision

**1. The app is extracted to [`Z-Space-Society/Corliss`](https://github.com/Z-Space-Society/Corliss)** —
public, fresh history (pre-extraction history stays in this repo). Renamed
Corliss; the `zai-auth` name doesn't travel outside this cluster.

**2. The role installs it like any other package.** `ansible.builtin.git`
clones the repo at the tag in `corliss_version` (`force: true`, so the checkout
is always exactly the pin). The public repo needs no credentials, so bootstrap
reproducibility is unaffected. Upgrading is a tag bump + role replay — a
reviewable commit, and revertible by putting the old tag back.

**3. Corliss owns the apex `{{ cluster_domain }}`**, with this URL surface:

| Path | Serves | Why there |
| ---- | ------ | --------- |
| `/` | account landing page | the app is the cluster's front door |
| `/auth/login`, `/auth/logout`, `/auth/oauth/callback` | human + ATProto-client surface | namespaced so the root stays free |
| `/auth/client-metadata.json` | ATProto client metadata | it *is* the `client_id`; belongs with the rest of the client surface |
| `/.well-known/openid-configuration`, `/.well-known/jwks.json` | OIDC discovery + JWKS | **must** be at `<issuer>/.well-known/…`, and the issuer is the bare origin |
| `/oidc/authorize`, `/oidc/token` | OIDC provider endpoints | machine-only, reached via discovery; their location is free, so moving them buys nothing |
| `/admin/` | Django admin | unchanged |

`account.<domain>` is retired outright rather than redirected: with no members,
there are no bookmarks to preserve, and the old `client_id` is intentionally
dead.

## Consequences

- **The atproto `client_id` changed twice over** (new host, new path), so it is
  a new client identity: every member re-consents at their PDS once, and
  existing Corliss sessions are void. Free today, which is why it was bundled —
  doing the host move and the path move separately would have cost two
  re-consents.
- **The origin certificate must cover the apex.** A wildcard `*.<domain>` cert
  does **not** match the bare domain; Cloudflare answers such a request with a
  526 under Full (strict). Cloudflare origin certs are normally issued for
  `<domain>, *.<domain>` — verify before cutover, reissue if not.
- **Renaming the role's vars regenerated its secrets** (they're keyed by name
  under `/root/.zai-secrets`): new `SECRET_KEY`, OIDC client secret, and both
  signing keys. Intended — a fresh identity for a fresh deployment.
- **`git pull` on CT 100 no longer updates the app**, only the blueprint. This
  is a deliberate narrowing of the repo's "pull = live" convention, which was
  always about *operator scripts in `bin/`*, not about vendored application
  source.
- **The old `apps/zai-auth/` tree is left in place for now**, unreferenced by
  any role, until the cutover is confirmed working. Deleting it is a follow-up.
- The blueprint stays host-agnostic (ADR-0001): the apex is still
  `{{ cluster_domain }}` from the git-ignored `inventory/local.yml`.
