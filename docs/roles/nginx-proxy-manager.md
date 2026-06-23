# Role: `nginx-proxy-manager`

Installs [Nginx Proxy Manager](https://nginxproxymanager.com/) (NPM) — a web-UI
reverse proxy — **natively, without Docker**, as the cluster's LAN-facing edge.

- **Source:** [`ansible/roles/nginx-proxy-manager/`](../../ansible/roles/nginx-proxy-manager/)
- **Applied by:** [`provision.yml`](../../ansible/provision.yml) (configure play, `hosts: npm`)
- **Target:** the `npm` service CT (whatever CTID it was assigned), over SSH

## Purpose

NPM is the only LAN-facing container. It fronts the internal services (litellm,
open-webui) and is managed day-to-day through its **web UI on port 81**
(internal-only). Proxy hosts, redirects and any uploaded certs live in NPM's
SQLite DB under `/data` — that's **runtime state captured by the [`backup`](backup.md)
role**, not configuration in git.

**No Docker, by design** (CLAUDE.md pinned decision). NPM ships only as a container
image, so this role assembles its parts under systemd: OpenResty (NPM's nginx),
a Node 18 backend, a built Vue frontend, and SQLite. The vendored, multi-step
build lives in one idempotent script (`npm-build.sh`, like `object_store`'s
`garage-init.sh`) because it's inherently shell-shaped; Ansible owns the repos,
directories, config, unit and service state around it.

**No ACME / Let's Encrypt.** Public TLS terminates at Cloudflare in front of the
cluster, so `certbot` is intentionally omitted — NPM serves plain HTTP on `:80`.
(If Cloudflare "Full (strict)" origin TLS is later wanted, upload a Cloudflare
**Origin CA** cert in the UI — still no ACME, no renewals.)

## Tasks

| Task | Module | Why |
| ---- | ------ | --- |
| Install base deps | `apt` | curl, git, build-essential, python3, sqlite3, rsync, etc. |
| Add OpenResty key + repo | `get_url` + `apt_repository` | No apt package for OpenResty in Debian; signed-by `.asc` keyring. (Node comes from Debian.) |
| Force apt to verify with `gpgv` | `copy` (apt.conf.d drop-in) | Debian 13's Sequoia `sqv` rejects OpenResty's SHA1-bound key; gpgv still verifies it. See Notes. |
| Install base deps, Node.js + OpenResty | `apt` (`state: present`) | Build/runtime deps, Debian's `nodejs` + `npm`, and NPM's nginx (OpenResty). |
| Install yarn | `command` (`creates:`) | NPM's package manager (classic yarn), version-pinned. Both workspaces lock with `yarn.lock`. |
| Create the `/data` tree + scratch dirs | `file` | Runtime state + nginx/cache scratch NPM expects at start. |
| Render `production.json` | `template` | Points the backend at SQLite (`/data/database.sqlite`). |
| Build + stage NPM | `template` + `command` | `npm-build.sh` fetches the pinned release, builds the frontend, stages the backend into `/app`, wires the paths NPM hardcodes. Version-aware → idempotent. |
| Install the `npm` systemd unit | `template` | Supervises the Node backend (`node /app/index.js`). |
| Install logrotate policy | `template` | Rotate `/data/logs`. |
| Start + enable `openresty`, `npm` | `service` | Running now + on boot. |
| Validate config | `command: nginx -t` (`changed_when: false`) | Fail the run on a bad assembled config. |

### Handlers

| Handler | Action |
| ------- | ------ |
| `reload systemd` | `systemctl daemon-reload` after a unit change |
| `restart npm` | Restart the backend (it regenerates + reloads OpenResty's per-host config) |
| `restart openresty` | Restart OpenResty when its own base config changes |

## Variables

Defined in [`defaults/main.yml`](../../ansible/roles/nginx-proxy-manager/defaults/main.yml):

| Variable | Default | Meaning |
| -------- | ------- | ------- |
| `npm_version` | `2.15.1` | Pinned NPM release tag (no leading `v`). Never `latest` — the build is reproducible only against a fixed tree. Bump deliberately. |
| `npm_yarn_version` | `1.22.22` | Classic yarn — both workspaces lock with `yarn.lock`. (Node is Debian's nodejs 20.19.x — no var.) |
| `npm_app_dir` | `/app` | Backend install dir (NPM hardcodes this). |
| `npm_data_dir` | `/data` | Runtime state (SQLite DB, per-host nginx config, certs) — the backup target. |
| `npm_src_dir` | `/opt/nginx-proxy-manager` | Build scratch + the `.installed_version` marker. |

## First run & UI

On first boot NPM seeds a default admin `admin@example.com` / `changeme` and forces
a password change. Complete admin setup in the UI on **`:81`** (internal-only — the
default creds are never exposed to the LAN). The admin account then lives in `/data`
and is backed up. Add proxy hosts in the UI pointing at each upstream's derived IP
(`10.1.1.<ctid>`, from `zai-assign` / `local.yml`), forward scheme **http**, no SSL.

## Backup of `/data`

NPM's proxy-host config is **not in git** — it's UI state in `/data`. The
[`backup`](backup.md) role's Tier-2 wiring captures it: set
`backup_npm_enabled: true` once the CT is up, and the control-node backup `rsync`s
`/data` into the restic repo. See that doc for the restore step.

## Verify

```bash
ssh root@10.1.1.<ctid> 'systemctl is-active openresty npm && /usr/sbin/nginx -t'
curl -I http://10.1.1.<ctid>:81/                  # NPM admin UI responds
# after creating a proxy host in the UI:
curl -H 'Host: chat.example.com' http://10.1.1.<ctid>/
```

## Notes

- **OpenResty apt suite is pinned to `bookworm`** (`npm_openresty_suite`).
  OpenResty has no Debian 13/trixie repo yet ([openresty#1054](https://github.com/openresty/openresty/issues/1054)),
  so the live codename can't be used. bookworm installs fine on trixie because
  OpenResty bundles its own PCRE/OpenSSL (`openresty-pcre`/`openresty-openssl3`)
  and doesn't pull the EOL `libpcre3` that trixie removed. Bump the var to `trixie`
  once upstream publishes it.
- **Node is Debian's `nodejs` + `npm`, not NodeSource.** trixie ships nodejs
  20.19.x, which is new enough to build the frontend (vite 8 needs ≥ 20.19) and run
  the backend, so NodeSource buys nothing — it only ever made sense for an older
  major. We install Debian's `nodejs` *and* `npm` (Debian splits npm into its own
  package; we need it to install yarn globally). This dropped a whole class of
  fragility: NodeSource pins a *lower* node major than trixie ships, so an earlier
  NodeSource-based version of this role had to fight apt with a priority-1001 pin
  and a downgrade-reconcile task just to win — all of which is gone now.
- **apt is pinned to the `gpgv` verifier on this CT** (`/etc/apt/apt.conf.d/99-zai-gpgv`).
  Debian 13's apt verifies signatures with Sequoia (`sqv`), whose policy rejects
  SHA1 key self-signatures from **2026-02-01**. OpenResty's signing key is SHA1-bound,
  so sqv reports the (correctly signed) repo as "not signed" and the apt update fails
  with `Sub-process /usr/bin/sqv returned an error code`. `gpgv` still verifies the
  signature against the pinned key but accepts SHA1 self-sigs — authenticity is kept,
  not disabled. This is independent of the bookworm suite pin (same key either way).
  **`gpgv` is installed first, as its own task,** because Debian 13 ships none by
  default (sqv replaced it); without that, the override breaks *every* repo —
  including the Debian repos needed to install gpgv (`Cannot find gpgv`). The
  install runs before the override and before the OpenResty repo, so it bootstraps
  under sqv against Debian's own (modern-key) repos. Task order is load-bearing.
- NPM without Docker is **unsupported upstream**; `npm-build.sh` follows the
  community dockerless assembly. Treat a `npm_version` bump as a change to test on
  a real CT, not a no-op.
- **Build with yarn `--frozen-lockfile`, not pnpm/npm.** Both NPM workspaces
  (`frontend/`, `backend/`) lock with classic `yarn.lock` and ship no
  `pnpm-lock.yaml`. pnpm/npm ignore yarn.lock and float every caret range to the
  newest release — an untested tree (e.g. vite 8 / typescript 6 / `@formatjs/cli`
  6.16.11) that breaks the build. yarn `--frozen-lockfile` reproduces the exact
  tested versions; the backend adds `--production` (no build step, runs directly
  under node).
- **Node 20 is required to build, not just run.** The frontend toolchain is
  modern — `vite ^8.0.14` needs Node ≥ 20.19, and `@formatjs/cli`'s ESM binary
  won't run on Node 18 (`Cannot use import statement outside a module`). Node 20
  also still ships `--openssl-legacy-provider` (removed in 22), which the backend's
  runtime sets (see the systemd unit). The backend declares no `engines`, so one
  Node 20 install both builds the frontend and runs the backend.
- **The frontend locale bundles are generated, not committed.** `npm-build.sh`
  runs `yarn run locale-compile` (formatjs: `src/locale/src/` → `src/locale/lang/`)
  before `yarn run build`, because the source tarball ships only the message
  sources. Without it `tsc` fails with `Cannot find module './lang/en.json'`.
  Upstream runs this as a separate CI step that `yarn build` doesn't trigger.
- **The backend needs `--openssl-legacy-provider` at runtime.** The systemd unit
  sets `NODE_OPTIONS=--openssl-legacy-provider` to match upstream's runtime image;
  without it the Node backend fails on legacy crypto.
- Resources (2 cores / 2 GiB / 8 GB) match the proven Proxmox community-script NPM
  LXC; the build script cleans its scratch tree so 8 GB holds.
- For how the CT is assigned a CTID, created and reached, see the
  [main docs](../README.md#networking) and [`provision.yml`](../../ansible/provision.yml).
