# Role: `pds`

The in-house PDS — **atproto-pds** (Nick @ngerakines.me, the
[atproto-crates](https://tangled.org/ngerakines.me/atproto-crates) workspace,
**MIT**). It is the **source of the cluster's AT Protocol (atmosphere)
accounts**: it hosts the SCN DID and member accounts and speaks federation,
OAuth 2.1 and Sync 1.1. Operator guide:
[atproto-crates.com/pds.html](https://atproto-crates.com/pds.html).

**Status: draft.** This role is the design deliverable for
[zai-ops#7](https://github.com/Z-Space-Society/zai-ops/issues/7). The pins
marked `TODO(spike)` are filled by the feasibility spike (a throwaway PDS on
the ovhproxmox node at `atprotopds.bringyourown.computer`, provisioned with
this role's semantics — see [Spike (ovhproxmox)](#spike-ovhproxmox) below)
before this lands. Nothing has run against a real cluster yet.

## Purpose

Every other member-facing surface in this cluster is built around the SCN DID
(`at://sharedcomputer.network`) — the website, Corliss login, membership. That
DID currently lives on an offshore reference PDS; this role brings account
hosting in-house: the SCN DID migrates here, members' accounts (`*.<domain>`
handles) get created here, and any atproto client (Corliss among them) talks to
it as a normal account server. It is deliberately **not** wired into Corliss as
a special dependency — no env keys, no OAuth callbacks, nothing that couples
the two deployments. It is identity infrastructure: platform tier, beside the
gateway and the sync relay.

Why this engine (from the plan): it is the only candidate with **delegated
admin today** (`PDS_ADMIN_DIDS`, `com.atproto.admin.*`) **and** permissioned
data (0016 spaces), builds as one Rust binary (no Docker), and fits the
Caddy-edge + systemd conventions of this repo. It is **SQLite-only** (per-actor
DBs + shared `accounts.sqlite`, Postgres/S3 refuse at boot) and **single
instance** — one CT, full stop; a replica count above one is a data-loss
setting.

## Tasks

Build-from-source, same shape as `happyview`:

1. Create the `pds` system user/group and the layout —
   `/opt/pds/{bin,src}` (root-owned), `/etc/pds` (root-only), and
   `/var/lib/pds` (**daemon-owned** — unlike happyview, state is on disk).
2. Install build deps + the Rust toolchain via rustup (minimal profile).
3. Clone the atproto-crates workspace at `pds_revision` (a commit SHA; upstream
   publishes no tag), then `cargo build --release --features
   clap,smtp,metrics,hickory-dns --bin pds --bin atproto-pds-admin`.
   Rebuild only when the pin moves or the binary is missing. `target/` is
   deleted after install (~6 GB).
4. Render `/etc/pds/pds.env` (0600 root — secrets via `.zai-secrets`) and the
   systemd unit, start + enable `pds`.
5. Smoke-test: wait on the port, then `/_alive` (liveness) and
   `/xrpc/_health` (readiness — opens the accounts DB).

### Handlers

- `reload systemd`, `restart pds`.

## Variables

Everything derives from the blueprint (ADR-0001); nothing this-cluster is
committed. Key defaults in `roles/pds/defaults/main.yml`:

| Var | Default | Meaning |
|---|---|---|
| `pds_repo_url` | `https://tangled.org/ngerakines.me/atproto-crates` | build source |
| `pds_revision` | `TODO(spike)` | **the reproducibility pin** — a commit SHA |
| `pds_version` | `0.15.0-rc.4` | informational; verified against the checkout |
| `pds_hostname` | `pds.{{ cluster_domain }}` | public hostname (SCN: `pds.sharedcomputer.network`) |
| `pds_service_did` | `did:web:pds.{{ cluster_domain }}` | the PDS's own service identity |
| `pds_handle_domains` | `[".{{ cluster_domain }}"]` | handle namespace accounts get (SCN: `*.sharedcomputer.network`) |
| `pds_crawlers` | `["https://bsky.network"]` | who may crawl/announce (TODO(decision): own relay?) |
| `pds_admin_dids` | `[]` | delegated admins — set per cluster with `zai-set-pds-admin` |
| `pds_data_dir` | `/var/lib/pds` | accounts.sqlite + repos + blobs; the unit's only `ReadWritePaths` |
| `pds_*_limit` | 16 MiB / 1 GiB | blob upload + import limits |

Network: `pds` is vmbr1-only, `10.1.1.<ctid>` derived from its assigned CTID
(platform tier — next free is 114), DNS + TLS at the proxy (`pds.{{ domain }}`
route already wired into `caddy_proxy_hosts`). Caddy passes `subscribeRepos`
WebSocket upgrades through by default; the PDS trusts exactly one proxy hop
(`PDS_TRUSTED_PROXY_HOPS=1`).

### Secrets (auto-generated — no manual step)

`group_vars/all/main.yml`, all under `/root/.zai-secrets` (Tier-1 backed up):

| Secret | For |
|---|---|
| `pds_jwt_secret` | signing JWTs the server issues |
| `pds_admin_password` | the `/admin` staff dashboard (break-glass) |
| `pds_oauth_jwk_set` *(TODO(spike))* | multi-key JWK set signing OAuth tokens |
| `pds_plc_rotation_key_private` *(TODO(spike))* | operator recovery for identities it issues — **losing it is losing the accounts** |

The last two need generator commands confirmed in the workspace; the env
template emits them only when defined, so the role boots without them today.

## Dependencies

None beyond the build toolchain — no Postgres, no Redis, no S3; SQLite-only
and single-instance by design. `provision.yml` runs the `pds` play after
nothing in particular.

## Verify

```
ssh pds   # from CT 100
curl http://127.0.0.1:3000/_alive          # 200 (liveness)
curl http://127.0.0.1:3000/xrpc/_health    # 200 (readiness — storage OK)
curl https://pds.<domain>/xrpc/_health     # through the edge
dig pds.<domain>                           # DNS at the edge
# account smoke (post-spike): invite → createAccount → handle resolves →
# DID document at https://plc.directory/did:plc:… resolves to our hostname
```

## Admin surface

The `/admin` dashboard and the `com.atproto.admin.*` API are **not** routed
publicly (deliberately absent from `caddy_proxy_hosts`). Staff operate them
over vmbr1 — from CT 100, `ssh pds` then curl `127.0.0.1:3000/xrpc/…`, or the
`atproto-pds-admin` binary on the CT — and administration by delegated DIDs
(`PDS_ADMIN_DIDS`) is the sanctioned door. Reconsider exposing a public admin
route only under explicit review. `zai-set-pds-admin <did:plc:…>` records the
admin list in the runtime inventory (replaces wholesale; comma-separate for
several).

## Backup

Out of the box, everything unreproducible on the PDS is in `/var/lib/pds`.
`bin/zai-backup` carries a guarded (`pds_enabled=false`) Tier-2 block that
streams that dir over SSH into the restic repo (`--tag zai-pds`); to enable:

1. `zai-assign pds <ctid>` and provision, so the CT exists and
   `hostvars['pds'].ansible_host` resolves.
2. Add `export ZAI_PDS_HOST="10.1.1.<ctid>"` to `/etc/zai-backup/restic.env`
   (rendered by the `backup` role — a follow-up can add it to
   `restic.env.j2` under a `pds_*` flag).
3. Flip `pds_enabled=true`, `git pull`, `zai-backup` once to verify the tag.

**Caveat:** a live `tar` of SQLite files is not a crash-consistent snapshot.
WAL mode usually survives (journal re-application), but the spike must verify
before the flag goes true; the hardened alternative is restic client-side on
the pds CT (snapshot `/var/lib/pds` directly) or a checkpoint-then-tar.

## Spike (ovhproxmox)

The feasibility spike runs **on the ovhproxmox node** (Proxmox VE, Canada/BHS;
LAN `10.0.0.0/24` on `vmbr1`) as a throwaway LXC served at
`atprotopds.bringyourown.computer` — using **zai-ops semantics** (this role,
env-file `pds.env`, systemd unit, Caddy-edge TLS, `.zai-secrets`-style
secrets), not the full cluster playbook. Known deltas vs. the blueprint:

- **Subnet:** ovhproxmox's `vmbr1` is `10.0.0.0/24` (gateway `10.0.0.1`), not
  `10.1.1.0/24` — the spike CT gets a `10.0.0.x` address and `ansible_host`
  must be set accordingly (the committed `10.1.1.{{ ctid }}` derivation is a
  Heron-cluster fact).
- **Domain:** the spike hostname/route is `atprotopds.bringyourown.computer`
  (override `pds_hostname`/the route; the `pds.{{ cluster_domain }}` defaults
  stay generic for the real cluster).
- **CTID tier / reserved list:** pick a free ovhproxmox CTID outside the live
  guests (104, 110, 201).

The spike fills the remaining `TODO(spike)`s (key generators; the revision pin
is already verified — see below) → proof list:
1. **Pin the revision** + confirm `pds_version`; record the build features.
2. **Key generators** — exact commands for the OAuth JWK set and the PLC
   `did:key` rotation key (or write the pipe lookups).
3. **Account hosting** (the point of this server): invite → `createAccount` →
   handle `*.<spike-domain>` resolves → login to the built-in OAuth provider
   with a dev client → PLC DID resolves to this hostname → federation health.
4. **Migration endpoints** — `createAccount` (service-auth JWT) → `getRepo`
   CAR + blobs + prefs → PLC update → activate/deactivate (the SCN DID move).
5. **Delegated admin** — an admin DID drives `com.atproto.admin.*`.
6. **WebSocket + Caddy** — `subscribeRepos` through the edge.
7. **Backup consistency** — tar-stream vs. restic client for `/var/lib/pds`.
8. Spaces (0016) as a stretch — volatility caveat applies.

If the spike is clean, the ovhproxmox instance can graduate to host the SCN
DID (the plan's production target remains the Z-Space cluster's Heron node;
decision deferred until the spike).

## Notes / open items

- `PDS_INVITE_REQUIRED=true` from first boot — a PLC DID made by a stray test
  is permanent and public.
- `PDS_PRODUCTION=true` — the server refuses to start if it smells dev
  defaults (one of the operator guide's deliberate refuse-to-start modes).
- Single instance, SQLite-only: acceptable for one LXC; backed by restic.
- 0016 permissioned-data (spaces) is a Phase-C direction, not a near-term
  promise — the draft is settling.
- The SCN **account** rotation key (boris's, signed on PDS Commons Computer) is
  distinct from this server's `PDS_PLC_ROTATION_KEY_PRIVATE` (recovery for
  identities it issues). Both are DR-critical.
- Downstream: when `zai-pds` operator commands are warranted (invites, account
  inspect), add a `bin/zai-pds` wrapper over `atproto-pds-admin` like
  `zai-litellm-key`/`zai-make-admin`.