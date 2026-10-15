---
type: spec
status: accepted
project: zai-ops
owner: quill
agent: quill
created: 2026-10-06
updated: 2026-10-15
tags:
  - zai-ops
  - scn-chat
  - runbook
  - deploy
  - ansible
  - spec
---

# 0005 Spec: scn-chat publish runbook (deploy to production SCN via Ansible)

Tracks [scn-chat#8](https://github.com/Z-Space-Society/scn-chat/issues/8)
("Make runbook for publishing scn-chat"). Goal: deploy
[scn-chat](https://github.com/Z-Space-Society/scn-chat), the SCN AI-assistant
chat app, to production SCN with the zai-ops Ansible stack. The role doc,
[`docs/roles/scn_chat.md`](../../roles/scn_chat.md), is the operator's
reference; this spec records why it is shaped this way.

## 1. What scn-chat is

An atproto-native AI assistant chat. Members sign in **with their own atproto account** (OAuth against their PDS). Chat history lives in **permissioned spaces** on a spaces-capable PDS (`network.sharedcomputer.chat.*` records). Members whose PDS lacks spaces get a **server SQLite fallback**.

- **Monorepo (pnpm):** `apps/server` (Hono, which also runs the TanStack Start web app), `apps/web`, `packages/lexicons`, `packages/plugin-api`, `plugins/`.
- **One process, no external infra:** sqlite plus a local blob store in `DATA_DIR`. No postgres, redis or object-store dependency; Postgres is supported but not used here.
- **Upstream deploy:** one `docker compose` service (`node:24-slim`, `python3 make g++` for better-sqlite3, `pnpm install --frozen-lockfile && pnpm build`). Only the build recipe carries over.
- **Env:**
  - `SECRET_KEY`;
  - `OAUTH_PRIVATE_KEYS`, a JSON array of private JWKs;
  - `PUBLIC_URL`, the HTTPS origin; the OAuth client_id is `<PUBLIC_URL>/oauth-client-metadata.json` and the did:web derives from it;
  - `OAUTH_SCOPE_MODE`, `permission-set` in production;
  - `ADMIN_DIDS`, `NODE_ENV`, `DATABASE_URL`, `DATA_DIR`.
- **Health:** `GET /api/health` runs `select 1` and 503s on failure.

## 2. Key decision: native Node LXC, no Docker

scn-chat is a single Node service with sqlite, so nothing here needs containers. It runs natively on one CT: a pinned Node 24 tarball, pnpm via corepack, a systemd unit, sqlite on the rootfs, and Caddy in front. That follows the house rule of native services and no Docker on LXC. The checked-in Dockerfile and compose stay upstream as the dev path.

## 3. Reference deploy → SCN mapping

| Upstream (compose) | SCN (zai-ops) |
|---|---|
| `node:24-slim` image | Node 24 tarball (sha256-pinned) + `build-essential python3` |
| `pnpm install --frozen-lockfile && pnpm build` | Same on the CT, pnpm from the repo's `packageManager` |
| `.env:/app/.env:ro` | `/etc/scn-chat/scn-chat.env` (0600) from `/root/.zai-secrets` + the admin roster |
| `./data:/data` (sqlite) | `/opt/scn-chat/data` on the rootfs, **not backed up** (Q7) |
| source | Release tag `scn_chat_version` (Q9) |
| `127.0.0.1:3000` | `10.1.1.{{ ctid }}:3000` on vmbr1 |
| reverse proxy + TLS | `proxy` role: `chat.{{ cluster_domain }}` |
| "lexicons and permission set published" | One-time SCN-account step before first sign-in (Q14) |
| Admin bootstrap | `ADMIN_DIDS` from the cluster admin roster |

## 4. Implementation (adding-a-service.md checklist)

1. **Inventory + CTID:** `scn-chat` under `service_containers`, default CTID 122 (application tier), assigned with `scn-config`.
2. **Role** `ansible/roles/scn_chat/`, the source-build shape:
   - Node tarball;
   - pnpm via corepack;
   - git clone at `scn_chat_version`, rebuilding only when the clone changed;
   - env render;
   - a hardened unit whose `ExecStartPre` runs the migrations;
   - a smoke test on `/api/health`.
3. **Manifest:** `scn-chat.json`, version `git describe` of the checkout.
4. **Play + route:** the configure play in `provision.yml`, with a roster `pre_task`. `caddy_proxy_hosts` gets `chat.{{ cluster_domain }}` → :3000.
5. **Corliss health check:** `corliss_chat_url` renders `SCN_CHAT_URL`, guarded like `corliss_pds_url` so a cluster without scn-chat still deploys Corliss. Corliss v1.4.2 adds the `_scn_chat` probe, `manifest=scn-chat`.

## 5. Secrets

All live under `/root/.zai-secrets` on the control node and ride the Tier-1 backup:

- **`scn_chat_secret_key`:** a generate-once `openssl rand -base64 32`.
- **`scn_chat_oauth_keys`:** `query('signing_keys', 'scn_chat_oauth')`, rendered with `private_jwk` ([ADR-0011](../../decisions/0011-signing-keys.md)). It rotates with `scn-config` → Signing Keys (Q11).
- **No DIDs** in any secret store: `ADMIN_DIDS` is derived from the roster at provision time.
- **Provider, model and search keys** are admin-UI or member state, not env (Q8).

## 6. Identity: bring your own PDS, no gating

scn-chat authenticates atproto-natively, and the Corliss OIDC bridge is not in its login path. Members bring their own PDS, spaces-capable or not. Registration is open for v0.1.0 (Q13). The in-house PDS (host `pds`) is the SCN-native option, not a dependency.

## 7. Rollout

1. **Staging cluster (Ronchamp) first,** passing `scn_chat_public_url` with `:8443` (Q16).
2. **Then production (Heron)** at `chat.sharedcomputer.network`.
3. Each one is verified with:
   - PLAY RECAP showing `failed=0`;
   - one ES256 kid at `/oauth/jwks.json`;
   - `scn_chat_oauth` current in `scn-config nonint show-keys`;
   - a member sign-in;
   - the `/systems/` row.

A per-app staging CT with webhook auto-deploy was proposed as spec 0006 (PR #26) and is parked in [zai-ops#33](https://github.com/Z-Space-Society/zai-ops/issues/33). The pinned tag already makes production a deliberate step.

## 8. Risks

- **No backup** of sqlite and blobs (Q7). Losing the CT loses the data of members without spaces.
- **spaces-alpha `@atproto/*` packages:** breaking changes land without notice. The tag pins them exactly, and a bump is reviewed.
- **The did:web and OAuth client are tied to `PUBLIC_URL`.** Changing the domain later breaks the client id.
- **Open registration** (Q13), and **admin changes need a replay** (Q15).
- **Upstream is experimental** ("do not store anything sensitive").

Open questions: `0005-questions-1-scn-chat-runbook.md` (closed).
