---
type: spec
status: draft
project: zai-ops
owner: quill
agent: quill
created: 2026-10-14
updated: 2026-10-14
tags:
  - zai-ops
  - scn-chat
  - staging
  - webhook
  - questions
---

# 0006 Q1 — scn-chat staging runbook: open questions

Companion to `0006-spec-scn-chat-staging.md`. Defaults are marked where a
recommendation is made; every item is "as filed" until boris answers.

1. **Q1 · Staging OAuth identity — separate keys (recommended).** The OAuth
   client id is a `did:web` bound to `PUBLIC_URL` (0005 Q3). Staging's origin
   (`chat-staging.`) is a different client id — so staging gets its own
   `SECRET_KEY` + `OAUTH_PRIVATE_KEYS` from its own `pnpm keys` run
   (`scn_chat_staging_oauth_private_keys`). Confirm we never share prod's JWK
   (it would sign tokens for an origin that isn't the client id).

2. **Q2 · Staging DB lifecycle — persistent (recommended).** Keep a real
   sqlite store on the staging CT — migrations run against it, so staging
   validates schema changes the same way prod will. Alternative: wipe on every
   deploy for a clean preview (loses migration testing; nothing else uses the
   data). Persistent unless you say otherwise.

3. **Q3 · Webhook exposure — path + HMAC secret only (recommended).** The
   endpoint is `POST /api/gh-webhook` on the staging host, protected by the
   shared-secret signature. GitHub IP allowlisting is *possible* (GitHub
   publishes webhook IPs) but adds an ops burden as their ranges move; the HMAC
   already proves the delivery came from GitHub. Add IP filtering only if you
   want defense in depth.

4. **Q4 · Corliss visibility for staging — probe + manifest (recommended) or
   prod-only?** Adding `SCN_CHAT_STAGING_URL` + `scn-chat-staging.json`
   (manifest) lets `/systems/` show whether staging is up and at which commit.
   It is a small corliss repo change (same `health.py` pattern as the prod
   probe PR). The zai-ops side can carry the staging manifest row now; the
   Corliss probe can land when the prod probe PR lands.

5. **Q5 · Concurrent pushes — skip (recommended).** The receiver runs deploys
   under a single-flight lock; a webhook that arrives while a deploy is running
   is acknowledged (202) and skipped — the next push (or a manual replay)
   converges `main`. Alternative: queue + re-fire after the current deploy
   finishes (more moving parts; a preview env doesn't need it).

6. **Q6 · Failed deploy — leave the broken build in place (recommended).** On
   build/health failure the journal carries the error and the previous
   revision stays running only if the restart never happened — otherwise the
   broken revision is live on staging (that is the point of a preview: prod
   never sees it; `/systems/` shows the bad sha). Alternative: auto-rollback
   to the previous HEAD. Recommend manual rollback (`git reset --hard <prev>`
   + restart) for now.

7. **Q7 · Manifest update cadence for staging — next play run (recommended).**
   The webhook deploy doesn't write the manifest (it is written by the
   manifest role on play runs). Staging's `/systems/` version therefore lags
   the live CT until the next play run. Alternative: the deploy script writes a
   version file the manifest role reads — adds a shared-data contract for a
   preview env. Recommend the simple lag.

8. **Q8 · GitHub webhook setup — boris by hand (recommended).** Repo Settings →
   Webhooks is a repo-admin action; the shared secret is the `.zai-secrets`
   value (control node). The runbook documents it; a scripts/automation path
   (bot token with admin) can come later if you want re-provisioning to be
   agent-driven.

9. **Q9 · Staging audience — internal preview only (recommended).** The app is
   public at `chat-staging.` (same Caddy edge, no auth wall) but not
   advertised; admin keys are the same admin-UI bootstrap as prod (0005 Q8).
   Confirm nothing else gates it.

10. **Q10 · Deploy trigger scope — every commit on `main`, no other refs.**
    Confirm: pushes to `main` only; PRs and other branches never deploy. (The
    receiver filters `refs/heads/main`; repos can also restrict webhooks per
    branch in the UI if you want the sender to enforce it too.)