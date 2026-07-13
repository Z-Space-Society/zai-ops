# Role: `proxy`

Installs [Caddy](https://caddyserver.com/) as the cluster's reverse-proxy **edge**
— the one LAN-facing service, fronting every internal upstream. (The role is named
by function, `proxy`; Caddy is the implementation, the same way `object_store`
runs Garage.)

- **Source:** [`ansible/roles/proxy/`](../../ansible/roles/proxy/)
- **Applied by:** [`provision.yml`](../../ansible/provision.yml) (configure play, `hosts: proxy`)
- **Target:** the `proxy` service CT (whatever CTID it was assigned), over SSH

## Purpose

Caddy is a single Go binary in Debian's own repos, so this role is one
`apt install` — no third-party repo, and none of the Debian-13 `gpgv`/`sqv` apt
workaround a third-party repo would need. Its config is a declarative `Caddyfile`
rendered from `caddy_proxy_hosts`, so **proxy routes live in git**, not in a
web-UI database. The CT therefore holds no unreproducible state: config is
committed, the TLS cert is in the vault — nothing for the [`backup`](backup.md)
role to capture.

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

Defined in [`defaults/main.yml`](../../ansible/roles/proxy/defaults/main.yml):

| Variable | Default | Meaning |
| -------- | ------- | ------- |
| `caddy_cert_path` | `/etc/caddy/cloudflare-origin.pem` | Where the Origin CA cert lands; the `tls` directive points here. |
| `caddy_key_path` | `/etc/caddy/cloudflare-origin.key` | Where the Origin CA private key lands (`0600`, owned by `caddy`). |
| `caddy_tls_enabled` | `{{ cloudflare_origin_cert is defined }}` | Auto: serve HTTPS when the Origin CA cert is in the vault, else HTTP-only. Override to force either way. |
| `caddy_proxy_hosts` | *(litellm)* | The routes. Each entry `{ domain, service, port }` maps a public domain to an internal service; the upstream IP is derived from that service's CTID via `hostvars[service].ansible_host` (`10.1.1.<ctid>`), never hardcoded. Ships with the live `litellm` route (`api.{{ cluster_domain }}`); the `:80` health/redirect site keeps the config sound even before a CTID is assigned. An entry may also carry `redirects: [{ from, to, code, skip_if_cookie }]` — edge-level `handle <from> { redir <to> <code> }` blocks, evaluated before the catch-all `reverse_proxy`, for cases the upstream app can't redirect itself (e.g. open-webui's `/auth*` → `/oauth/oidc/login`, since it has no native "skip the login page when OAuth is the only option"). `skip_if_cookie` names a cookie whose presence lets the request fall through to the real app instead of redirecting — see [Notes](#notes) below, it's load-bearing for open-webui, not optional. |

The committed default carries one live route, with the **domain derived from
`cluster_domain`** (set per cluster with `zai-set-domain`) so the route holds no
this-cluster facts — the same number-free principle the inventory follows. The
role asserts `cluster_domain` is set when routes exist, failing with `run:
zai-set-domain <domain>` instead of a raw undefined-variable error.

```yaml
caddy_proxy_hosts:
  - { domain: "api.{{ cluster_domain }}", service: litellm, port: 4000 }
#  - { domain: "chat.{{ cluster_domain }}", service: open-webui, port: 8080 }
```

## Secrets (one manual step)

The role reads `cloudflare_origin_cert` and `cloudflare_origin_key` from the
vault ([`group_vars/all/vault.yml`](../../ansible/group_vars/all/vault.yml)).
Generate a (wildcard `*.example.com`) cert once in the Cloudflare dashboard
(SSL/TLS → Origin Server → Create Certificate), paste cert + key into the vault,
then set the Cloudflare SSL mode to **Full (strict)**. The vault is already in
the [`backup`](backup.md) job's `backup_paths` (in `bin/zai-backup`), so no
backup change is needed.

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

- **Empty `caddy_proxy_hosts` is safe** — the `:80` site (health probe + HTTPS
  redirect) keeps the Caddyfile valid before any upstream is assigned, mirroring
  the inventory's placeholder pattern.
- **The edge sits at tier 110.** Per the [CTID tier
  convention](../README.md#networking) the proxy is the first platform-tier CT —
  one step out from the core data foundations, the only box on the LAN.
- **Per-route `redirects` use `handle` blocks, not a bare `redir` directive.**
  A site block mixing a top-level `redir`/`reverse_proxy` with `handle` blocks
  is order-ambiguous (Caddy sorts un-wrapped directives by an internal
  priority, not source order); wrapping *everything* in mutually-exclusive
  `handle` blocks — redirects first, a catch-all `handle { reverse_proxy … }`
  last — is unambiguous and matches the `:80` site's own health-check/redirect
  pattern above it in the same file. Same rule applies one level deeper for
  `skip_if_cookie`: the cookie check and the redirect are both wrapped in
  their own `handle` blocks (not a `handle` plus a bare sibling `redir`) —
  a directive that isn't itself a `handle` block doesn't share in that
  mutual-exclusion guarantee, so it would fire unconditionally alongside
  whichever `handle` block matched.
- **A blind `/auth*` redirect breaks open-webui's own OIDC login completion —
  `skip_if_cookie: token` is required, not decorative.** open-webui's OIDC
  callback always finishes by redirecting the browser *back* to `/auth` (its
  frontend reads the just-set `token` session cookie there and completes
  login client-side) — an edge redirect that intercepts every `/auth*`
  request unconditionally also catches that completion request and bounces
  it into a fresh OIDC round-trip, forever. The symptom is a browser redirect
  loop entirely on the app's own domain (never reaching the identity
  provider) while the app's own log shows a successful token exchange on
  every single cycle — easy to misdiagnose as an OIDC config problem when
  it's actually the edge redirect fighting the app's own completion
  mechanism. Caddy has no dedicated `cookie` matcher; `skip_if_cookie` is
  implemented as a `header_regexp` check against the raw `Cookie` header,
  anchored on a header boundary (`(^|;\s*)<name>=`) so it can't false-match a
  differently-named cookie that merely contains the same substring.
- For how the CT is assigned a CTID, created and reached, see the
  [main docs](../README.md#networking) and [`provision.yml`](../../ansible/provision.yml).
