# Role: `habitat`

Installs [Habitat](https://github.com/habitat-network/habitat), the `pear`
binary, an Organizational Data Server on AT Protocol, **natively** under
systemd, as a **time-boxed evaluation instance**. Postgres-backed, fronted by
Caddy.

- **Source:** [`ansible/roles/habitat/`](../../ansible/roles/habitat/)
- **Applied by:** [`provision.yml`](../../ansible/provision.yml) (configure play, `hosts: habitat`, **after** the postgres play)
- **Target:** the `habitat` CT (platform tier, 110–119), over SSH, internal-only on `vmbr1`, reached from the LAN through [`proxy`](proxy.md)
- **Design:** [ADR-0008](../decisions/0008-habitat-evaluation.md)

## Purpose

One Go binary run as a dedicated system user under systemd, **no Docker** (per
the [prime directive](../../CLAUDE.md)), even though a container is the only
packaging upstream supports. `pear` starts everything in-process: organizations,
spaces, OpenSocial, its own OAuth 2.0 server, DID/identity, P2P, notifications,
an OpenFGA-backed permissions store, and an embedded web UI.

> [!danger] This is not a second membership store, and nothing may be wired to it
> Cluster membership stays in the registry space on
> [`happyview`](happyview.md) and Workspace membership stays in
> [`corliss`](corliss.md). Neither decision is touched by this role.
>
> The instance is deliberately consumed by **nothing**: no `corliss_*` variable
> points at it, it has no row on `/systems/`, and the relay does not read it. It
> holds no cluster members. That isolation is the whole reason it can exist
> alongside HappyView without reintroducing the two-identity-stores problem,
> running two live membership authorities is the specific failure this project
> has a standing guardrail against.
>
> Wiring anything to it is a decision that supersedes ADR-0003/ADR-0006, not a
> configuration change. See [ADR-0008](../decisions/0008-habitat-evaluation.md).

**Build-from-source note.** Upstream publishes `ghcr.io/habitat-network/pear`
and nothing else, no releases, no binaries, no Debian package. The role
reproduces the two stages of
[`build/debian/pear/Dockerfile`](https://github.com/habitat-network/habitat/blob/main/build/debian/pear/Dockerfile)
on the CT: Node + pnpm + Vite for the web UI, then a pinned Go toolchain for the
binary. Same shape as [`happyview`](happyview.md), which builds Rust + Next.js
for the same reason. The CT is sized 4 GB / 24 GB for build headroom, heavier
than happyview's because a pnpm workspace install plus a Vite build plus a Go
build is more than a `cargo build --release`. The runtime footprint is light.

## Tasks

| Task | Module | Why |
| ---- | ------ | --- |
| Probe + create the `habitat` PG role | `command`/`shell` → `su - postgres -c psql`, `delegate_to: postgres` | Postgres superuser is **peer-only** on that CT; DB setup must be delegated there. Probe `pg_roles` → `CREATE ROLE` (else `ALTER ROLE` to sync the password). Password via `$HABITAT_DB_PW` (`no_log`) so it reaches neither argv nor the Ansible log. |
| Probe + create the `habitat` database | `command` → `su - postgres -c psql`, `delegate_to: postgres` | Probe `pg_database`, then `CREATE DATABASE OWNER habitat`. The **schema** is not created here: `pear` runs goose migrations on every startup, which is why the role must own the database. |
| Garage key + bucket | `command`, `delegate_to: object-store` | Import the access key, create the bucket, grant read/write. Each guarded by its own `grep -q` on the live `garage` listing. Deliberately *not* added to the object_store role's `garage-init.sh`, which is sentinel-guarded and will not re-run. Skipped if `habitat_blob_use_garage` is `false`. |
| Create `habitat` group + user | `group`, `user` | Run the daemon unprivileged, no login shell. |
| Create home, config + blob dirs | `ansible.builtin.file` | `/opt/habitat` + `/opt/habitat/bin` (root-owned), `/etc/habitat` (root-owned `0750`), `/var/lib/habitat/blobs` (**habitat-owned**, the only path the daemon writes). |
| Install build dependencies | `apt` | `build-essential`, `ca-certificates`, `curl`, `git`, `pkg-config`. |
| Install Node.js + pnpm | `shell` (NodeSource), `apt`, `command` | Node 22 per the Dockerfile's UI stage; Debian's own is older than the toolchain expects. pnpm is pinned to the Dockerfile's version because the repo ships a `pnpm-lock.yaml` and the install runs `--frozen-lockfile`. |
| Install the Go toolchain | `get_url` (+`sha256`), `file`, `unarchive` | Debian 13 ships older than `cmd/pear` needs. Pinned + checksummed tarball, same posture as the Garage binary; `get_url` fails the run on a mismatch. The previous tree is removed before unpacking, because the tarball extracts as `go/` and would otherwise leave stale files. |
| Check checked-out version | `command` → `git describe --tags --exact-match` | Detect version drift; re-clone only when the pinned tag differs from what is on disk. |
| Clone source at pinned tag | `ansible.builtin.git` | `--depth 1` keeps the clone lean; `force: true` discards drift on re-pin. |
| Install pnpm workspace deps | `command` → `pnpm install --frozen-lockfile` | **Known divergence** from the Dockerfile, see notes. |
| Build the `internal` TS package | `command` → `pnpm --filter internal build` | `pear-pages` depends on it and pnpm does not build workspace deps implicitly. |
| Build the embedded web UI | `command` → `pnpm --filter pear-pages exec vite build --outDir …/internal/webui/dist` | **Order is load-bearing**: the UI is embedded in the Go binary, so this must land before `go build`. |
| Build `pear` and `keygen` | `command` → `go build` | Runs only when the clone changed or the binary is missing. No `creates:` guard, because the deleted build caches would otherwise trigger a rebuild on every replay. Notifies restart. |
| Detect + assert the blob driver | `shell` → `go list -deps`, `assert` | Proves the configured scheme's driver is in *this* build, turning a runtime "no driver registered" into a provisioning failure that names the drivers actually linked. See "Blob storage". |
| Mint the space signing key | `copy`, `command` → `go run`, `copy` (`delegate_to: localhost`) | `pear` requires a multibase P-256 key that **nothing upstream will generate**. See "The space signing key". |
| Remove build trees | `file: state=absent` | `node_modules`, the Go build cache and module cache are several GB. The `src/` tree stays so the `git describe` probe keeps working. |
| Render the env file | `template` (`0600 root`, `no_log`) | The whole config surface, `pear` reads every flag from a `HABITAT_`-prefixed env var, so the unit's `ExecStart` carries no arguments. Notifies restart. |
| Install the systemd unit | `template` → `/etc/systemd/system/habitat.service` | Hardened; `Restart=always`. Notifies reload + restart. |
| Ensure started + enabled | `ansible.builtin.systemd` | Running now + on boot. |
| Flush handlers | `meta: flush_handlers` | Bring the daemon up with final config before the smoke test. |
| Wait for port + root endpoint | `wait_for` (`127.0.0.1:8000`) + `uri` (`/`) | Proves the process booted, bound, and got far enough to serve the embedded UI, which, since migrations run at startup, also means Postgres was reachable. |

### Handlers

| Handler | Action |
| ------- | ------ |
| `reload systemd` | `systemd: daemon_reload=true` |
| `restart habitat` | `service: name=habitat state=restarted` |

## Variables

Defined in [`defaults/main.yml`](../../ansible/roles/habitat/defaults/main.yml):

| Variable | Default | Meaning |
| -------- | ------- | ------- |
| `habitat_version` | a `main` commit SHA | **A commit, not a tag**; no tag can be right here. See the pinning note below. |
| `habitat_port` | `8000` | Listen port. There is **no** `habitat_host`: `pear` has no bind-address flag, so it always serves on all interfaces. The literal-IP cold-boot trap that [`postgres`](postgres.md) and [`sync_relay`](sync_relay.md) guard against cannot be configured into existence here. |
| `habitat_domain` | `habitat.{{ cluster_domain }}` | `HABITAT_DOMAIN`, no scheme. **Effectively immutable**, baked into org DIDs and the OAuth issuer. |
| `habitat_home` / `_bin` / `_src` | `/opt/habitat[/bin/pear, /src]` | Install path, binary path, source checkout. |
| `habitat_keygen_bin` | `/opt/habitat/bin/keygen` | Upstream's secret generator, installed as an operator tool. Not used by the role. |
| `habitat_env_file` | `/etc/habitat/habitat.env` | The `0600` env read via `EnvironmentFile`. |
| `habitat_db_name` / `_db_user` | `habitat` | Postgres database + role this role creates. |
| `habitat_database_url` | composed | `postgres://…@{{ hostvars['postgres'].ansible_host }}:5432/…`, the address is derived, never written down. |
| `habitat_blob_dir` | `/var/lib/habitat/blobs` | Local-disk fallback path, used only when `habitat_blob_use_garage` is `false`. |
| `habitat_blob_use_garage` | `true` | Blobs on Garage over S3. `false` switches to a local-disk `file://` bucket. See "Blob storage". |
| `habitat_blob_bucket` | composed | The Garage `s3://…` string, or `file://…` when the flag above is false. |
| `habitat_node_major` / `_pnpm_version` | `22` / `11.5.1` | Mirrors the Dockerfile's UI stage, **not** `.prototools`. |
| `habitat_go_version` / `_go_sha256` | `1.27.1` | Pinned toolchain. Bump the version and the checksum together, from <https://go.dev/dl/?mode=json>. |

Secrets are **not** role defaults: they live in
[`group_vars/all/main.yml`](../../ansible/group_vars/all/main.yml) as
generate-once lookups under `/root/.zai-secrets`, `habitat_db_password` (hex,
so it needs no percent-encoding inside `HABITAT_DB`), the three base64 keys
(`habitat_pds_cred_encrypt_key`, `habitat_oauth_server_secret`,
`habitat_oauth_client_secret`), `habitat_admin_password`, and the unused Garage
pair.

`habitat_admin_password` is set explicitly for a specific reason: with it unset,
`pear` generates a random password on every boot and prints it once to stdout,
which under systemd means it lands in the journal and changes on every restart.

## Dependencies

- [`postgres`](postgres.md), must be provisioned and SSH-reachable, including
  for a `--limit habitat` run, because the DB tasks are delegated to it.
- [`proxy`](proxy.md), needs a `caddy_proxy_hosts` entry, and the origin cert
  must cover `habitat.<domain>`. Unlike [`sync_relay`](sync_relay.md) this
  service genuinely needs a public origin: org DIDs are minted against
  `HABITAT_DOMAIN` and PDS OAuth plus external DID resolution have to reach it
  from the internet.
- [`object_store`](object_store.md), for the blob bucket. Must be provisioned and
  SSH-reachable, because the bucket and key tasks are delegated to it.

### Who else reaches this

**Nothing, by design.** See the danger note at the top.

## The space signing key

`pear` marks `--space_signing_key` `Required: true` with no default and no
auto-generation, and **nothing upstream will produce a valid one**:

- `cmd/keygen` returns `encrypt.GenerateKey()`, the 32-byte base64 shape the
  three OAuth/PDS secrets use.
- `cmd/didgen` emits a **secp256k1** key as hex, and needs `--pds-url` /
  `--did-host` besides.
- Upstream's own `build/debian/pear/docker-entrypoint.sh` generates the three
  base64 secrets and **never sets this one**, so the published container cannot
  start either without it being supplied from outside.

`pear` parses the value with `atcrypto.ParsePrivateMultibase` from
[indigo](https://github.com/bluesky-social/indigo), which wants multibase
base58btc with a multicodec varint prefix, `0x86 0x26` for P-256. Rather than
hand-roll that encoding, the role copies
[`files/spacekeygen/main.go`](../../ansible/roles/habitat/files/spacekeygen/main.go)
into the source tree as a subdirectory of `cmd/pear`, so it compiles against
`cmd/pear/go.mod` and resolves the **same indigo version the server will parse
with**. It calls `atcrypto.GeneratePrivateKeyP256().Multibase()` and prints it.

The key is minted **on the service CT** (where Go is) and persisted **back** to
`/root/.zai-secrets/habitat_space_signing_key` on the control node, the same
idiom [`litellm`](litellm.md) uses to mint corliss's provisioner key. It
therefore rides the existing Tier-1 control-node backup, and a rebuilt CT reads
the same key back instead of minting a second host identity.

> [!danger] Immutable once anything is signed
> This key signs permissioned-repo commits for repo owners on external PDSes.
> Replacing it orphans everything already signed under it, with no migration
> path, the same class of immutability as
> `happyview_token_encryption_key` and corliss's two signing keys.

## Blob storage

**Garage over S3, which is what the SCN spec asks for.** `HABITAT_BLOB_BUCKET` is
a `gocloud.dev` connection string, and `pear` registers the drivers in
`internal/spaces/blobs.go`, which blank-imports `fileblob`, `gcsblob`, `memblob`
and `s3blob`. `cmd/pear/main.go` reaches that package through the `go.work`
workspace, so all four schemes work in the built binary.

> [!warning] Do not conclude a driver is missing from `cmd/pear/go.mod`
> `cmd/pear/go.mod` lists **no** AWS modules, even though `s3blob` is linked and
> cannot compile without `aws-sdk-go-v2/service/s3`. That is not a contradiction:
> `cmd/pear` is a submodule in a `go.work` workspace, and the workspace build
> takes the union of the member modules' requirements, so the AWS deps come from
> the **root** `go.mod` (where they appear as indirect). Reading the submodule's
> `go.mod` alone gives exactly the wrong answer here.

Because which schemes work is a property of the build rather than of the flag's
help text, the role asks the build rather than trusting either: `go list -deps`
on `cmd/pear`, filtered to `gocloud.dev/blob/`, then an `assert` that the
configured scheme is among them. A wrong configuration fails the play with a
message naming the real options, in the same fail-closed-and-loudly spirit as
corliss's push token.

The bucket and access key are provisioned by delegated `garage` tasks inside this
role, each with its own `grep -q` guard, rather than by extending the
`object_store` role's `garage-init.sh` (which is sentinel-guarded and will not
re-run on an initialised store). The key is `garage key import`ed rather than
generated so it survives a rebuilt object store, matching the backup key.

Two things to know about the connection string:

- **Garage is not AWS**, so it needs an explicit `endpoint`, `use_path_style` and
  `disable_https` (plain HTTP over `vmbr1`). This spelling is **unverified**
  against a running Garage; check it against the gocloud version in the root
  `go.mod` on first run. A wrong endpoint does not error clearly, it tries real
  AWS, and the journal is where that shows up.
- **Credentials go in the env file, not the URL.** `flags.go` describes the
  string as carrying "inline credentials", but the AWS env vars keep the secret
  out of the process list and out of anything that echoes the URL, and s3blob
  resolves them through the AWS default credential chain either way.

Setting `habitat_blob_use_garage: false` switches to a local-disk `file://`
bucket under `habitat_blob_dir`, skips the Garage tasks, and is the only mode
where the unit grants any `ReadWritePaths`.

## Verify

```sh
systemctl status habitat
journalctl -u habitat -f
ss -ltnp | grep 8000
curl -si http://127.0.0.1:8000/ | head -1
```

**Migrations ran against Postgres, not SQLite.** This is the check worth doing
first, because `HABITAT_DB` silently falls back: `ParseDialect()` returns a `""`
dialect for anything not prefixed `postgres` or `sqlite`. From the postgres CT:

```sh
su - postgres -c "psql -d habitat -c '\dt'"
```

That must list goose's version table plus Habitat's own tables. A healthy
service with an empty database means the DSN did not take.

Blobs land in Garage as they are uploaded through the console. On the object
store:

```sh
garage bucket info habitat-blobs
```

**Idempotency.** A second `provision.yml --limit habitat` must not rebuild.
Because the build tasks deliberately carry no `creates:` guard, this is the
specific thing to confirm after any change to them.

## First boot, by hand

Two steps Ansible does not own, the same category as
[`happyview`](happyview.md)'s hand-minted `hv_` admin key and the
scn-member-registry deploy:

1. **Create the evaluation org** in the console at `https://habitat.<domain>`,
   signing in as the instance admin (`cat
   /root/.zai-secrets/habitat_admin_password`). Org creation mints a DID bound
   to `HABITAT_DOMAIN`, which is why the domain is not reversible.
2. **Approve a test application** through their admin-approved app OAuth flow,
   if it exists on this version. This is the evaluation's primary question, see
   ADR-0008.

## Notes

> [!danger] No tag can pin this codebase, and the obvious one is a trap
> Habitat's tags do not version the software this role builds. All 28 are
> `v0.0.x-testing-N` from **mid-2024** (the newest, `v0.0.2-testing-12`, is
> dated 2024-06-18) and they point at the previous architecture, when the
> project was `github.com/eagraf/habitat-new`: go 1.22, one `cmd/node` binary,
> an **npm** frontend under `frontend/`, and a Makefile as the build system.
> None of `cmd/pear`, `go.work`, the pnpm workspace or any `HABITAT_*` flag
> exists there.
>
> The pear/OpenSocial tree, which is what their API docs, the published
> `ghcr.io/habitat-network/pear` image and this whole role describe, lives only
> on an **untagged `main`**. So `habitat_version` has to be a main commit SHA.
>
> Pinning to a tag fails several tasks deep with an unrelated-looking error
> (pnpm reporting no `package.json`, because the pnpm workspace does not exist
> at that ref). The layout probe in `tasks/main.yml` exists to turn that into a
> statement of the actual problem. Do not "fix" it by patching build paths: the
> binary, the module path and the frontend toolchain all differ.
>
> Habitat also publishes **no GitHub releases**, so their self-hosting guide's
> `curl releases/latest/download/docker-compose.yml` 404s. Do not follow it.

> [!note] Switching to a SHA pin needs two other changes
> The drift probe uses `git describe --tags --exact-match`, which cannot
> describe a SHA-pinned checkout, so it would re-clone on every run; use
> `git rev-parse HEAD` instead. And `depth: 1` is unreliable when `version` is
> a bare SHA rather than a branch or tag tip, so drop it or fetch the branch
> and reset.

> [!warning] The pnpm install diverges from upstream's Dockerfile
> Their UI stage copies in only `typescript/` before installing, so pnpm
> resolves a subset of the workspace. We have the whole clone, so the role
> installs **every** workspace package. That is slower, and it breaks if any of them
> gains an install-time requirement the UI build does not need. If that
> happens, the fix is to install with `--filter pear-pages...`, not to fight the
> extra packages. This is the likeliest place the build breaks when upstream
> moves.

> [!important] `go.work` at the repo root is load-bearing
> `cmd/pear` is its own module (`github.com/habitat-network/habitat/cmd/pear`)
> with **no `replace` directive**. Without workspace mode the build resolves the
> root module from the module proxy and embeds a web UI from a *published*
> version rather than the one just built: a wrong binary that builds cleanly.
> Do not add `-mod=mod` or any `GOFLAGS` that disables the workspace.

> [!note] The build order is not cosmetic
> The web UI is embedded in the Go binary; the only escape hatch is
> `--ui_dev_proxy`, which exists for development. So the Vite output must be at
> `internal/webui/dist` *before* `go build` runs. Unlike
> [`happyview`](happyview.md) there is no `STATIC_DIR` to point at a directory
> after the fact.

> [!note] Config is env-only
> Every flag has a `HABITAT_`-prefixed env var (`--blob_bucket` →
> `HABITAT_BLOB_BUCKET`), so the whole surface is in the env template and
> `ExecStart` takes no arguments, matching every other role here. `pear` also
> supports stacked YAML `--profile` files; deliberately unused, because it would
> split the config surface in two.

> [!note] `HABITAT_ORG` is undocumented
> Upstream's compose file sets it (defaulting to `true`) but it does not appear
> among the flags in `cmd/pear/flags.go`. The role does not set it. Check
> whether it still exists before relying on either behaviour.
