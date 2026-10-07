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
  - deploy
  - ansible
  - spec
---

# 0005 — Spec: scn-chat publish runbook (deploy to production SCN via Ansible)

Tracks [scn-chat#8](https://github.com/Z-Space-Society/scn-chat/issues/8)
("Make runbook for publishing scn-chat"). Goal: define how
[scn-chat](https://github.com/Z-Space-Society/scn-chat) — the SCN AI-assistant
chat app — is deployed to production SCN with the zai-ops Ansible stack.

## 1. What scn-chat is

ATProto-native AI assistant chat. Users sign in **with their atproto account**
(OAuth); chat history lives in **permissioned spaces** on the user's spaces-
capable PDS (`network.sharedcomputer.chat.*` records) with a **server SQLite
fallback** for users whose PDS lacks spaces (phase 1 default: local DB + atproto
sign-in).

- Monorepo (pnpm): `apps/server` (Hono API, runs the web build), `apps/web`
  (Vite frontend), `packages/lexicons`, `packages/plugin-api`, `plugins/`.
- **Single service, zero external infra**: sqlite at `DATA_DIR` (default
  `/data/scn-chat.sqlite`), no postgres/redis/object-store dependency. Binds
  `127.0.0.1:3000` locally; reverse proxy terminates TLS.
- Upstream deploy = one `docker compose` service (`node:24-slim`, build
  python3/make/g++ for better-sqlite3, `pnpm install --frozen-lockfile` +
  `pnpm build`). **No Dockerfile logic we need beyond a build recipe.**
- Env: `SECRET_KEY` + `OAUTH_PRIVATE_KEYS` (from `pnpm keys`),
  `PUBLIC_URL` (the HTTPS URL — becomes the OAuth client id, a `did:web`),
  `OAUTH_SCOPE_MODE` (`permission-set` in prod), `ADMIN_DIDS`,
  `NODE_ENV`, `DATABASE_URL`, `DATA_DIR`.
- Health: `GET /api/health` (Hono, DB query) — the Corliss probe target.

## 2. Key decision — native Node LXC, no Docker

scn-chat is a **single stateless-ish Node service with sqlite** — unlike the
lasuite compose stack, there is nothing here that needs containers. Port it
**natively** onto an LXC CT: pinned Node 24 + pnpm, systemd unit, sqlite on the
CT rootfs, Caddy in front. Consistent with issue #17 ("no Docker/Podman") and
the generic-LXC-per-service model. The checked-in Dockerfile/compose stay
upstream as the dev/CI path, not the SCN runtime.

## 3. Reference deploy → SCN mapping

| Upstream (compose) | SCN (zai-ops) |
|---|---|
| `node:24-slim` image | Node 24 linux-x64 tarball on the CT (pinned, checksummed) + `build-essential python3` for better-sqlite3 |
| `pnpm install --frozen-lockfile && pnpm build` | same on the CT via corepack |
| `.env:/app/.env:ro` | rendered `/opt/scn-chat/.env` (0600) from ansible-vault vars |
| `./data:/data` (sqlite) | `/data` on the CT rootfs; add to restic paths in `bin/zai-backup` |
| `127.0.0.1:3000` | `10.1.1.{{ ctid }}:3000` (internal only) |
| reverse proxy + TLS | `proxy` role: Caddy route `chat.{{ cluster_domain }}` — on SCN: `chat.sharedcomputer.network` |
| "lexicons and permission set published" | **the app's responsibility** (upstream deploy concern; Q5 answered) |
| Admin bootstrap (Settings → Admin) | manual post-deploy step; `ADMIN_DIDS` from the cluster admin roster |

## 4. Implementation (adding-a-service.md checklist)

1. **Inventory + CTID** — add `scn-chat` under `service_containers` in
   `ansible/inventory/hosts.yml`; runtime CTID via `zai-assign scn-chat <ctid>`.
   No data dependencies → no ordering row in `diagrams.md`.
2. **Role** — new `ansible/roles/scn_chat/` (source-build model, copy
   `sync_relay`):
   - Install pinned Node 24 tarball → `/usr/local`; `corepack enable` (pnpm).
   - `apt`: `python3 make g++` (better-sqlite3 native build).
   - `git` clone `Z-Space-Society/scn-chat` pinned at `scn_chat_version`
     (commit ref — repo is young; see Q9) → `/opt/scn-chat`.
   - `pnpm install --frozen-lockfile` → `pnpm build` → `pnpm migrate`.
   - Render `.env` (0600) from vault vars; `ADMIN_DIDS` is **not** a hardcoded
     var — `include_tasks: tasks/roster.yml` (`scn_roster_admin_dids`, the
     cluster admin roster, same authority Corliss and the PDS use) and
     comma-join into the env. `Restart=always` systemd unit
     `scn-chat.service` (`ExecStart=pnpm start`, `EnvironmentFile=/opt/scn-chat/.env`).
   - **Smoke test** at role end: `wait_for` `10.1.1.{{ctid}}:3000`, then `uri`
     `GET /api/health` (proves the feature, not just systemd "active").
3. **Manifest** — last task records `manifest_service: scn-chat`,
   `manifest_version: "{{ scn_chat_version }}"`; add `scn-chat.json` to the key
   table in `docs/roles/manifest.md` (Corliss reads it by that exact name).
4. **Play + route** — configure play in `provision.yml` (after `proxy`);
   `caddy_proxy_hosts` entry `chat.{{ cluster_domain }}` →
   `10.1.1.{{ctid}}:3000`. On SCN `cluster_domain == sharedcomputer.network`
   (pds role note) so the URL is `chat.sharedcomputer.network`. Subdomain
   pattern is established: OpenWebUI now serves at `owui.{{ cluster_domain }}`
   (949fa0b), so `chat.` is free.
5. **Corliss health check** — probe `_scn_chat`: `GET http://10.1.1.{{ctid}}:3000/api/health`;
   `Probe` row in `STACK` with `manifest=scn-chat`; `<SERVICE>_URL` setting +
   `.env.example`/README/tests in `corliss/health.py`.
6. **Point Corliss at it** — `corliss_scn_chat_url` default from `hostvars`,
   rendered in `corliss.env.j2`, bump `corliss_version`.

## 5. Secrets (control-node `/root/.zai-secrets`, the existing idiom — not a vault)

- `scn_chat_secret_key` — generate-once `openssl rand -base64 32` cached via a
  `pipe` lookup in `group_vars/all/main.yml` (the happyview/corliss posture).
- `scn_chat_oauth_private_keys` — the app's own `pnpm keys` output (an ES256
  private JWK), cached the same way; the lookup bootstraps a one-time
  `/root/scn-chat` checkout on the control node if the file is absent.
- Both persist under `/root/.zai-secrets` and ride the existing Tier-1
  control-node backup — no new escrow path.
- `scn_chat_public_url` (= `https://chat.{{ cluster_domain }}`),
  `node_env: production`, `oauth_scope_mode: permission-set`.
  `ADMIN_DIDS` is derived at provision time from the cluster admin roster
  (`tasks/roster.yml`) — no DIDs live in any secret store (Q4 answered).
- Provider/model API keys are **admin-UI state**, not env — bootstrapped by a
  human after first login (Q8).

## 6. Identity — bring-your-own PDS, no gating

scn-chat authenticates **atproto-native** (OAuth against the user's PDS) — the
Corliss OIDC bridge is *not* part of its login path (contrast: lasuite).
Members **bring their own logins**: any PDS works, spaces-capable or not
(full spaces experience only on 0016-capable PDSs; otherwise the server sqlite
fallback). **No gating at this stage** — guiding users to account creation or
their existing credentials is the app's job (app-layer concern, Q2/Q10
answered).

The in-house **atproto-pds is in the zai-ops blueprint**: host `pds` in
`ansible/inventory/hosts.yml` (platform tier, default CTID 114), `pds` role
merged via PR #19, public identity `pds.sharedcomputer.network`
(`cluster_domain == sharedcomputer.network`) with a `did:web` service DID. It's
the SCN-native option for members without a spaces-capable PDS — *not* a
deployment dependency of scn-chat.

Corliss still applies for the manifest/probe ("/systems/ can say whether it's
up and which version").

## 7. Rollout (phased)

1. **Phase 1 — staging (Ronchamp)**: role + play, source pin, smoke test
   `/api/health`; verify OAuth login with a test atproto account; confirm the
   app serves its OAuth client metadata at `PUBLIC_URL` (Q6).
2. **Phase 2 — production (Heron)**: assign CTID, deploy, Caddy route live
   (`chat.sharedcomputer.network`), first admin login (roster-derived).
3. **Phase 3 — observability**: Corliss probe + manifest, `/systems/` shows
   scn-chat up + version.
4. **Phase 4 — operations**: backups (Q7 → hadsie), provider/model config
   (Q8), announce to members.

## 8. Risks

- **spaces-alpha atproto packages** (root `pnpm.overrides` pins
  `0.0.0-spaces-alpha-…`) — breaking changes expected; the pin+rebuild path is
  our only stability lever (Q9 → hadsie).
- **Single-CT sqlite** — no HA; backup discipline is the safeguard (Q7 → hadsie).
- **`did:web` OAuth client tied to `PUBLIC_URL`** — changing the public domain
  later breaks the client id; choose it once (Q3, answered: `chat.` subdomain).
- **Experimental status upstream** ("do not store anything sensitive").

Open questions in the companion file:
`0005-questions-1-scn-chat-runbook.md`.