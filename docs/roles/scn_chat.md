# Role: `scn_chat`

Installs [scn-chat](https://tangled.org/sharedcomputer.network/scn-chat), the SCN
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
> [`zai-backup`](backup.md) does not cover them. That is every member's chats
> whose PDS lacks spaces, and everything the app keeps for itself: the roles,
> the invite-only access mode, every plugin secret (the registry token, the
> shared LiteLLM key), and the cron timer's API key. Losing the CT brings the
> app back open to anyone with an atproto account, with no models. Moving the
> database to Postgres and the blobs into the Tier-2 backup is
> [zai-ops#52](https://github.com/Z-Space-Society/zai-ops/issues/52).

## Tasks

| Task | Why |
| --- | --- |
| Download + checksum-verify the Node tarball into `/opt/node-<ver>-linux-x64`, symlink `node`/`npm`/`npx`/`corepack` | The Garage/uv tarball pattern; no third-party apt repo |
| `corepack enable` | The pnpm shim, shipped with Node |
| apt `build-essential python3 git …` | better-sqlite3 builds from source when no prebuilt binary matches |
| Resolve `scn_chat_version` to a tag ([`tasks/release_ref.yml`](../../ansible/tasks/release_ref.yml)) | `latest` becomes the newest `vX.Y.Z` tag upstream; a tag passes through ([ADR-0012](../decisions/0012-release-versions-are-runtime-data.md)) |
| `git` clone at that tag, `force: true` | The clone's `changed` flag triggers the rebuild, so `latest` with no new tag rebuilds nothing |
| `corepack install` in the checkout | Installs the pnpm the tag's `packageManager` names, so a pnpm bump upstream needs no change here |
| `git describe --tags --always` | What the manifest records, e.g. `v0.1.0` |
| `pnpm install --frozen-lockfile` + `pnpm build`, only when the clone changed or `node_modules` is missing | A replay costs seconds |
| System user/group, home, data (0750) and config (0750) dirs | The service runs unprivileged; the checkout is root-owned and read-only to it |
| Assert both secrets resolved, and at least one admin | Fail before rendering a broken env, with a message that names the cause |
| Render `/etc/scn-chat/scn-chat.env` (0600, `no_log`) | One value per line; `OAUTH_PRIVATE_KEYS` is one JSON line |
| Install the hardened unit, start, flush handlers | `ExecStartPre` runs the idempotent migrations on every start |
| `wait_for` :3000, then `GET /api/health` = 200 | The endpoint runs `select 1` against sqlite, so a 200 proves unit, migration and storage |
| Install `scn-chat-cron.service` + `.timer`; with an admin key recorded, render `/etc/scn-chat/cron.env` (0600, `no_log`) and enable the timer, otherwise remove the file and stop the timer | scn-chat has no scheduler; see [Cron](#cron) |
| Manifest `scn-chat.json` | After the smoke test, so a failed deploy never claims a version |

## Variables

| Variable | Default | Notes |
| --- | --- | --- |
| `scn_chat_version` | `latest` | A release tag, or `latest`, the newest one each provision. A cluster pins one with `scn-config nonint set-version scn-chat <tag>` (or the Versions menu), recorded in `inventory/local.yml` over this default. Pinning back across a migration is unsafe: they run forward only |
| `scn_chat_repo_url` | `https://knot.commonscomputer.com/sharedcomputer.network/scn-chat` | |
| `scn_chat_node_version` / `scn_chat_node_sha256` | `v24.21.0` / pinned | Change together |
| `scn_chat_port` | `3000` | Keep in sync with `corliss_chat_port` |
| `scn_chat_public_url` | `https://chat.{{ cluster_domain }}` | The OAuth client_id base and did:web origin. Must be the origin members reach, port included |
| `scn_chat_data_dir` | `/opt/scn-chat/data` | sqlite + blobs; not backed up |
| `scn_chat_database_url` | `sqlite:<data_dir>/scn-chat.sqlite` | |
| `scn_chat_plc_url` | `https://plc.directory` | |
| `scn_chat_cron_interval` | `5min` | How often the timer POSTs `/api/cron`. Also how long a revoked member keeps the `scn-member` role |
| `scn_chat_cron_env_file` | `/etc/scn-chat/cron.env` | Holds the admin key for the cron unit only, apart from the app's env |

`scn_chat_admin_dids` is set by the play from the cluster admin roster
(`tasks/roster.yml`). It is never committed.

## Secrets

Both come from [`group_vars/all/main.yml`](../../ansible/group_vars/all/main.yml) and live under `/root/.zai-secrets`, in the Tier-1 backup:

- **`SECRET_KEY`**: `scn_chat_secret_key`, a generate-once openssl value. It encrypts the API keys the app stores. `SECRET_KEYS_OLD` is rendered empty; rotating it is not wired.
- **`OAUTH_PRIVATE_KEYS`**: `scn_chat_oauth_keys`, the `signing_keys` lookup on key `scn_chat_oauth` ([ADR-0011](../decisions/0011-signing-keys.md)). The first provision generates a P-256 PEM. The env renders the list through `private_jwk`, the current key first, and the kid is the RFC 7638 thumbprint.

**Rotation:** `scn-config` → Signing Keys, then reprovision.
- `rotate-key scn_chat_oauth` adds a new current key. `@atproto/oauth-client` starts new sessions on it and refreshes existing sessions with the kid they recorded.
- `retire-key scn_chat_oauth` drops the old key and ends every session still on it. Those members sign in again.

A third secret, the admin API key the cron timer sends, is minted in the app rather than generated; see [Cron](#cron).

## Cron

`POST /api/cron` runs every scn-chat plugin's `cron` hook, and scn-chat has no scheduler of its own (its `specs/cron.md`). `scn-member-registry` adds members at sign-in but removes the `scn-member` role only on a cron run, so without cron a member revoked on SCN keeps their account, their role, and the admin models on the shared LiteLLM key.

The role installs `scn-chat-cron.timer` on the CT, the same per-CT shape as `corliss-reconcile.timer`. It starts `scn-chat-cron.service`, a oneshot that POSTs `http://127.0.0.1:3000/api/cron` every `scn_chat_cron_interval`. Over loopback, the admin key never leaves the CT.

The key holds `admin`, and only scn-chat can mint it, so it is set by hand once per cluster:

1. In scn-chat, Admin > API keys, issue a key holding `admin`. Use it for nothing else, so revoking it stops only cron.
2. `scn-config` → Cluster Settings → **scn-chat admin key**, or `scn-config nonint set-scn-chat-key` with the key on stdin. It is stored at `/root/.zai-secrets/scn_chat_admin_key`, in the Tier-1 backup.
3. Provision scn-chat; the menu offers to. The timer starts, and its first run is immediate.

With no key recorded the timer stays off and the play still succeeds. scn-chat's own `/admin/cron` page shows when cron last ran and warns when it hasn't run in a day.

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
- With an admin key recorded, `systemctl list-timers scn-chat-cron.timer` on the CT shows the next run, and scn-chat's `/admin/cron` shows the last one.
- `curl -s https://chat.<domain>/oauth/jwks.json` lists one ES256 kid. During a rotation it lists two.
- `scn-config nonint show-keys` lists `scn_chat_oauth` as `current`.
- A member sign-in completes.
- Corliss `/systems/` shows scn-chat up at `v0.1.0`. This needs Corliss v1.4.2 or later, which reads `SCN_CHAT_URL`.

## Notes

- **Admin changes need a replay.** `ADMIN_DIDS` is read at startup. A roster add or remove reaches scn-chat only on the next `provision.yml --limit scn-chat`, which restarts it. `scn-config apply-admins` does not cover scn-chat. [scn-chat#11](https://github.com/Z-Space-Society/scn-chat/issues/11) tracks the app following the roster itself.
- **Revocation needs cron.** A member revoked on SCN loses the `scn-member` role on the next cron run, so within `scn_chat_cron_interval` once the admin key is set. See [Cron](#cron).
- **Runtime settings are app state, not ansible.** Providers, models, plugins, roles and the registration mode are all set in scn-chat's `/admin` after first sign-in.
- **Only SCN members can create an account.** The `scn-member-registry` plugin (Admin > Plugins, holding a registry members token) gives SCN members the `scn-member` role, and registration is `invite` with `scn-member` as the invite role. Setup steps are in scn-chat's `plugins/scn-member-registry/README.md`. A fresh deploy starts `open` until an admin does this.
- **A fresh deploy can't chat yet.** Nobody can chat until an admin enables a provider plugin and configures a model, or members add their own keys.
- **Admin models run on one shared LiteLLM key**, limited to the `scn-member` role. It was created by hand in the LiteLLM admin UI and is entered in scn-chat's provider settings; see [`litellm`](litellm.md#four-key-management-paths-kept-deliberately-separate).
- **`ALLOW_PRIVATE_NETWORKS=false` stays.** The CT sits on vmbr1 beside postgres, redis and litellm. The flag governs DID and PDS resolution, the plugins' own fetches (web search, web fetch, the registry) and members' own model endpoints, and keeps all of them off 10.1.1.0/24. It does not govern the admin's configured provider, which the app calls directly: the shared LiteLLM key belongs at `http://10.1.1.112:4000` in the admin area, one hop over vmbr1, not at the public `api.` origin.
- **Upstream is experimental.** The `@atproto/*` packages are spaces-alpha releases, pinned exactly by the tag. On `latest`, the next provision deploys whatever was tagged last; pin a tag to read the changes first.
- **Corliss links it in the nav** as **Chat**, between Notes and API, from v1.4.2 (`CHAT_URL`, rendered from `corliss_chat_public_url`). It is members-only there, like its neighbours, and scn-chat's own sign-in is limited to members too.
- **Staging with webhook auto-deploy** is planned, not in this round. [zai-ops#33](https://github.com/Z-Space-Society/zai-ops/issues/33) holds the work, starting from #26's first version.
