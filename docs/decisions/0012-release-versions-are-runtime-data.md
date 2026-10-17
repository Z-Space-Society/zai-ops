# ADR-0012: An app's release is runtime data, a tag or `latest`

> Status: **Accepted** (2026-10-17). Revises spec 0005 Q9.

## Context

scn-chat and Corliss are cloned onto their CTs at a release tag from their
own repos. The tag was a role default (`scn_chat_version`, `corliss_version`),
so every app release meant a zai-ops commit, a zai-ops tag and a `git pull` on
the control node before the release could be deployed, even when the release
changed nothing the role renders. Spec 0005 Q9 made this the policy for
scn-chat: upgrading is a one-line bump, rolling back is reverting it.

Passing `-e scn_chat_version=…` on one run deploys a different release, but
nothing records it: the next plain provision, including one `scn-config`
offers after an unrelated change, puts the role default back.

## Decision

1. **The version is a release tag (`vX.Y.Z`) or `latest`.** `latest` is the
   newest `vX.Y.Z` tag upstream, looked up by
   [`tasks/release_ref.yml`](../../ansible/tasks/release_ref.yml) each time
   the app is provisioned. Pre-release tags and untagged commits are never
   deployed. The manifest records `git describe` of the checkout, so
   `/systems/` shows the tag, never the word `latest`.

2. **A cluster records its choice in `inventory/local.yml`**, with
   [`set-version.yml`](../../ansible/set-version.yml), driven by
   `scn-config`'s Versions menu and `scn-config nonint set-version |
   clear-version | show-versions`. It is this-cluster data like the domain and
   the CTIDs (ADR-0001). Clearing it falls back to the role default.

3. **Role defaults:** scn-chat follows `latest`; Corliss is pinned to a tag.
   Both accept either.

4. **Nothing in zai-ops provisions on its own.** A `latest` app moves only
   when someone provisions it. What triggers a provision when a tag appears is
   left to zai-ops#33.

5. **Only apps cloned at a tag from their own repo are settable.** Every other
   service installs a version its role was written against (a checksummed
   tarball, a PyPI pin, a Debian package), so moving it is a role change.
   `show-versions` still lists what each has deployed.

## Consequences

- A release that changes nothing the role renders deploys with no zai-ops
  change. A release that needs new env keys or other role wiring still needs
  one, which is Corliss's usual case: `latest` on Corliss can deploy a release
  before its role supports it, so the default stays a pinned tag.
- Rolling back is pinning the older tag and provisioning. An app whose
  migrations run forward only (scn-chat's do) is not safe to pin back across
  a migration.
- With `latest`, two provisions on different days can deploy different
  releases from the same zai-ops revision. The manifest's `version` and
  `zai_ops` fields together say what ran.
