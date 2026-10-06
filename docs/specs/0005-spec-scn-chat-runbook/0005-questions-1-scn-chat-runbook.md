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

Companion to `0005-spec-scn-chat-runbook.md`. Answer before or during rollout:

1. **Q1 · Node install method.** Official Node 24 tarball (pinned, checksummed)
   vs nodesource apt repo. Recommendation: tarball — matches the Go/`tg`
   pattern, no third-party repo. Any objection?

2. **Q2 · Production PDS for member logins.** scn-chat needs spaces-capable
   PDSs (0016) for the full experience. Has the in-house atproto-pds (zai-ops
   `pds` role) landed on production Heron yet, or do members bring their own
   PDS for now? Does chat deployment block on the PDS being production?

3. **Q3 · Public URL / OAuth client id.** Recommend `chat.{{ cluster_domain }}`
   (subdomain, mirrors `api.{{ cluster_domain }}`). The URL becomes the
   `did:web` OAuth client id — decided once, hard to change later. Confirm the
   base domain for production SCN and the subdomain choice.

4. **Q4 · Admin DIDs.** Which atproto DIDs get `ADMIN_DIDS` at launch
   (boris? Jacob? pawl? a service DID?). List, and whether agents should be
   admins.

5. **Q5 · Lexicon + permission-set publication.** The upstream deploy
   doc requires "the lexicons and permission set published". To which PDS/repo
   does `pnpm publish-lexicons` write, and who runs it in production
   (runbook step vs once-at-bootstrap)? Is a spaces/PDS write credential
   needed?

6. **Q6 · OAuth client metadata.** Verify the server serves its
   `/.well-known/oauth-client` metadata at `PUBLIC_URL` (it hosts OAuth
   client code in `apps/server/src/auth/oauth-client.ts`) and conformance for
   all redirect URIs under `https://chat.{{ cluster_domain }}/`. Confirm in
   Phase 1 before the route goes live.

7. **Q7 · Backups.** sqlite lives at `/data` on the scn-chat CT. Add it to the
   restic path list in `bin/zai-backup` (control-node backup role), or a
   per-CT snapshot? (Backup role currently scoops control-node state only.)

8. **Q8 · Model providers at launch.** Which OpenAI-compatible providers/
   models for the first admin bootstrap, and whose API keys (vault-managed
   vs pasted in admin UI)? Streaming model for the default assistant?

9. **Q9 · Pin policy.** The repo is young and its atproto deps are
   spaces-alpha. Pin by commit ref (recommended) with a documented bump
   process — or wait for a release tag before production?

10. **Q10 · Non-spaces users in prod.** Phase 1 stores chats in the server
    DB for users whose PDS lacks spaces — acceptable for production SCN, or
    gate logins to spaces-capable PDSs until migration tooling exists?