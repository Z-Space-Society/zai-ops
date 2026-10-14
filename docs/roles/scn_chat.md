---
type: role
project: zai-ops
status: draft
owner: quill
agent: quill
created: 2026-10-06
updated: 2026-10-14
tags:
  - zai-ops
  - scn-chat
  - runbook
---

# scn_chat role — scn-chat publish runbook (spec 0005)

Deploys **scn-chat**, the SCN AI-assistant chat app, as one native LXC
(**no Docker** — zai-ops #17): a pinned Node 24 tarball, the monorepo cloned
tracking **origin/main HEAD (no pins — Q9)**, `pnpm install
--frozen-lockfile` + `pnpm build`, sqlite in `DATA_DIR` on the CT rootfs, and
a systemd unit. The upstream `node:24-slim` compose container is reproduced
one-to-one on the CT.

Spec: `docs/specs/0005-spec-scn-chat-runbook/`. Issue: scn-chat#8. This
service has **no data dependencies** — no postgres/redis/object-store — so no
provision-order edge in `diagrams.md`.

## Reference deploy (upstream compose) → this role

| Upstream | Here |
| --- | --- |
| `node:24-slim` image | `node-v24.21.0` tarball (sha256-pinned) → `/opt/node-…`, symlinked onto PATH |
| `pnpm install --frozen-lockfile && pnpm build` | same on the CT via corepack (pnpm 10.33.0, the repo's `packageManager`) |
| `.env:/app/.env:ro` | `/etc/scn-chat/scn-chat.env` (0600, root) from `/root/.zai-secrets` lookups + the admin roster |
| `./data:/data` (sqlite) | `/opt/scn-chat/data` on the rootfs — covered by the SCN-wide backup (Q7) |
| source | `origin/main` HEAD each run; rebuild+restart only when HEAD moved; manifest records the deployed sha |
| `127.0.0.1:3000` | listens on all interfaces; vmbr1-only CT, `10.1.1.{{ ctid }}:3000` internally |
| reverse proxy + TLS | proxy role, Caddy route `chat.{{ cluster_domain }}` → `scn-chat:3000` |
| admin bootstrap | `ADMIN_DIDS` from the cluster admin roster (`tasks/roster.yml`) |

## Install

```console
ansible-playbook provision.yml --tags provision -l scn-chat   # or via scn-config
```

No manual secrets step: `scn_chat_secret_key` and `scn_chat_oauth_private_keys`
are generate-once lookups in `group_vars/all/main.yml`, resolved on the control
node and persisted under `/root/.zai-secrets` (the same trust domain and
Tier-1 backup as corliss's signing keys). First provision generates them —
SECRET_KEY via `openssl rand`, OAUTH_PRIVATE_KEYS from the app's own
`pnpm keys` (a throwaway `/root/scn-chat` checkout bootstraps itself if
needed). Later runs just read the cached files.

The play reads the cluster admin roster as a `pre_task`
(`tasks/roster.yml`) and the role renders `ADMIN_DIDS` from it — the same
authority Corliss and the PDS use. An empty roster **fails the play** (assert):
scn-chat has no other admin door, unlike the PDS's admin password.

## Verify

- Role smoke test: `wait_for` :3000 then `GET /api/health` → 200. The endpoint
  runs `select 1` against sqlite and 503s on failure, so a green check proves
  the unit, the `ExecStartPre` migration, and the sqlite path.
- `journalctl -u scn-chat` for app logs; `LOG_LEVEL=info`.
- Corliss `/systems/` shows scn-chat once the Corliss-side probe (`SCN_CHAT_URL`
  key, `_scn_chat`) ships — see the Corliss repo follow-up.

## Operations

- **Upgrade** = re-run the play. The role fetches `origin/main` and
  reinstalls + restarts **only when HEAD moved** (no pins — Q9); the manifest
  (`scn_chat_deployed_version`) records the exact deployed sha, and the unit's
  `ExecStartPre` migrates the sqlite schema (drizzle is idempotent).
- **Backups** = `scn_chat_data_dir` (sqlite). **Deferred to the SCN-wide
  backup setup** (Q7 answered 2026-10-14) — no per-service restic path.
- **Secrets rotation** = delete the file under `/root/.zai-secrets`
  (`scn_chat_secret_key` and/or `scn_chat_oauth_private_keys`), re-run the
  play; the next provision regenerates and restarts. Rotating the JWK makes
  every member re-consent to the OAuth client — coordinate it.
- **Domain** = `scn_chat_public_url` is `https://chat.{{ cluster_domain }}`
  (SCN: `chat.sharedcomputer.network`); it anchors the `did:web` OAuth client
  id — chosen once (Q3).

## Gotchas

- **Login is atproto OAuth, not OIDC** — members bring their own PDS; no
  gating (Q2/Q10). Corliss is not in the auth path, only the probe/manifest.
- **`OAUTH_SCOPE_MODE=permission-set` and `ALLOW_PRIVATE_NETWORKS=false`** in
  production; a fresh commit changing either is a deliberate decision.
- **The repo is experimental upstream** ("do not store anything sensitive");
  HEAD-of-main means a breaking upstream commit deploys itself on the next play
  run — the manifest's recorded sha is what Corliss reports, so a bad bump is
  visible in `/systems/` immediately.
- **Corliss pin**: the probe renders `SCN_CHAT_URL`; a corliss release without
  the key ignores it and shows no row (no migration — same as PDS).

---

# Staging profile + webhook auto-deploy (spec 0006)

The **same role** deploys the staging preview at
`chat-staging.{{ cluster_domain }}` (SCN: `chat-staging.sharedcomputer.network`)
— a second CT (`scn-chat-staging`, app tier, default CTID 123) with path/URL
overrides from its inventory host vars:

| Var (host vars) | Staging value |
| --- | --- |
| `scn_chat_home` / `scn_chat_src` / `scn_chat_data_dir` | `/opt/scn-chat-staging`(…`/src`, `/data`) |
| `scn_chat_config_dir` / `scn_chat_env_file` | `/etc/scn-chat-staging`(…`/scn-chat.env`) |
| `scn_chat_public_url` | `https://chat-staging.{{ cluster_domain }}` |
| `scn_chat_unit_name` | `scn-chat-staging` (systemd unit) |
| `scn_chat_manifest_service` | `scn-chat-staging` (`scn-chat-staging.json`) |
| `scn_chat_webhook_enabled` | `true` |

The staging play (`Configure scn-chat staging` in `provision.yml`) maps the
separate staging secrets into the role's names:
`scn_chat_staging_secret_key` → `scn_chat_secret_key`, and
`scn_chat_staging_oauth_private_keys` → `scn_chat_oauth_private_keys`. The
staging OAuth client is a different `did:web` (its own PUBLIC_URL) — **never
share the prod JWK** (0006 Q1).

## The webhook receiver (auto-deploy on commits to `main`)

`scn_chat_webhook_enabled: true` installs two extra systemd-managed pieces on
the staging CT (Node is already there — no new deps):

- **`scn-chat-webhook.service`** — a Node-stdlib HTTP receiver on
  `0.0.0.0:{{ scn_chat_webhook_port }}` (3001). It verifies
  `X-Hub-Signature-256` (HMAC-SHA256 under `scn_chat_webhook_secret`), ignores
  everything but `push` on `refs/heads/main` (204), takes a single-flight lock
  (atomic `mkdir` in `/run/scn-chat-deploy.lockd`), spawns the deploy script
  detached and answers **202 immediately**. Logs: `journalctl -u scn-chat-webhook`.
- **`{{ scn_chat_deploy_script }}`** (`/usr/local/bin/scn-chat-deploy.sh`) —
  `git fetch --depth 1 origin main` → skip if HEAD unchanged → `git reset
  --hard` → `pnpm install --frozen-lockfile` → `pnpm build` →
  `systemctl restart {{ scn_chat_unit_name }}` → wait on `/api/health` 200
  (40×3s). Same HEAD-of-main, no-pin policy as the role; the lock is released
  by a trap on every exit path. The deploy does **not** write the manifest —
  staging's `/systems/` version updates on the next play run (0006 Q7).

The role's smoke test posts a bogus signature to the receiver and requires a
**400** — the listener is up AND the HMAC gate is armed.

## Caddy route

```yaml
- domain: "chat-staging.{{ cluster_domain }}"
  service: scn-chat-staging
  port: 3000
  paths:
    - from: "/api/gh-webhook*"
      port: 3001
```

The `paths:` entry (optional) renders a `handle` path-matcher proxy before the
catch-all in `Caddyfile.j2` — GitHub's webhook delivery hits the receiver,
everything else reaches the app.

## Webhook secret + rotation

`scn_chat_webhook_secret` is a generate-once hex `password` lookup in
`group_vars/all/main.yml` → `/root/.zai-secrets/scn_chat_webhook_secret`. It
lives in **two places**: the control node AND the repo's webhook settings
(`Z-Space-Society/scn-chat` → Settings → Webhooks). Rotate **both together**:
regenerate the file (`rm` + re-run the play, or generate a new hex string),
paste the new value into the repo's webhook secret, and redeliver a test
delivery to confirm 202.