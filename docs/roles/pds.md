# Role: `pds`

The in-house PDS — **atproto-pds** (Nick @ngerakines.me, the
[atproto-crates](https://tangled.org/ngerakines.me/atproto-crates) workspace,
**MIT**). It is the **source of the cluster's AT Protocol (atmosphere)
accounts**: it hosts the SCN DID and member accounts and speaks federation,
OAuth 2.1 and Sync 1.1. Operator guide:
[atproto-crates.com/pds.html](https://atproto-crates.com/pds.html).

This role is the deliverable for
[zai-ops#7](https://github.com/Z-Space-Society/zai-ops/issues/7).

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

Why this engine (see zai-ops#7): it is the only candidate with **delegated
invite minting today** (`PDS_ADMIN_DIDS`; narrower than first read, see
[Admin surface](#admin-surface)) **and** permissioned
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
6. Record `pds.json` in the Garage manifest bucket via the `manifest` role
   (ADR-0009), after the smoke tests pass.

### Handlers

- `reload systemd`, `restart pds`.

## Variables

Everything derives from the blueprint (ADR-0001); nothing this-cluster is
committed. Key defaults in `roles/pds/defaults/main.yml`:

| Var | Default | Meaning |
|---|---|---|
| `pds_repo_url` | `https://tangled.org/ngerakines.me/atproto-crates` | build source: Nick's canonical workspace. The SCN fork (`sharedcomputer.network/atproto-crates`, on the @commonscomputer.com knot) is not used while it trails upstream; see the comment in `defaults/main.yml` |
| `pds_revision` | `f74a3e104661…` | **the reproducibility pin**, a commit SHA |
| `pds_version` | `0.15.0-rc.6` | informational; verified against the checkout |
| `pds_hostname` | `pds.{{ cluster_domain }}` | public hostname (SCN: `pds.sharedcomputer.network`) |
| `pds_service_did` | `did:web:pds.{{ cluster_domain }}` | the PDS's own service identity |
| `pds_handle_domains` | `[".{{ cluster_domain }}"]` | handle namespace accounts get (SCN: `*.sharedcomputer.network`) |
| `pds_crawlers` | `["https://bsky.network"]` | who may crawl/announce (TODO(decision): own relay?) |
| `pds_admin_dids` | from the roster | DIDs that may mint invite codes, and nothing else. Not stored: `provision.yml`'s pds play reads the cluster's public admin roster and uses its current admins. Empty leaves `PDS_ADMIN_DIDS` out of the env file |
| `pds_email_from_address` | `pds@{{ cluster_domain }}` | the From address the PDS sends as; mail is off until the cluster relay (`smtp_url`) is also set |
| `pds_delegation_enabled` | `true` | account delegation (`/account/delegation`), default ON (boris). Needs an HTTPS origin (Caddy) and a P-256 OAuth signing key, which the server generates on first boot |
| `pds_data_dir` | `/var/lib/pds` | accounts.sqlite + repos + blobs; the unit's only `ReadWritePaths` |
| `pds_*_limit` | 16 MiB / 1 GiB | blob upload + import limits |

Network: `pds` is vmbr1-only, `10.1.1.<ctid>` derived from its assigned CTID
(platform tier), DNS + TLS at the proxy (`pds.{{ domain }}` route already wired
into `caddy_proxy_hosts`, and skipped until `pds` has a CTID). Caddy passes `subscribeRepos`
WebSocket upgrades through by default; the PDS trusts exactly one proxy hop
(`PDS_TRUSTED_PROXY_HOPS=1`).

### Secrets

`group_vars/all/main.yml`, all under `/root/.zai-secrets` (Tier-1 backed up).
The first two are generated on first run with no manual step. The other two
are optional, placed there by an operator, and the SMTP URL below is set with
`scn-config`: a missing file reads as empty and its env line is left out. The PLC key is not
wired yet.

| Secret | For |
|---|---|
| `pds_jwt_secret` | signing JWTs the server issues, access and refresh tokens included |
| `pds_admin_password` | the admin credential: HTTP basic auth for the `/admin` dashboard, every `com.atproto.admin.*` route and the `atproto-pds-admin` CLI. Not break-glass; it is how the server is administered |
| `pds_oauth_jwk_set` *(optional override)* | the PDS's service signing key, as `{"keys": [<private jwk>, ...]}`. Unset, the server generates a **P-256** key on first boot and keeps it in its key store under `/var/lib/pds`, so a fresh install needs nothing here. Only a PDS first booted on a build older than the `f74a3e1` pin needs it: those generated K-256, which account delegation refuses |
| `pds_plc_rotation_key_private` *(required for recovery)* | operator recovery for identities this server issues; **losing it is losing the accounts**. Wire before the SCN DID migration |

The service signing key signs the client assertion the PDS sends during
delegated sign-in. It does not sign access or refresh tokens, so replacing it
signs nobody out. Because the generated key lives in the data dir, it is
covered by the PDS data backup below, not by the Tier-1 secrets backup.

### Outbound email (SMTP)

The PDS sends through the cluster's mail relay: one SMTP URL, credentials
included, read from `/root/.zai-secrets/smtp_url`. The blueprint names no
provider; which relay a cluster uses is its own choice. `pds_email_from_address` is the address it
sends as (default `pds@<domain>`).

Both must be set for delivery. With the relay unset the server logs each
message's recipient and subject and drops it. Accounts can still be created,
but nothing that mails a code works: password reset, email confirmation, and
the token an account needs to sign a PLC operation (changing its identity, or
migrating away).

Set it with `scn-config`'s **Set SMTP** entry, which asks for the host, port,
username, password and how the relay does TLS, then offers to provision `pds`
so the change takes effect. Scripted: `scn-config nonint set-smtp` with the URL
on stdin, `show-smtp`, `clear-smtp`. The engine is
[`set-smtp.yml`](../README.md#playbooks).

The server parses the URL with `lettre`, which fixes two things:

- **TLS is in the URL.** `smtps://host:465` is implicit TLS;
  `smtp://host:587?tls=required` is STARTTLS. A bare `smtp://` with no `tls`
  parameter is unencrypted, password included, so `set-smtp.yml` refuses it.
- **Credentials are percent-decoded.** A username that is an email address is
  stored as `user%40example.com`; the menu encodes both fields for you.

## Dependencies

None beyond the build toolchain — no Postgres, no Redis, no S3; SQLite-only
and single-instance by design. `provision.yml` runs the `pds` play after
nothing in particular.

Nothing depends on it either, which is why it is the one optional service: the
proxy skips its route, and [Corliss](corliss.md) renders a blank `PDS_URL`,
until `pds` has a CTID. Once it does, replay `corliss` so the PDS row on
`/systems/` gets an address to check. The row's Version comes from `pds.json`.

## Verify

```
ssh pds   # from CT 100
curl http://127.0.0.1:3000/_alive          # 200 (liveness)
curl http://127.0.0.1:3000/xrpc/_health    # 200 (readiness — storage OK)
curl https://pds.<domain>/xrpc/_health     # through the edge
dig pds.<domain>                           # DNS at the edge
# account smoke: invite → createAccount → handle resolves →
# DID document at https://plc.directory/did:plc:… resolves to our hostname
```

## Who signs in where

The server has three separate doors, and none of them accepts another's
credential. This is the part that is not obvious from the pages themselves.

| Door | Who it is for | Credential |
|---|---|---|
| `/account/signin` | someone whose account is **hosted on this PDS**, to change their password or end their sessions | that account's handle (or DID, or email) and its own password. Not an app password, and not the admin password |
| `/admin`, `com.atproto.admin.*`, `atproto-pds-admin` | the operator | HTTP basic auth with `pds_admin_password` |
| `com.atproto.server.createInviteCode` | the DIDs in `PDS_ADMIN_DIDS` | a service-auth token signed with the `#atproto` key of their own DID document. There is no page for this |

Two consequences:

- **A cluster admin cannot sign in at `/account/signin`** unless they also hold
  an account here. Their admin DID lives on whichever PDS hosts it, and that
  password means nothing to this server. Being on the roster gives a DID the
  third door only.
- **A fresh PDS has no accounts**, so `/account/signin` has nothing to accept
  until the first one is created.

### Creating an account

`PDS_INVITE_REQUIRED=true`, so every account starts with an invite code, and
the first code has to come from the admin CLI. From CT 100:

```
ssh pds
env $(grep -E '^PDS_ADMIN_(PASSWORD|BASE_URL)=' /etc/pds/pds.env) \
  /opt/pds/bin/atproto-pds-admin invite create
```

The binary is not on `PATH`, and it reads the password and target from the
environment; the `env $(grep ...)` prefix lends it those two values from the
root-only env file without exporting the rest. Then open
`https://pds.<domain>/account/signin`, follow **Create one**, and enter the
code. The new account gets a `*.<domain>` handle and can use `/account/signin`
from then on.

**A code only works on the PDS that minted it.** Staging and production are
separate servers with separate code tables; a code from one fails on the other
with `invite code unknown disabled or exhausted`. Check which cluster the shell
is on before minting (both control nodes show the same prompt).

The same prefix runs the rest of the CLI: `invite list`, `invite disable`,
`account info`, `account search`, `account delete`, `takedown apply / lift /
status`. `--help` on any of them lists its flags.

## Admin surface

Administration is by the **admin password**. It is generated on the first
provision and kept on the control node at `/root/.zai-secrets/pds_admin_password`
(rendered into `/etc/pds/pds.env` as `PDS_ADMIN_PASSWORD`). It opens:

- the `atproto-pds-admin` CLI on the pds CT, as shown above;
- the `com.atproto.admin.*` XRPC routes, with basic auth;
- the HTML dashboard at `GET /admin`, with basic auth.

`PDS_ADMIN_DIDS` is **not** a second admin door. Upstream scopes it to
`createInviteCode` only: every `com.atproto.admin.*` route still requires the
password. An account hosted here gets no admin rights either, whoever it
belongs to.

**The edge does not hide `/admin` yet.** The intent recorded here was that the
dashboard and admin API stay off the public route and are reached over vmbr1.
The proxy does not implement that: the `pds.<domain>` entry in
`caddy_proxy_hosts` forwards the whole hostname, so `/admin` and
`/xrpc/com.atproto.admin.*` answer at the edge, behind basic auth alone.
TODO(decision): add a path block at the proxy, or accept basic auth as the
boundary and drop the claim.

**Who may mint invites is not a zai-ops setting.** Each time the pds
play runs, [`tasks/roster.yml`](../../ansible/tasks/roster.yml) reads the
cluster's public admin roster (the `network.sharedcomputer.admin.list` record
in the `scn_service_did` account's repo, written by
[Corliss](corliss.md)) and renders its current admins as `PDS_ADMIN_DIDS`. So
the PDS and Corliss follow one list:

- **No roster record yet:** the service account alone, the same bootstrap rule
  Corliss applies.
- **No `scn_service_did` recorded:** `PDS_ADMIN_DIDS` is left out, and invites
  come from the admin CLI only.
- **The roster cannot be read:** the play fails before touching the PDS. It
  never renders an empty list over a good one.

An admin appointed in Corliss reaches the PDS on its next
`provision.yml --limit pds`. `scn-config`'s Cluster Admins entry offers to run
that after each add or remove, and its **Apply** item (`scn-config nonint
apply-admins`, or `apply-admins pds` for the PDS alone) runs it with no roster
change at all: the way to bring a PDS built or restored after the last change
up to the current list.

## Backup

Out of the box, everything unreproducible on the PDS is in `/var/lib/pds`.
`bin/zai-backup` carries a guarded (`pds_enabled=false`) Tier-2 block that
streams that dir over SSH into the restic repo (`--tag zai-pds`); to enable:

1. Assign `pds` a CTID in `scn-config` and provision, so the CT exists and
   `hostvars['pds'].ansible_host` resolves.
2. Add `export ZAI_PDS_HOST="10.1.1.<ctid>"` to `/etc/zai-backup/restic.env`
   (rendered by the `backup` role — a follow-up can add it to
   `restic.env.j2` under a `pds_*` flag).
3. Flip `pds_enabled=true`, `git pull`, `zai-backup` once to verify the tag.

**Caveat:** a live `tar` of SQLite files is not a crash-consistent snapshot.
WAL mode usually survives (journal re-application), but the spike must verify
before the flag goes true; the hardened alternative is restic client-side on
the pds CT (snapshot `/var/lib/pds` directly) or a checkpoint-then-tar.

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
  `zai-litellm-key`.