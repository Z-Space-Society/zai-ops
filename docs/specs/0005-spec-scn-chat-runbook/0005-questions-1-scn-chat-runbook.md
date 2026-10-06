---
type: spec
status: draft
project: zai-ops
owner: quill
agent: quill
created: 2026-10-06
updated: 2026-10-06
tags:
  - zai-ops
  - scn-chat
  - runbook
  - questions
---

# 0005 Q1 — scn-chat runbook: open questions

Companion to `0005-spec-scn-chat-runbook.md`. Statuses from the 2026-10-06
review with boris; two items deferred to **hadsie** (Scott Hadfield, @hadsie).

1. **Q1 · Node install method.** Official Node 24 tarball (pinned, checksummed)
   vs nodesource apt repo. Recommendation: tarball — matches the Go/`tg`
   pattern, no third-party repo. Any objection?

2. **Q2 · Production PDS for member logins — ANSWERED (boris).** Users CAN
   bring their own logins; it's up to chat to guide them to account creation or
   using their existing credentials. **No gating** — that's an app-layer
   concern. The in-house atproto-pds is in the blueprint (host `pds`, default
   CTID 114, `pds.sharedcomputer.network`) as the SCN-native option, not a
   dependency of scn-chat.

3. **Q3 · Public URL / OAuth client id — ANSWERED (boris).**
   `chat.{{ cluster_domain }}` → **`chat.sharedcomputer.network`** on SCN
   (`cluster_domain == sharedcomputer.network`, pds role note). `chat.` is free:
   OpenWebUI just moved to `owui.{{ cluster_domain }}` (zai-ops 949fa0b). The
   URL becomes the `did:web` OAuth client id — decided once, hard to change
   later.

4. **Q4 · Admin DIDs — ANSWERED (boris).** "Zai Ops should have an admin
   definition as part of rules." Reuse the **cluster admin roster**
   (`ansible/tasks/roster.yml` → `scn_roster_admin_dids`: reads
   `network.sharedcomputer.admin.list` from the service account, the only
   authority — same list Corliss and the PDS derive). The scn_chat role
   `include_tasks`es `roster.yml` and comma-joins into `ADMIN_DIDS`; no DIDs
   committed in vault.

5. **Q5 · Lexicon + permission-set publication — ANSWERED (boris).**
   Lexicons are **up to the app** (upstream responsibility). Nothing for zai-ops
   beyond running the app's own publish flow.

6. **Q6 · OAuth client metadata.** Verify the server serves its
   `/.well-known/oauth-client` metadata at `PUBLIC_URL` (it hosts OAuth
   client code in `apps/server/src/auth/oauth-client.ts`) and conformance for
   all redirect URIs under `https://chat.sharedcomputer.network/`. Confirm in
   Phase 1 before the route goes live.

7. **Q7 · Backups — DEFERRED → hadsie.** sqlite lives at `/data` on the
   scn-chat CT. Add it to the restic path list in `bin/zai-backup`
   (control-node backup role), or a per-CT snapshot? Boris deferred to
   **@hadsie** (Scott Hadfield).

8. **Q8 · Model providers at launch.** Which OpenAI-compatible providers/
   models for the first admin bootstrap, and whose API keys (vault-managed
   vs pasted in admin UI)? Streaming model for the default assistant?

9. **Q9 · Pin policy — DEFERRED → hadsie.** The repo is young and its atproto
   deps are spaces-alpha. Pin by commit ref (recommended) with a documented
   bump process — or wait for a release tag before production? Boris deferred
   to **@hadsie**.

10. **Q10 · Non-spaces users in prod — ANSWERED (boris).** No gating at this
    stage — users can bring any PDS and the server sqlite fallback covers
    non-spaces users; guiding account creation/credentials is the app's job.
    Migration tooling to spaces remains a future app feature.