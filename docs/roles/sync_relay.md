# Role: `sync_relay`

Installs [scn-sync-relay](https://github.com/Z-Space-Society/scn-sync-relay)
**natively** as the cluster's Automerge sync server — the server end of the
[automerge-repo](https://automerge.org/) WebSocket protocol, so standard
`@automerge/automerge-repo` clients sync against it unmodified. Documents persist
to [`postgres`](postgres.md).

- **Source:** [`ansible/roles/sync_relay/`](../../ansible/roles/sync_relay/)
- **Applied by:** [`provision.yml`](../../ansible/provision.yml) (configure play, `hosts: sync-relay`, **after** the postgres play)
- **Target:** the `sync-relay` CT (CTID 113 by convention — platform tier, 110–119), over SSH, internal-only on `vmbr1`
- **Design:** [ADR-0007](../decisions/0007-sync-relay-and-space-membership.md)

## Purpose

A Rust binary run as a dedicated system user under systemd — **no Docker** (per
the [prime directive](../../CLAUDE.md)), and no Node runtime, which is most of
why the relay is `samod` rather than the JavaScript reference sync server. Its
state lives entirely in Postgres, so the CT holds no unreproducible runtime
state.

> [!danger] Phase A enforces no membership, and has no proxy route by design
> This role currently deploys the **Phase A** relay: it will sync any document
> id it holds to any peer that can reach port 7030. That is the same property
> that disqualified the upstream reference sync server, and it is acceptable
> only as a bring-up state, under two conditions that are both load-bearing:
>
> 1. **No entry in the [`proxy`](proxy.md) role's `caddy_proxy_hosts`.** `vmbr1`
>    is the entire access boundary. This is the one service here that is
>    internal-only *for a security reason* rather than for tidiness.
> 2. **Nothing real is synced into it.**
>
> The binary itself fails closed: `SCN_SYNC_RELAY_REQUIRE_AUTH` defaults to
> `true` and the Phase A build **refuses to start** when it is true, because it
> has no DID verifier. The role sets it to `false` explicitly, so running
> ungated is a written-down decision rather than a default. Flip it back to
> `true` and add the Caddy route in the same change that lands the Phase B
> verifier — not before either one.

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
| Render the env file | `template` (`0600 root`, `no_log`) | `SCN_SYNC_RELAY_DATABASE_URL`, `_BIND`, `_REQUIRE_AUTH`, `RUST_LOG`. Notifies restart. |
| Install the systemd unit | `template` → `/etc/systemd/system/scn-sync-relay.service` | Hardened; `Restart=always`. Notifies reload + restart. |
| Ensure started + enabled | `ansible.builtin.systemd` | Running now + on boot. |
| Flush handlers | `meta: flush_handlers` | Bring the daemon up with final config before the smoke test. |
| Wait for port + health endpoint | `wait_for` (`127.0.0.1:7030`) + `uri` (`/health`) | Proves the process booted and bound. `/health` deliberately does **not** touch Postgres, so this checks liveness, not storage — storage is proved by the protocol test below. |

### Handlers

| Handler | Action |
| ------- | ------ |
| `reload systemd` | `systemd: daemon_reload=true` |
| `restart scn-sync-relay` | `service: name=scn-sync-relay state=restarted` |

## Variables

Defined in [`defaults/main.yml`](../../ansible/roles/sync_relay/defaults/main.yml):

| Variable | Default | Meaning |
| -------- | ------- | ------- |
| `sync_relay_version` | `0.1.0` | Tag checked out and built (the role prefixes `v`). Bump to upgrade; the clone and build tasks re-run when this changes. `0.1.0` is Phase A — protocol + storage, no enforcement. |
| `sync_relay_require_auth` | `false` | **Phase A only.** The binary defaults this to `true` and refuses to start when true. See the danger note above before changing it. |
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

## Dependencies

- [`postgres`](postgres.md) — must be provisioned and SSH-reachable, including
  for a `--limit sync-relay` run, because the DB tasks are delegated to it.
- **Not** [`proxy`](proxy.md), deliberately. See the danger note.

### Who else reaches this

[`corliss`](corliss.md) polls `/health` from its own CT to render the relay's row
on `/systems/`, as `corliss_sync_relay_url`. Nothing about Phase A changes for
it: the probe is one unauthenticated hop over `vmbr1` to a read-only liveness
endpoint, so it neither needs nor gains anything the LAN boundary is holding
back. What it does mean is that **this role's CTID must be assigned before the
corliss role can render its env file** — that derivation is unguarded on purpose.

It also inherits the caveat above: `/health` reports liveness without touching
Postgres, so a green dot on `/systems/` says the process is serving, not that its
storage works. Corliss says so on the row rather than letting the dot overclaim.

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
curl -s http://127.0.0.1:7030/health          # ok auth=allow-all

# The bind must be the wildcard, not 10.1.1.113
ss -ltnp | grep 7030
```

From the control node:

```sh
psql -h 10.1.1.102 -U scn_sync_relay -c 'select count(*) from storage;'
```

**The protocol test.** `10.1.1.113` is on `vmbr1`, which has **no uplink** — only
the Proxmox host and CT 100 can reach it, and no browser on the LAN can. Since the
relay deliberately has no Caddy route, reach it with an SSH tunnel rather than by
opening a hole:

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

Step 1 failing is the finding Phase A exists to produce: it would mean samod's
classic automerge-repo protocol does not interoperate with the client's
prerelease, and the design changes downstream of that.

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
