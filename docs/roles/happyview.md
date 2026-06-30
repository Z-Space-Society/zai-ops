# Role: `happyview`

Installs [HappyView](https://happyview.dev/) **natively** as an
[AT Protocol](https://atproto.com/) AppView platform — a schema-driven framework that
auto-generates XRPC endpoints, OAuth flows, real-time network sync, and historical
backfill from uploaded lexicon schemas, with state persisted in
[`postgres`](postgres.md).

- **Source:** [`ansible/roles/happyview/`](../../ansible/roles/happyview/)
- **Applied by:** [`provision.yml`](../../ansible/provision.yml) (configure play, `hosts: happyview`, **after** the postgres play)
- **Target:** the `happyview` CT (CTID 121 by convention — apps tier, 120–129), over SSH, internal-only on `vmbr1`

## Purpose

A native HappyView install: a Rust binary run as a dedicated system user under
systemd — **no Docker** (per the [prime directive](../../CLAUDE.md)). It is
**internal-only** on `vmbr1`; the LAN reaches it through the [`proxy`](proxy.md) edge
(`view.{{ cluster_domain }}` → `happyview:3000`). Its state lives in Postgres, so the
CT holds no unreproducible runtime state.

**Build-from-source note.** HappyView's project releases are hosted on
[Tangled](https://tangled.org/) as AT Protocol blobs (not plain HTTP URLs), so the
role builds from source using the Rust toolchain + Bun (the JS runtime for the
frontend build pipeline). First provision takes ~15–30 min on a cold CT; the Cargo
build tree is cleaned afterwards to reclaim ~6 GB. The CT is therefore sized at 2 GB
RAM / 16 GB disk for build headroom — the runtime footprint is much lighter.

## Tasks

| Task | Module | Why |
| ---- | ------ | --- |
| Probe + create the `happyview` PG role | `command`/`shell` → `su - postgres -c psql`, `delegate_to: postgres` | Postgres superuser is **peer-only** on that CT; DB setup must be delegated there. Probe `pg_roles` → `CREATE ROLE` (else `ALTER ROLE` to sync the password). Password via `$HAPPYVIEW_DB_PW` (`no_log`). |
| Probe + create the `happyview` database | `command` → `su - postgres -c psql`, `delegate_to: postgres` | Probe `pg_database`, then `CREATE DATABASE OWNER happyview` if absent. |
| Create `happyview` group + user | `group`, `user` | Run the daemon unprivileged, no login shell. |
| Create home + config dirs | `ansible.builtin.file` | `/opt/happyview` + `/opt/happyview/bin` (root-owned), `/etc/happyview` (root-owned, `0750`). |
| Install build dependencies | `apt` | `build-essential`, `ca-certificates`, `curl`, `git`, `libssl-dev`, `pkg-config`, `unzip`. |
| Install Bun | `shell` (official installer) | HappyView's frontend build uses Bun (a `bun.lock` is in the repo). Skipped if already present. |
| Install Rust toolchain | `shell` (rustup) | `cargo build --release` requires Rust stable. Skipped if already present. |
| Check checked-out version | `command` → `git describe` | Detect version drift; re-clone only when the pinned tag differs. |
| Clone source at pinned tag | `ansible.builtin.git` | `--depth 1` keeps the clone lean; `force: true` handles a dirty tree on re-pin. |
| Build (`cargo build --release`) | `command` → `cargo build --release` | Compiles the Rust backend + triggers any `build.rs` frontend steps (Bun). `creates:` guard skips if the binary already exists at the matching version. Notifies restart. |
| Install binary to `/opt/happyview/bin` | `copy` (remote_src) | Copies from `target/release/happyview` to the stable install path. |
| Remove the Cargo build tree | `file: state=absent` | `target/` holds ~5+ GB of objects + deps that are no longer needed after install. |
| Render the secret env file | `template` (`0600 root`, `no_log`) | `DATABASE_URL`, `PUBLIC_URL`, `SESSION_SECRET`, `TOKEN_ENCRYPTION_KEY`, `HOST`/`PORT`. Notifies restart. |
| Install the systemd unit | `template` → `/etc/systemd/system/happyview.service` | Hardened (`ProtectSystem=strict`, `ReadWritePaths=/opt/happyview`). Notifies reload + restart. |
| Ensure started + enabled | `ansible.builtin.systemd` | Running now + on boot. |
| Flush handlers | `meta: flush_handlers` | Bring the daemon up with final config before the smoke test. |
| Wait for port + root endpoint | `wait_for` (`127.0.0.1:3000`) + `uri` (`/`) | Proves the app booted and bound the port. |

### Handlers

| Handler | Action |
| ------- | ------ |
| `reload systemd` | `systemd: daemon_reload=true` |
| `restart happyview` | `service: name=happyview state=restarted` |

## Variables

Defined in [`defaults/main.yml`](../../ansible/roles/happyview/defaults/main.yml):

| Variable | Default | Meaning |
| -------- | ------- | ------- |
| `happyview_version` | `2.10.0` | Tag checked out and built. Bump to upgrade; the build task re-runs when this changes. |
| `happyview_port` / `happyview_host` | `3000` / `0.0.0.0` | Listen socket; `0.0.0.0` so Caddy can reach it from the proxy CT. |
| `happyview_home` / `happyview_bin` / `happyview_src` | `/opt/happyview[/bin/happyview, /src]` | Install path, binary path, and source checkout. |
| `happyview_env_file` | `/etc/happyview/happyview.env` | The `0600` secret env read via `EnvironmentFile`. |
| `happyview_db_name` / `happyview_db_user` | `happyview` | Postgres database + role this role creates. |
| `happyview_public_url` | `https://view.{{ cluster_domain }}` | External URL for AT Protocol OAuth callbacks. Must match the Caddy route domain. |
| `happyview_repo_url` | Tangled repo URL | Source cloned at build time. |
| `happyview_rust_channel` | `stable` | Rustup toolchain channel. |

### Secrets (auto-generated — no manual step)

All three secrets are **generated on first run** and persisted under `/root/.zai-secrets`
on CT 100 (same posture as all other cluster secrets):

| Secret | Generation | Notes |
| ------ | ---------- | ----- |
| `happyview_db_password` | `password` lookup, hex, 48 chars | Postgres role password; safe in `DATABASE_URL` without percent-encoding. |
| `happyview_session_secret` | `password` lookup, alphanumeric, 64 chars | Signs session cookies. Stable so sessions survive a re-provision. |
| `happyview_token_encryption_key` | `pipe` lookup → `openssl rand -base64 32` | Base64-encoded 32-byte key required by HappyView. **Immutable after first use** — changing it invalidates all stored AT Protocol OAuth tokens and forces all connected accounts to re-authenticate. |

## Dependencies

- **[`postgres`](postgres.md)** must be provisioned first — this role connects to the
  postgres CT (`delegate_to: postgres`) to create its role + database. A full
  [`provision.yml`](../../ansible/provision.yml) run guarantees the order; a
  `--limit happyview` run still needs postgres already up.
- **[`proxy`](proxy.md)** exposes it to the LAN via `caddy_proxy_hosts`
  (`view.{{ cluster_domain }}`); re-run the proxy play after adding the route or set
  the domain once with `zai-set-domain`.

## Verify

```bash
# From CT 100 (curl isn't on the CT, but it binds 0.0.0.0):
ssh root@10.1.1.<ctid> 'systemctl is-active happyview'
ssh root@10.1.1.<ctid> 'ss -ltnp | grep 3000'
curl -fs http://10.1.1.<ctid>:3000/

# Database:
ssh root@<postgres-ip> "su - postgres -c 'psql -l'" | grep happyview

# End-to-end: browse https://view.<domain>, upload a lexicon schema, and verify
# the generated XRPC endpoints appear in the admin dashboard.
```

## Notes

- **First provision is slow.** `cargo build --release` downloads and compiles the
  full Rust dependency graph on a cold CT. Expect 15–30 min. Subsequent runs are fast
  (incremental build) unless the version is bumped or the Cargo cache is cold.
- **AT Protocol Service Identity.** HappyView requires a DID (Decentralized
  Identifier) registered with a handle for its OAuth server to be discoverable on the
  AT Protocol network. This is a post-install step: configure it through the HappyView
  admin dashboard after first boot. See the
  [service identity docs](https://happyview.dev/getting-started/service-identity).
- **`TOKEN_ENCRYPTION_KEY` is immutable.** The `openssl rand -base64 32` value written
  to `/root/.zai-secrets/happyview_token_key` at first provision encrypts all stored
  AT Protocol OAuth tokens. If you restore the cluster from backup, restore
  `/root/.zai-secrets/` first — a fresh CT 100 would generate a new key and render all
  previously stored tokens unreadable.
- **`happyview_public_url` must match the Caddy route.** HappyView uses `PUBLIC_URL`
  for AT Protocol OAuth callbacks; if the domain drifts from `view.{{ cluster_domain }}`
  OAuth will fail. Change both `happyview_public_url` (in defaults or inventory) and
  the Caddy route entry together.
- For how the CT is assigned a CTID, created and reached, see
  [`provision.yml`](../../ansible/provision.yml) and the
  [main docs](../README.md#service-ctid-assignment).
