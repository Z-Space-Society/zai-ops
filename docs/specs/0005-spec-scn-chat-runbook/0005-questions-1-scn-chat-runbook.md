---
type: spec
status: closed
project: zai-ops
owner: quill
agent: quill
created: 2026-10-06
updated: 2026-10-15
tags:
  - zai-ops
  - scn-chat
  - runbook
  - questions
---

# 0005 Q1: scn-chat runbook, open questions

Companion to `0005-spec-scn-chat-runbook.md`. Statuses come from the 2026-10-06 review with boris and his 2026-10-14 answers. On 2026-10-15 jsayles took over the PR and revised Q7 and Q9, answered Q8, and recorded Q11 to Q16. The question set is closed.

1. **Q1 · Node install method: ADOPTED (tarball).** The official Node 24 tarball, pinned and checksummed. No nodesource apt repo.

2. **Q2 · Production PDS for member logins: ANSWERED (boris).** Members bring their own logins, with no gating. Guiding them to an account is the app's job. The in-house PDS (host `pds`) is the SCN-native option, not a dependency of scn-chat.

3. **Q3 · Public URL / OAuth client id: ANSWERED (boris).** `chat.{{ cluster_domain }}`, so `chat.sharedcomputer.network` on SCN. The OAuth client_id is `<PUBLIC_URL>/oauth-client-metadata.json` and the app's did:web derives from the same origin, so it is decided once.

4. **Q4 · Admin DIDs: ANSWERED (boris).** Reuse the cluster admin roster (`ansible/tasks/roster.yml`), comma-joined into `ADMIN_DIDS`. No DIDs are committed. See Q15 for how changes reach the app.

5. **Q5 · Lexicon + permission-set publication: ANSWERED (boris).** The lexicons are the app's. See Q14 for what that asks of SCN.

6. **Q6 · OAuth client metadata: VERIFY AT DEPLOY.** Confirm `/oauth-client-metadata.json` and `/oauth/jwks.json` serve at `PUBLIC_URL`, and that a member sign-in completes. These are part of the role doc's Verify steps.

7. **Q7 · Backups: REVISED (jsayles, 2026-10-15).** **Not backed up at all.** The sqlite database and `DATA_DIR/blobs` are on the CT rootfs, and `zai-backup` does not cover them. That is accepted for v0.1.0. scn-chat supports Postgres if this needs to change.

8. **Q8 · Model providers at launch: ANSWERED (jsayles).** That is the member's problem. Members supply their own provider and search keys; SCN configures no paid admin models at launch.

9. **Q9 · Pin policy: REVISED (jsayles, 2026-10-15).** **Pin a release tag** (`scn_chat_version: v0.1.0`). Upgrading is a one-line bump, then a play run. The manifest records `git describe`, so `/systems/` shows the tag.

10. **Q10 · Non-spaces users in prod: ANSWERED (boris).** No gating. Users on any PDS work, and the server's sqlite holds the data of those without spaces (see Q7).

11. **Q11 · Signing key: DECIDED.** The atproto OAuth client key is `scn_chat_oauth` under ADR-0011 (`query('signing_keys', …)` plus `private_jwk`). It replaced a `pnpm keys` bootstrap that needed Node on the control node. It rotates with `scn-config`.

12. **Q12 · Cron: TRACKED in [zai-ops#32](https://github.com/Z-Space-Society/zai-ops/issues/32).** scn-chat expects `POST /api/cron` on a schedule. At v0.1.0 its only user, `scn-member-registry`, is not configured, so nothing needs it yet.

13. **Q13 · Registration: ACCEPTED as open for v0.1.0.** scn-chat defaults to `open`, so any atproto account can sign up. A notification on signup would need an scn-chat plugin on its sign-in hook, which doesn't exist yet. Gating to members uses `scn-member-registry`, which needs Q12.

14. **Q14 · Lexicon publishing: ACCEPTED as an SCN-account step.** Sign-in fails until the lexicons and permission set are published from the sharedcomputer.network account (`pnpm publish-lexicons`). Do this once before the first sign-in.

15. **Q15 · Admin changes: TRACKED in [scn-chat#11](https://github.com/Z-Space-Society/scn-chat/issues/11).** `ADMIN_DIDS` is read at startup, so a roster change reaches scn-chat only on the next play run. `scn-config apply-admins` deliberately does not cover it; the fix is the app following the roster itself.

16. **Q16 · Clusters on a non-standard port.** Ronchamp is reached on :8443, and `PUBLIC_URL` must carry that port. Pass `-e scn_chat_public_url=https://chat.<domain>:8443` there.

Staging (spec 0006, PR #26) is parked in [zai-ops#33](https://github.com/Z-Space-Society/zai-ops/issues/33).
