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
| Add NodeSource + OpenResty keys/repos | `get_url` + `apt_repository` | No apt package for Node 18 / OpenResty in Debian; signed-by `.asc` keyrings. |
| Install Node.js + OpenResty | `apt` | The runtime (Node) and NPM's nginx (OpenResty). |
| Install pnpm | `command` (`creates:`) | NPM's package manager, version-pinned. |
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
| `npm_node_major` | `18` | Node major from NodeSource. |
| `npm_pnpm_version` | `8.15` | pnpm version. |
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
- NPM without Docker is **unsupported upstream**; `npm-build.sh` follows the
  community dockerless assembly. Treat a `npm_version` bump as a change to test on
  a real CT, not a no-op.
- Resources (2 cores / 2 GiB / 8 GB) match the proven Proxmox community-script NPM
  LXC; the build script cleans its scratch tree so 8 GB holds.
- For how the CT is assigned a CTID, created and reached, see the
  [main docs](../README.md#networking) and [`provision.yml`](../../ansible/provision.yml).
