# Role: `caddy`

Installs [Caddy](https://caddyserver.com/) as a reverse proxy — a **trial second
edge running beside [`nginx-proxy-manager`](nginx-proxy-manager.md)**, so the two
can be compared in place. npm is left fully intact; nothing here replaces it.

- **Source:** [`ansible/roles/caddy/`](../../ansible/roles/caddy/)
- **Applied by:** [`provision.yml`](../../ansible/provision.yml) (configure play, `hosts: caddy`)
- **Target:** the `caddy` service CT (whatever CTID it was assigned), over SSH

## Purpose

Caddy is a single Go binary in Debian's own repos, so this role is one
`apt install` — none of npm's OpenResty + Node + yarn + SQLite source build, and
none of the Debian-13 `gpgv`/`sqv` apt workaround that build needs. Its config is
a declarative `Caddyfile` rendered from `caddy_proxy_hosts`, so **proxy routes
live in git**, not in a web-UI database. The CT therefore holds no unreproducible
state: config is committed, the TLS cert is in the vault. (Contrast npm, whose
routes are UI state in `/data` that the [`backup`](backup.md) role must capture.)

**Origin TLS, no ACME.** Caddy serves `:443` using a **Cloudflare Origin CA**
certificate (a long-lived static cert/key from the vault), with `:80` redirecting
to `:443`. That lets Cloudflare run **Full (strict)** to the origin. Caddy's
automatic-HTTPS/ACME is disabled — the Debian package ships no DNS plugin and
ACME would fail behind Cloudflare regardless.

**TLS auto-enables on the cert.** `caddy_tls_enabled` defaults to "is
`cloudflare_origin_cert` in the vault?". Until you add the cert the role stands
Caddy up **HTTP-only** (routes proxy on `:80`, `auto_https off`), so the CT can be
built and smoke-tested first; add the cert/key to the vault and re-run to flip on
`:443` + the `:80`→`:443` redirect. No code change between the two — just the vault.

## Tasks

| Task | Module | Why |
| ---- | ------ | --- |
| Install Caddy | `apt` (`state: present`) | One package; pulls the `caddy` user, `/etc/caddy/`, `/var/lib/caddy`, and `caddy.service`. No third-party repo → no signing-key/sqv friction. |
| Install Origin CA cert + key | `copy` (`content:`, `no_log`) | Cloudflare Origin CA material from the vault. Key `0600` owned by `caddy`; cert world-readable. Done before the Caddyfile so the `tls` files exist at validate time. |
| Deploy the Caddyfile | `template` (`validate: caddy validate`) | Renders `caddy_proxy_hosts`. `validate` is the `nginx -t` analog — a bad config fails the task instead of deploying. |
| Start + enable `caddy` | `systemd` | Running now + on boot. |
| Validate the deployed config | `command: caddy validate` (`changed_when: false`) | Final guard that the live file is valid. |

### Handlers

| Handler | Action |
| ------- | ------ |
| `reload caddy` | `systemctl reload caddy` — the Debian unit runs `caddy reload`, a graceful zero-downtime config swap |

## Variables

Defined in [`defaults/main.yml`](../../ansible/roles/caddy/defaults/main.yml):

| Variable | Default | Meaning |
| -------- | ------- | ------- |
| `caddy_cert_path` | `/etc/caddy/cloudflare-origin.pem` | Where the Origin CA cert lands; the `tls` directive points here. |
| `caddy_key_path` | `/etc/caddy/cloudflare-origin.key` | Where the Origin CA private key lands (`0600`, owned by `caddy`). |
| `caddy_tls_enabled` | `{{ cloudflare_origin_cert is defined }}` | Auto: serve HTTPS when the Origin CA cert is in the vault, else HTTP-only. Override to force either way. |
| `caddy_proxy_hosts` | `[]` | The routes. Each entry `{ domain, service, port }` maps a public domain to an internal service; the upstream IP is derived from that service's CTID via `hostvars[service].ansible_host` (`10.1.1.<ctid>`), never hardcoded. Empty is valid — the `:80` health/redirect site keeps the config sound. |

Example:

```yaml
caddy_proxy_hosts:
  - { domain: chat.example.com, service: open-webui, port: 8080 }
  - { domain: api.example.com,  service: litellm,    port: 4000 }
```

## Secrets (one manual step)

The role reads `cloudflare_origin_cert` and `cloudflare_origin_key` from the
vault ([`group_vars/all/vault.yml`](../../ansible/group_vars/all/vault.yml)).
Generate a (wildcard `*.example.com`) cert once in the Cloudflare dashboard
(SSL/TLS → Origin Server → Create Certificate), paste cert + key into the vault,
then set the Cloudflare SSL mode to **Full (strict)**. The vault is already in
the [`backup`](backup.md) role's `backup_paths`, so no backup change is needed.

## Verify

```bash
ssh root@10.1.1.<ctid> 'systemctl is-active caddy && \
  caddy validate --adapter caddyfile --config /etc/caddy/Caddyfile'
curl -s  http://10.1.1.<ctid>/healthz     # -> ok
curl -kI https://10.1.1.<ctid>/           # TLS served by the origin cert
# after adding a real entry to caddy_proxy_hosts and re-running:
curl -kH 'Host: chat.example.com' https://10.1.1.<ctid>/
```

## Notes

- **Trial, not a replacement.** This stands beside npm so Caddy can be evaluated
  on the real cluster. If it wins, retiring npm and renaming to the generic
  `proxy` / `reverse-proxy` (per the repo's name-by-function convention) is a
  separate follow-up.
- **Empty `caddy_proxy_hosts` is safe** — the `:80` site (health probe + HTTPS
  redirect) keeps the Caddyfile valid before any upstream is assigned, mirroring
  the inventory's placeholder pattern.
- **One public hostname per proxy at a time.** Both npm and caddy are LAN-facing;
  point a given hostname at only one of them (controlled at Cloudflare) when
  comparing.
- For how the CT is assigned a CTID, created and reached, see the
  [main docs](../README.md#networking) and [`provision.yml`](../../ansible/provision.yml).
