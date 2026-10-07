---
type: role
project: zai-ops
status: draft
owner: quill
agent: quill
created: 2026-10-06
updated: 2026-10-06
tags:
  - zai-ops
  - scn-chat
  - runbook
---

# scn_chat role — scn-chat publish runbook (spec 0005)

Deploys **scn-chat**, the SCN AI-assistant chat app, as one native LXC
(**no Docker** — zai-ops #17): a pinned Node 24 tarball, the monorepo cloned
at a pinned commit, `pnpm install --frozen-lockfile` + `pnpm build`, sqlite in
`DATA_DIR` on the CT rootfs, and a systemd unit. The upstream `node:24-slim`
compose container is reproduced one-to-one on the CT.

Spec: `docs/specs/0005-spec-scn-chat-runbook/`. Issue: scn-chat#8. This
service has **no data dependencies** — no postgres/redis/object-store — so no
provision-order edge in `diagrams.md`.

## Reference deploy (upstream compose) → this role

| Upstream | Here |
| --- | --- |
| `node:24-slim` image | `node-v24.21.0` tarball (sha256-pinned) → `/opt/node-…`, symlinked onto PATH |
| `pnpm install --frozen-lockfile && pnpm build` | same on the CT via corepack (pnpm 10.33.0, the repo's `packageManager`) |
| `.env:/app/.env:ro` | `/etc/scn-chat/scn-chat.env` (0600, root) from ansible-vault + the admin roster |
| `./data:/data` (sqlite) | `/opt/scn-chat/data` on the rootfs — the backup unit (Q7) |
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

- **Upgrade** = bump `scn_chat_version` (a commit ref — repo is pre-1.0, Q9
  deferred to hadsie), re-run the play; the role re-clones, reinstalls and the
  unit's `ExecStartPre` migrates (drizzle is idempotent).
- **Backups** = `scn_chat_data_dir` (sqlite). Decision pending hadsie (Q7):
  restic path in `bin/zai-backup` vs per-CT snapshot.
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
- **The repo is experimental upstream** ("do not store anything sensitive").
- **Corliss pin**: the probe renders `SCN_CHAT_URL`; a corliss release without
  the key ignores it and shows no row (no migration — same as PDS).