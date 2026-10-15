# Role: `scn_chat`

Installs [scn-chat](https://github.com/Z-Space-Society/scn-chat), the SCN
AI-assistant chat app, **natively** on its own CT: a pinned Node 24 tarball,
the monorepo cloned at a pinned release tag, `pnpm install --frozen-lockfile`
and `pnpm build`, sqlite in `DATA_DIR` on the rootfs, and a systemd unit.

- **Source:** [`ansible/roles/scn_chat/`](../../ansible/roles/scn_chat/)
- **Applied by:** [`provision.yml`](../../ansible/provision.yml) (configure play, `hosts: scn-chat`, after `proxy`; a roster `pre_task` supplies the admins)
- **Target:** the `scn-chat` CT (CTID 122 by convention, application tier 120-129), over SSH. Public through the [`proxy`](proxy.md) at `chat.<domain>`
- **Spec:** `docs/specs/0005-spec-scn-chat-runbook/`, tracking [scn-chat#8](https://github.com/Z-Space-Society/scn-chat/issues/8)

## Purpose

One Node process (Hono, port 3000) serves everything:

- the built web app (TanStack Start, rendered in the same process);
- the API under `/api`;
- the OAuth client endpoints (`/oauth-client-metadata.json`, `/oauth/jwks.json`);
- the app's did:web (`/.well-known/did.json`);
- the XRPC routes members' PDSes call back on (`notifyWrite`).

It also generates replies through whichever LLM provider plugin an admin configured, streaming them over server-sent events.

Members sign in with **atproto OAuth against their own PDS**. Corliss is not in the login path; it only probes and reports the service on `/systems/`.

Where a member's chats live is decided at first sign-in:
- **PDS supports spaces:** on their PDS, as `network.sharedcomputer.chat.*` records.
- **PDS doesn't support spaces** (most today): in this CT's sqlite, with attachments under `DATA_DIR/blobs`.

> [!warning] Not backed up
> The sqlite database and `DATA_DIR/blobs` live on the CT rootfs, and
> [`zai-backup`](backup.md) does not cover them (spec 0005 Q7). Losing the CT
> loses the chats of every member whose PDS lacks spaces, and every encrypted
> API key stored in the app. scn-chat supports Postgres
> (`DATABASE_URL=postgres://…`); moving to it is how this would join the
> `pg_dumpall` tier.

## Tasks

| Task | Why |
| --- | --- |
| Download + checksum-verify the Node tarball into `/opt/node-<ver>`, symlink `node`/`npm`/`npx`/`corepack` | The Garage/uv tarball pattern; no third-party apt repo |
| `corepack enable` + prepare `pnpm@<scn_chat_pnpm_version>` | The repo's own `packageManager` pin |
| apt `build-essential python3 git …` | better-sqlite3 builds from source when no prebuilt binary matches |
| `git` clone at `scn_chat_version`, `force: true` | The pin; the clone's `changed` flag triggers the rebuild |
| `git describe --tags --always` | What the manifest records, e.g. `v0.1.0` |
| `pnpm install --frozen-lockfile` + `pnpm build`, only when the clone changed or `node_modules` is missing | A replay costs seconds |
| System user/group, home, data (0750) and config (0750) dirs | The service runs unprivileged; the checkout is root-owned and read-only to it |
| Assert both secrets resolved, and at least one admin | Fail before rendering a broken env, with a message that names the cause |
| Render `/etc/scn-chat/scn-chat.env` (0600, `no_log`) | One value per line; `OAUTH_PRIVATE_KEYS` is one JSON line |
| Install the hardened unit, start, flush handlers | `ExecStartPre` runs the idempotent migrations on every start |
| `wait_for` :3000, then `GET /api/health` = 200 | The endpoint runs `select 1` against sqlite, so a 200 proves unit, migration and storage |
| Manifest `scn-chat.json` | After the smoke test, so a failed deploy never claims a version |

## Variables

| Variable | Default | Notes |
| --- | --- | --- |
| `scn_chat_version` | `v0.1.0` | Release tag. Bump to upgrade; revert the commit to roll back |
| `scn_chat_repo_url` | `https://github.com/Z-Space-Society/scn-chat` | |
| `scn_chat_node_version` / `scn_chat_node_sha256` | `v24.21.0` / pinned | Change together |
| `scn_chat_pnpm_version` | `10.33.0` | Must match `packageManager` at the pinned tag |
| `scn_chat_port` | `3000` | Keep in sync with `corliss_chat_port` |
| `scn_chat_public_url` | `https://chat.{{ cluster_domain }}` | The OAuth client_id base and did:web origin. Must be the origin members reach, port included |
| `scn_chat_data_dir` | `/opt/scn-chat/data` | sqlite + blobs; not backed up |
| `scn_chat_database_url` | `sqlite:<data_dir>/scn-chat.sqlite` | |
| `scn_chat_plc_url` | `https://plc.directory` | |

`scn_chat_admin_dids` is set by the play from the cluster admin roster
(`tasks/roster.yml`). It is never committed.

## Secrets

Both come from [`group_vars/all/main.yml`](../../ansible/group_vars/all/main.yml) and live under `/root/.zai-secrets`, in the Tier-1 backup:

- **`SECRET_KEY`**: `scn_chat_secret_key`, a generate-once openssl value. It encrypts the API keys the app stores. `SECRET_KEYS_OLD` is rendered empty; rotating it is not wired.
- **`OAUTH_PRIVATE_KEYS`**: `scn_chat_oauth_keys`, the `signing_keys` lookup on key `scn_chat_oauth` ([ADR-0011](../decisions/0011-signing-keys.md)). The first provision generates a P-256 PEM. The env renders the list through `private_jwk`, the current key first, and the kid is the RFC 7638 thumbprint.

**Rotation:** `scn-config` → Signing Keys, then reprovision.
- `rotate-key scn_chat_oauth` adds a new current key. `@atproto/oauth-client` starts new sessions on it and refreshes existing sessions with the kid they recorded.
- `retire-key scn_chat_oauth` drops the old key and ends every session still on it. Those members sign in again.

## Dependencies

- **[`proxy`](proxy.md):** the `chat.<domain>` route. The app must be publicly reachable, because members' PDSes fetch its client metadata and JWKS during sign-in and call `notifyWrite` on it.
- **Cluster admin roster:** read at provision for `ADMIN_DIDS`. An empty roster (no registry identity recorded yet) fails the play with an assert, since scn-chat refuses to start without an admin and has no other admin door.
- **Published lexicons and permission set.** Sign-in fails until scn-chat's lexicons are published from the sharedcomputer.network account (`pnpm publish-lexicons`, scn-chat `lexicons/README.md`). This is a one-time SCN-account step, not part of this role.
- **No postgres, redis or object-store dependency.**

## Verify

```console
ansible-playbook provision.yml --limit scn-chat
```

- The role's smoke test: `GET /api/health` returns 200.
- `curl -s https://chat.<domain>/oauth/jwks.json` lists one ES256 kid. During a rotation it lists two.
- `scn-config nonint show-keys` lists `scn_chat_oauth` as `current`.
- A member sign-in completes.
- Corliss `/systems/` shows scn-chat up at `v0.1.0`. This needs Corliss v1.4.2 or later, which reads `SCN_CHAT_URL`.

## Notes

- **Admin changes need a replay.** `ADMIN_DIDS` is read at startup. A roster add or remove reaches scn-chat only on the next `provision.yml --limit scn-chat`, which restarts it. `scn-config apply-admins` does not cover scn-chat. [scn-chat#11](https://github.com/Z-Space-Society/scn-chat/issues/11) tracks the app following the roster itself.
- **Cron is not wired.** scn-chat expects `POST /api/cron` every few minutes, but its only user at v0.1.0, `scn-member-registry`, is not configured. [zai-ops#32](https://github.com/Z-Space-Society/zai-ops/issues/32) tracks it.
- **Runtime settings are app state, not ansible.** Providers, models, plugins, roles and the registration mode are all set in scn-chat's `/admin` after first sign-in. Registration defaults to `open`, accepted for v0.1.0. Model and search API keys are the member's to supply.
- **A fresh deploy can't chat yet.** Nobody can chat until an admin enables a provider plugin and configures a model, or members add their own keys.
- **`ALLOW_PRIVATE_NETWORKS=false` stays.** The CT sits on vmbr1 beside postgres, redis and litellm, and the setting keeps the app and its web-fetch and search plugins off 10.1.1.0/24.
- **Upstream is experimental.** The `@atproto/*` packages are spaces-alpha releases, pinned exactly by the tag. Read the changes between tags before bumping.
- **Staging with webhook auto-deploy** is planned, not in this round. [zai-ops#33](https://github.com/Z-Space-Society/zai-ops/issues/33) holds the work, starting from #26's first version.
