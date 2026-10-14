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
  - runbook
  - ansible
  - spec
---

# 0006 — Spec: scn-chat staging runbook (chat-staging + GitHub webhook auto-deploy)

Tracks [scn-chat#10](https://github.com/Z-Space-Society/scn-chat/issues/10)
("Make staging runbook for scn-chat — chat-staging.sharedcomputer.network +
GitHub webhook auto-deploy"). Goal: a **preview environment** for
[scn-chat](https://github.com/Z-Space-Society/scn-chat) at
`chat-staging.sharedcomputer.network` that **auto-builds and auto-deploys the
codebase on every commit to `main`**, driven by a **GitHub webhook**.

Sibling: [0005 spec (production runbook)](../0005-spec-scn-chat-runbook/0005-spec-scn-chat-runbook.md)
— this spec is the delta on top of it; read 0005 first for what scn-chat is,
the no-Docker decision (zai-ops issue #17), the role shape, and the secrets
idiom.

## 1. Why staging

Production scn-chat tracks `origin/main` **HEAD with no pinning** (0005 Q9,
boris 2026-10-14) — but production only moves when an Ansible play run
executes (a human-gated cadence). Staging closes the gap:

- **Every merged commit to `main` becomes a deploy within seconds** — a live
  preview that continuously validates `main` (migrations, builds, runtime).
- **Production stays gated**: the next prod play run is the human decision;
  staging is the always-on canary, not the guard.
- The 0005 risk ("a bad upstream commit deploys itself on the next play run")
  is mitigated operationally: a bad `main` first breaks **staging**, where it
  is visible in `/systems/` (manifest version) and the running app — before
  prod ever moves.

## 2. What ships in this runbook

| Piece | Shape |
|---|---|
| Staging CT | New host `scn-chat-staging` in `ansible/inventory/hosts.yml`, app tier (120–129), **default CTID 123** (122 = prod), same 2 core / 2 GB / 16 GB profile |
| App | The **same `scn_chat` role** — parameterized for staging: `/opt/scn-chat-staging/*` paths, unit `scn-chat-staging`, URL `chat-staging.{{ cluster_domain }}`, separate sqlite |
| Webhook receiver | Minimal **Node stdlib HTTP service** (no deps — Node is already on the CT), systemd `scn-chat-webhook.service`, listening `0.0.0.0:3001` (wildcard, house rule) |
| Deploy script | `/usr/local/bin/scn-chat-deploy.sh` — fetch + `reset --hard` to `origin/main`, `pnpm install --frozen-lockfile`, `pnpm build`, `systemctl restart scn-chat-staging`, wait on `/api/health` 200 |
| Route | Caddy: `chat-staging.{{ cluster_domain }}` → app :3000, `handle /api/gh-webhook*` → receiver :3001 (same host; new optional `paths:` on a `caddy_proxy_hosts` entry) |
| Secrets | Staging `SECRET_KEY` + `OAUTH_PRIVATE_KEYS` (separate from prod — see Q1), webhook shared secret — all generate-once lookups under `/root/.zai-secrets` |
| GitHub webhook | Repo `Z-Space-Society/scn-chat` → `https://chat-staging.sharedcomputer.network/api/gh-webhook`; `application/json`; push events; signature secret = the `.zai-secrets` value (one-time manual step, Q8) |

## 3. Webhook receiver — behavior

`POST /api/gh-webhook*` (only POST; anything else → 405):

1. **Verify** `X-Hub-Signature-256` = `sha256=` + HMAC-SHA256 of the raw body
   under the shared secret (constant-time compare). Reject → 400, log.
2. **Filter** `X-GitHub-Event: push` and `ref == refs/heads/main` —
   anything else → 204 ignored.
3. **Single-flight** — run the deploy under `flock -n` (a lock in `/run`). If
   a deploy is already running (concurrent webhook deliveries): 202 + log
   "deploy already in progress", skip (Q5).
4. **Spawn detached**, reply **202 immediately** (GitHub's delivery window is
   short; the deploy takes minutes). Deploy output goes to the journal
   (`journalctl -u scn-chat-webhook`).
5. The deploy script is the **same build sequence as the 0005 role** —
   HEAD-of-main, no pins; rebuild + restart only when HEAD moved; health wait
   at the end. `git reset --hard` (not force-checkout module) keeps the
   receiver dependency-light: it uses plain git + pnpm, both already on the CT.

### Why not CI / not the prod path

- The estate rule is **no Docker** (issue #17) and the app has no CI that
  builds SCN artifacts — the runbook pattern is native builds on the CT.
- GitHub **Actions** could build+push an image, but that reintroduces Docker
  to the runtime. A webhook → native deploy keeps the same binary path as
  production (same tarball, same pnpm, same systemd) — staging and prod
  differ only in data + URL, which is the point of a preview.

## 4. Staging ↔ prod mapping

| Dimension | Production (0005) | Staging (this spec) |
|---|---|---|
| Host | `scn-chat` | `scn-chat-staging` |
| CTID | 122 (default) | **123** (default) |
| URL | `chat.sharedcomputer.network` | `chat-staging.sharedcomputer.network` |
| Paths | `/opt/scn-chat` · `/etc/scn-chat` | `/opt/scn-chat-staging` · `/etc/scn-chat-staging` |
| Unit | `scn-chat.service` | `scn-chat-staging.service` |
| sqlite | `/opt/scn-chat/data/scn-chat.sqlite` | `/opt/scn-chat-staging/data/scn-chat.sqlite` (separate store, Q2) |
| Node / pnpm | v24.21.0 tarball / pnpm 10.33.0 | **same** (same role defaults) |
| Version policy | HEAD of main, on play run | HEAD of main, **on every push to main** |
| Secrets | `scn_chat_secret_key` / `scn_chat_oauth_private_keys` | `scn_chat_staging_secret_key` / `scn_chat_staging_oauth_private_keys` |
| Admin DIDs | roster pre_task | roster pre_task (same authority) |
| Manifest | `scn-chat.json` | `scn-chat-staging.json` (Q7) |
| Corliss probe | `SCN_CHAT_URL` (corliss repo PR) | optional `SCN_CHAT_STAGING_URL` (Q4) |
| Backups | SCN-wide backup setup (0005 Q7) | same backup class — a second CT rootfs on Heron (no per-service restic path) |

## 5. Implementation (zai-ops changes on branch `scn-chat-staging`)

1. **Role parameterization** (`ansible/roles/scn_chat/`):
   - New defaults: `scn_chat_unit_name: scn-chat` (used in the systemd unit
     name + service reference), `scn_chat_manifest_service: scn-chat`,
     `scn_chat_webhook_enabled: false`, `scn_chat_webhook_port: 3001`.
   - Path/URL vars already exist; staging overrides them as **host vars** in
     the inventory (no role forks).
   - New tasks + templates behind `when: scn_chat_webhook_enabled`:
     `scn-chat-deploy.sh.j2`, `webhook.js.j2`, `scn-chat-webhook.env.j2`
     (0600), `scn-chat-webhook.service.j2`. Smoke: `uri` POST to the receiver
     with a bogus signature expects **400** (listener up + rejecting), the
     same "prove the feature" habit as the 0005 health probe.
2. **Inventory** — `scn-chat-staging` block after `scn-chat` (same profile,
   `default_ctid: 123`) with the staging path/URL/unit overrides +
   `scn_chat_webhook_enabled: true`.
3. **Play** (`ansible/provision.yml`) — `Configure scn-chat staging` after
   `Configure scn-chat`: roster `pre_task`, then `set_fact` mapping
   `scn_chat_staging_secret_key` / `scn_chat_staging_oauth_private_keys`
   into the role's var names, `roles: [scn_chat]`.
4. **Route** (`ansible/roles/proxy/defaults/main.yml`) — add:
   ```yaml
   - domain: "chat-staging.{{ cluster_domain }}"
     service: scn-chat-staging
     port: 3000
     paths:
       - from: "/api/gh-webhook*"
         port: 3001
   ```
   Template (`Caddyfile.j2`) gains an optional `paths:` rendering: a `handle
   {{ p.from }} { reverse_proxy …:{{ p.port }} }` before the catch-all
   `handle { reverse_proxy …:{{ h.port }} }` — same mutually-exclusive
   handle idiom the `redirects:` branch already uses. The no-CTID skip guard
   already covers the new host.
5. **Secrets** (`ansible/group_vars/all/main.yml`) — three generate-once
   lookups (same idiom, all under `_zai_secrets_dir`):
   - `scn_chat_staging_secret_key` (`openssl rand -base64 32`, cached)
   - `scn_chat_staging_oauth_private_keys` (`pnpm keys` from a one-time
     `/root/scn-chat-staging` bootstrap clone on the control node)
   - `scn_chat_webhook_secret` (`password` lookup, hexdigits, length 64 —
     hex so the header/HMAC path needs no escaping)
6. **Manifest** — a `scn-chat-staging.json` row in `docs/roles/manifest.md`
   (Q7); the role's last task already records `manifest_version` from the
   deployed sha (handlers unchanged — the webhook deploy path updates the
   manifest on the **next play run**, see Q7).
7. **Docs** — `docs/roles/scn_chat.md` gains a "Staging profile + webhook
   auto-deploy" section; this spec's questions file holds the open items.

## 6. GitHub webhook setup (one-time, after phase 1)

1. `Z-Space-Society/scn-chat` → Settings → Webhooks → Add webhook.
2. Payload URL: `https://chat-staging.sharedcomputer.network/api/gh-webhook`.
3. Content type: `application/json`; Secret: the value of
   `scn_chat_webhook_secret` in `/root/.zai-secrets` (control node — does not
   leave the estate; this is a repo-admin action, Q8).
4. Events: **Just the push event**; leave *active*.
5. Verify: push a commit to `main` → Deliveries tab shows 202 (or 400/red if
   the signature check fails — the receiver rejects loudly, which is the
   intended first-deploy test). The CT journal shows the deploy.

No TLS edge changes needed: `chat-staging.` is covered by Caddy's ACME
(0005 route pattern — the pds/openwebui subdomains all ride the same edge).

## 7. Rollout (phased)

1. **Phase 1 — CT + role**: inventory, play, staging secrets; provision;
   smoke `uri` health + receiver-reject probe on the CT. Verify the app's
   OAuth client metadata serves at the staging URL (same check as 0005 Q6).
2. **Phase 2 — webhook**: wire the GitHub webhook (section 6); push a
   throwaway commit; confirm deploy + `/api/health` 200 via the journal and
   Caddy.
3. **Phase 3 — route live**: `chat-staging.{{ cluster_domain }}`
   DNS/ACME; first admin login against the staging origin (staging `ADMIN_DIDS`
   = roster; provider/model keys are admin-UI state, same as prod 0005 Q8).
4. **Phase 4 — operations**: manifest visibility (Q7), Corliss staging probe
   (Q4), webhook secret rotation note (it lives in two places: GitHub repo
   settings + `.zai-secrets`).

## 8. Risks

- **A broken `main` deploys itself to staging immediately** — that is the
  feature, not a bug; mitigations: health-wait gates the deploy signal, the
  journal carries the failure, and prod never auto-moves (0005's Q9 risk is
  *reduced* because staging shows the break first).
- **Webhook signature secret durability**: it exists in GitHub repo settings
  and `.zai-secrets`; rotation = update both (documented in `scn_chat.md`).
- **Concurrent pushes race the single-flight lock** — a skipped deploy self-
  heals on the next push (Q5 policy); with per-push deliveries, `main` HEAD
  always converges.
- **Staging DB divergence** — staging sqlite is a real store (migrations run
  against it); if it diverges from prod schema (e.g. rolled-back migrations),
  a manual reset may be wanted (Q2 policy).
- **`did:web` identity at the staging origin** — the staging OAuth client is
  bound to `chat-staging.`; it must never be pointed at prod keys (Q1
  confirms separation) and it is isolated from the prod client id by
  construction (different PUBLIC_URL).

Open questions in the companion file:
`0006-questions-1-scn-chat-staging.md`.