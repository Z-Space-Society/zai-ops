# Role: `sync_relay`

Installs [scn-sync-relay](https://github.com/Z-Space-Society/scn-sync-relay)
**natively** as the cluster's Automerge sync server — the server end of the
[automerge-repo](https://automerge.org/) WebSocket protocol, so standard
`@automerge/automerge-repo` clients sync against it unmodified. Documents persist
to [`postgres`](postgres.md).

- **Source:** [`ansible/roles/sync_relay/`](../../ansible/roles/sync_relay/)
- **Applied by:** [`provision.yml`](../../ansible/provision.yml) (configure play, `hosts: sync-relay`, **after** the postgres play)
- **Target:** the `sync-relay` CT (CTID 113 by convention — platform tier, 110–119), over SSH. Public through the [`proxy`](proxy.md) at `sync.<domain>` and `mcp.<domain>`
- **Design:** [ADR-0007](../decisions/0007-sync-relay-and-space-membership.md)

## Purpose

A Rust binary run as a dedicated system user under systemd — **no Docker** (per
the [prime directive](../../CLAUDE.md)), and no Node runtime, which is most of
why the relay is `samod` rather than the JavaScript reference sync server. Its
state lives entirely in Postgres, so the CT holds no unreproducible runtime
state.

> [!important] Phase B: every connection carries a Corliss access token
> Since relay v0.2.0 the relay verifies a token issued by [`corliss`](corliss.md)
> before the websocket upgrade and lets a member open only the vaults they own.
> That is what made the proxy routes safe to add, and the two landed together
> with Corliss v1.4.0, which issues the tokens.
>
> Through Phase A (v0.1.x) the relay had no verifier, `sync_relay_require_auth`
> was `false`, and there was deliberately no proxy route. If this role is ever
> pinned back below v0.2.0, the routes in `caddy_proxy_hosts` must go with it:
> a v0.1.x relay behind a public route syncs any document to anyone.
>
> The smoke test fails the play unless `/health` reports `auth=corliss`.

**Two public routes, one unrouted prefix.** The proxy sends `sync.<domain>` to
port 7030 (the sync websocket and a member's own `/vaults`) and `mcp.<domain>`
to port 7031 (the MCP service). `/internal/*` on 7030 is answered with a 404 at
the edge: it is where Corliss lists and creates any member's vaults with the
shared service token, over `vmbr1` only.

**Build-from-source note.** The relay is our own code and no one publishes a
Debian package for it, so the role builds from source with the Rust toolchain,
the same shape as [`happyview`](happyview.md). First provision takes ~15–30 min
on a cold CT because `cargo build --release` compiles the full dependency graph;
`target/` is deleted afterwards to reclaim several GB. The CT is therefore sized
at 2 GB RAM / 16 GB disk for build headroom — the runtime footprint is far
lighter.

## Tasks

| Task | Module | Why |
| ---- | ------ | --- |
| Probe + create the `scn_sync_relay` PG role | `command`/`shell` → `su - postgres -c psql`, `delegate_to: postgres` | Postgres superuser is **peer-only** on that CT; DB setup must be delegated there. Probe `pg_roles` → `CREATE ROLE` (else `ALTER ROLE` to sync the password). Password via `$SCN_SYNC_RELAY_DB_PW` (`no_log`) so it reaches neither argv nor the Ansible log. |
| Probe + create the `scn_sync_relay` database | `command` → `su - postgres -c psql`, `delegate_to: postgres` | Probe `pg_database`, then `CREATE DATABASE OWNER scn_sync_relay`. The **schema** is not created here — the binary runs `CREATE TABLE IF NOT EXISTS` at startup, which is why the role must own the database. |
| Create `scn-sync-relay` group + user | `group`, `user` | Run the daemon unprivileged, no login shell. |
| Create home + config dirs | `ansible.builtin.file` | `/opt/scn-sync-relay` + `/opt/scn-sync-relay/bin` (root-owned), `/etc/scn-sync-relay` (root-owned, `0750`). |
| Install build dependencies | `apt` | `build-essential`, `ca-certificates`, `curl`, `git`, `libssl-dev`, `pkg-config` — the last two for the TLS stack behind `sqlx`. |
| Install Rust toolchain | `shell` (rustup) | `cargo build --release` needs Rust. Skipped if already present. The repo commits a `rust-toolchain.toml`, so rustup installs and uses the *pinned* toolchain on first build regardless of the channel this role requests. |
| Check checked-out version | `command` → `git describe --tags --exact-match` | Detect version drift; re-clone only when the pinned tag differs from what is on disk. |
| Clone source at pinned tag | `ansible.builtin.git` | `--depth 1` keeps the clone lean; `force: true` discards drift on re-pin. **The tag must be pushed** — see the gotcha below. |
| Build (`cargo build --release`) | `command` | Runs only when the clone changed or the binary is missing. No `creates:` guard, because the deleted `target/` would otherwise trigger a rebuild on every replay. Notifies restart. |
| Install binary to `/opt/scn-sync-relay/bin` | `copy` (remote_src) | `target/release/scn-sync-relay` → the stable install path. |
| Remove the Cargo build tree | `file: state=absent` | `target/` holds GB of intermediates no longer needed. The `src/` tree stays so the `git describe` probe above keeps working. |
| Render the env file | `template` (`0600 root`, `no_log`) | `SCN_SYNC_RELAY_DATABASE_URL`, `_BIND`, `_REQUIRE_AUTH`, the `_OIDC_ISSUER` and `_OIDC_JWKS_URL` it verifies tokens against, the `_SYNC_AUDIENCE` and `_MCP_AUDIENCE` they must carry, `_MCP_BIND` and `_SERVICE_TOKEN`, then `RUST_LOG`. Notifies restart. |
| Install the systemd unit | `template` → `/etc/systemd/system/scn-sync-relay.service` | Hardened; `Restart=always`. Notifies reload + restart. |
| Ensure started + enabled | `ansible.builtin.systemd` | Running now + on boot. |
| Flush handlers | `meta: flush_handlers` | Bring the daemon up with final config before the smoke test. |
| Wait for port + health endpoint | `wait_for` (`127.0.0.1:7030`) + `uri` (`/health`) | Proves the process booted and bound. `/health` deliberately does **not** touch Postgres, so this checks liveness, not storage — storage is proved by the protocol test below. |
| Assert `auth=corliss` | `assert` on the `/health` body | With auth on, anything else means the relay is serving unverified connections on a published route. A failed deploy, not a warning. |
| Wait for the MCP listener | `wait_for` (`127.0.0.1:7031`) | The second listener binds separately and can fail on its own. |
| Record the manifest | `include_role: manifest` (`sync-relay`, `sync_relay_version`) | Last task, after the smoke test, so a service that failed it never claims a version. Writes `sync-relay.json` to Garage for Corliss's `/systems/`. Warns and carries on if the write fails. See [`manifest`](manifest.md). |

### Handlers

| Handler | Action |
| ------- | ------ |
| `reload systemd` | `systemd: daemon_reload=true` |
| `restart scn-sync-relay` | `service: name=scn-sync-relay state=restarted` |

## Variables

Defined in [`defaults/main.yml`](../../ansible/roles/sync_relay/defaults/main.yml):

| Variable | Default | Meaning |
| -------- | ------- | ------- |
| `sync_relay_version` | `0.2.0` | Tag checked out and built (the role prefixes `v`). Bump to upgrade; the clone and build tasks re-run when this changes. `0.2.0` is Phase B: token verification, owner-only vaults, `/internal/vaults` and MCP. |
| `sync_relay_require_auth` | `true` | The binary's own default. It was `false` through Phase A only. |
| `sync_relay_mcp_port` | `7031` | The MCP listener, routed as `mcp.<domain>`. |
| `sync_relay_oidc_issuer` | `https://{{ cluster_domain }}` | The `iss` every token must carry. A local copy of the corliss role's `corliss_url`; keep the two in sync. |
| `sync_relay_oidc_jwks_url` | composed | Corliss's `/.well-known/jwks.json` at its internal address, derived from its CTID. |
| `sync_relay_sync_audience` / `_mcp_audience` | `https://` + the group_vars host | The `aud` a sync or MCP token must carry. Built from the same `sync_relay_sync_host` / `sync_relay_mcp_host` Corliss builds `OIDC_RESOURCES` from. |
| `sync_relay_port` / `sync_relay_host` | `7030` / `0.0.0.0` | Listen socket. The wildcard is mandatory — binding a literal `10.1.1.x` loses a cold-boot race with `systemd-networkd` and comes up silently loopback-only. |
| `sync_relay_home` / `_bin` / `_src` | `/opt/scn-sync-relay[/bin/scn-sync-relay, /src]` | Install path, binary path, source checkout. |
| `sync_relay_env_file` | `/etc/scn-sync-relay/scn-sync-relay.env` | The `0600` env read via `EnvironmentFile`. |
| `sync_relay_db_name` / `_db_user` | `scn_sync_relay` | Postgres database + role this role creates. |
| `sync_relay_database_url` | composed | `postgresql://…@{{ hostvars['postgres'].ansible_host }}:5432/…` — the address is derived, never written down. |
| `sync_relay_repo_url` | GitHub | Public repo, so no credentials are needed at build time. |

`sync_relay_db_password` is **not** a role default: it lives in
[`group_vars/all/main.yml`](../../ansible/group_vars/all/main.yml) as a
generate-once `password` lookup under `/root/.zai-secrets`, hex so it needs no
percent-encoding inside the connection string.

Also in `group_vars`, because Corliss renders them too:
`sync_relay_service_token` (generate-once, the credential for `/internal/vaults`,
not DR-critical: losing it costs a regenerate and a replay of both roles), and
`sync_relay_sync_host` / `sync_relay_mcp_host`, the two public hostnames.

## Dependencies

- [`postgres`](postgres.md) — must be provisioned and SSH-reachable, including
  for a `--limit sync-relay` run, because the DB tasks are delegated to it.
- [`corliss`](corliss.md) at v1.4.0 or later, provisioned first: the relay
  fetches its JWKS at that internal address, and only that release issues
  tokens the relay accepts.
- [`proxy`](proxy.md) for the two public routes. DNS for `sync.<domain>` and
  `mcp.<domain>` must reach the edge before Caddy can issue their certs.

### Who else reaches this

[`corliss`](corliss.md) polls `/health` from its own CT to render the relay's row
on `/systems/`, as `corliss_sync_relay_url`, and since v1.4.0 calls
`/internal/vaults` at the same address for its Notes page. The probe is one unauthenticated hop over `vmbr1` to a read-only liveness
endpoint, so it neither needs nor gains anything the LAN boundary is holding
back. What it does mean is that **this role's CTID must be assigned before the
corliss role can render its env file** — that derivation is unguarded on purpose.

It also inherits the caveat above: `/health` reports liveness without touching
Postgres, so a green dot on `/systems/` says the process is serving, not that its
storage works. Since Corliss v1.2.1 the row no longer carries a note saying so.

## Storage

One table, created by the binary at startup:

```sql
CREATE TABLE storage (key TEXT PRIMARY KEY, value BYTEA NOT NULL);
CREATE INDEX storage_key_prefix ON storage (key text_pattern_ops);
```

`text_pattern_ops` is load-bearing: the default opclass follows the database
collation and will not serve a `LIKE 'prefix%'` scan, which would quietly turn
every range read into a full scan of a table that only grows.

**This CT's data is not reproducible.** Every other service CT here can be
rebuilt from Postgres plus the blueprint; this one *is* the thing in Postgres,
and the change table grows steadily because Automerge retains the full change
graph. It belongs in the Tier-2 backup story.

## Verify

```sh
systemctl status scn-sync-relay
journalctl -u scn-sync-relay -f
curl -s http://127.0.0.1:7030/health          # ok auth=corliss

# The bind must be the wildcard, not 10.1.1.113
ss -ltnp | grep 7030
```

From the control node:

```sh
psql -h 10.1.1.102 -U scn_sync_relay -c 'select count(*) from storage;'
```

**The protocol test.** This was Phase A's bring-up check, run before the relay
had a route or a verifier. With Phase B every connection needs a Corliss access
token, so the everyday check is a member signing in from the SCN Sync plugin and
creating a vault on Corliss's Notes page. The tunnel below still reaches the
relay directly when a test needs to bypass the edge, and a client then has to
present a token:

```sh
# on your laptop; leave running
ssh -L 7030:10.1.1.113:7030 jsayles@192.168.6.99
```

Then point an automerge-repo client at `ws://localhost:7030` — note **no path**;
the client connects to exactly the URL it is given. The tunnel is authenticated by
SSH and dies when you close it, which is the property a temporary vhost would not
have. Then:

1. Create a document — `storage` gains rows.
2. Edit from a second browser — both converge.
3. `systemctl restart scn-sync-relay` — documents survive. This is what proves
   Postgres storage rather than memory.
4. Reboot the CT — same.

In Phase A, step 1 failing would have meant samod's classic automerge-repo
protocol did not interoperate with the client's prerelease. It passed, which is
what Phase B was built on.

## Notes

> [!danger] The release tag must be pushed, not just created
> `ansible.builtin.git` can only check out what origin advertises. A local-only
> tag fails at checkout with `pathspec 'v0.1.0' did not match any file(s) known
> to git`, which reads like a bad version pin rather than an unpushed tag.
> `git ls-remote --tags origin` distinguishes the two; listing tags in a local
> clone does not.

> [!note] Build on the CT, not on CT 100
> This follows [`happyview`](happyview.md): rustup on the service CT, build in
> place, delete `target/`. The alternative — build on the control node and ship
> the artifact — would put a Rust toolchain and a multi-GB cargo cache on the
> only LAN-facing CT, and the glibc the binary links against would have to match
> anyway. Both CTs are Trixie, so building where it runs is the simpler answer.

> [!note] The role is `sync_relay`; the software is `scn-sync-relay`
> The role, the inventory host and the role's variables are named for the
> *function*; the unix user, unit, paths and env-var prefix keep the crate's own
> name, because that is what the upstream repo calls itself. Same split as
> [`object_store`](object_store.md), which deploys Garage as `garage.service`
> into `/var/lib/garage`. The `scn-` prefix earns its keep on GitHub, where the
> name has to be unique; inside this repo it would just be noise.
