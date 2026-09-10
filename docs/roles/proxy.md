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
committed, and the TLS cert is either in the vault or reissued by Caddy on
demand. Nothing for the [`backup`](backup.md) role to capture.

## TLS modes

Where the certificate comes from is a fact about what sits in front of the proxy
CT, so it is a per-cluster choice, `caddy_tls_mode`:

| Mode | Use when | What Caddy does |
| ---- | -------- | --------------- |
| `origin_ca` | Cloudflare **proxies** the domain (orange cloud) | Serves `:443` with the long-lived **Cloudflare Origin CA** cert/key from the vault, so Cloudflare can run **Full (strict)** to the origin. ACME never runs. The cert is trusted by Cloudflare and nobody else. |
| `acme` | DNS points **straight at this edge** (grey cloud, or no Cloudflare) | Obtains and renews its own **Let's Encrypt** cert, one per hostname in `caddy_proxy_hosts`, over HTTP-01. No vault material. Public `:80` must reach the proxy CT. |
| `none` | No cert yet, or an external edge terminates TLS in front | Plain HTTP on `:80` (`auto_https off`). Pair with `caddy_trusted_proxies` in the external-edge case. |

In both cert-bearing modes `:80` redirects to `:443` (except `/healthz`), and
`caddy_tls_enabled` is true. That boolean is now derived from the mode, not set.

**The default reproduces the old behaviour exactly.** Unset, `caddy_tls_mode` is
`origin_ca` if `cloudflare_origin_cert` is in the vault and `none` otherwise.
So a Cloudflare-fronted cluster still stands up HTTP-only for smoke-testing, and
flips to `:443` when the cert/key land in the vault and the role re-runs, with no
code change. `acme` is never picked automatically. Set it per cluster, in the
git-ignored runtime inventory alongside `cluster_domain` (it is a this-cluster
fact, so it does not belong in committed `group_vars`):

```yaml
# inventory/local.yml, under the proxy host or a group it belongs to
caddy_tls_mode: acme
caddy_acme_email: ops@example.org
```

A cluster that wants **HTTP-only while a vault cert exists** now sets
`caddy_tls_mode: none`. Overriding `caddy_tls_enabled` no longer does anything
useful, because the template and tasks branch on the mode.

## Tasks

| Task | Module | Why |
| ---- | ------ | --- |
| Add backports + pin caddy | `apt` (`python3-debian`), `deb822_repository`, `copy` (apt preferences) | Debian 13's base suite ships caddy **2.6.2**; several options this cluster needs landed in 2.7+. Backports carries 2.11.2 and is still Debian-signed, so no third-party key. The pin is scoped to `caddy` by name so backports supplies nothing else. |
| Install Caddy | `apt` (`caddy={{ caddy_apt_version }}`) | Version-pinned, like Garage and uv. Pulls the `caddy` user, `/etc/caddy/`, `/var/lib/caddy`, and `caddy.service`. **This upgrades an existing 2.6.2 install and restarts Caddy**, which is a brief outage of every public route on the LAN-facing edge. |
| Assert the TLS mode | `assert` | The template branches on the exact mode string, so a typo would render a config for no mode, and `caddy validate` checks syntax, not intent. Also catches a forced `origin_ca` with no vault cert, which would otherwise fail inside a `no_log` task that hides the error. |
| Install Origin CA cert + key | `copy` (`content:`, `no_log`) | `origin_ca` mode only. Gated on the mode, not `caddy_tls_enabled`, because `acme` serves TLS with no vault material. Key `0600` owned by `caddy`; cert world-readable. Done before the Caddyfile so the `tls` files exist at validate time. |
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
| `caddy_tls_mode` | `{{ 'origin_ca' if cloudflare_origin_cert is defined else 'none' }}` | `origin_ca`, `acme` or `none`. See [TLS modes](#tls-modes). The default is the pre-mode behaviour; set `acme` explicitly per cluster. The role asserts the value is one of the three, and that `origin_ca` has its vault cert/key. |
| `caddy_tls_enabled` | `{{ caddy_tls_mode != 'none' }}` | Derived: does this edge serve `:443`? Drives the `:80` redirect and the `https://` site addresses. Set the mode, not this. |
| `caddy_acme_email` | `""` | Let's Encrypt account contact, `acme` mode only, rendered as the global `email` option when non-empty. Not a secret. Let's Encrypt no longer sends expiry reminders, so it is not a renewal alarm. |
| `caddy_cert_path` | `/etc/caddy/cloudflare-origin.pem` | `origin_ca` only. Where the Origin CA cert lands; the `tls` directive points here. |
| `caddy_key_path` | `/etc/caddy/cloudflare-origin.key` | `origin_ca` only. Where the Origin CA private key lands (`0600`, owned by `caddy`). |
| `caddy_backports_suite` | `{{ ansible_distribution_release }}-backports` | Derived from the CT's own release, so the blueprint stays generic. |
| `caddy_apt_version` | `2.11.2-1~bpo13+1` | Exact apt version pin. **Not** generic: `~bpo13` names Debian 13, so a base-image change means re-pinning here. It fails loudly at apt rather than installing something else. |
| `caddy_trusted_proxies` | `[]` | Addresses whose `X-Forwarded-*` headers Caddy trusts instead of overwriting. Empty renders no `servers` block at all, so a cluster where this Caddy is the outermost proxy is unaffected. See [Notes](#notes). |
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

`acme` and `none` read no secrets. In `origin_ca` mode the role reads
`cloudflare_origin_cert` and `cloudflare_origin_key` from the vault
(`ansible/group_vars/all/vault.yml`, git-ignored, so it exists only on the
control node).
Generate a cert once in the Cloudflare dashboard (SSL/TLS → Origin Server →
Create Certificate), paste cert + key into the vault, then set the Cloudflare
SSL mode to **Full (strict)**. Cover **both the apex and the wildcard**
(`example.com, *.example.com` — Cloudflare's default pair): a wildcard-only
cert does not match the bare domain, and [`corliss`](corliss.md) is served
there, so a wildcard-only cert makes the apex answer 526. Check an existing
cert with:

```bash
echo | openssl s_client -connect 10.1.1.<proxy-ctid>:443 2>/dev/null \
  | openssl x509 -noout -ext subjectAltName
``` The vault is already in
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

In `acme` mode the point is a chain browsers trust, so verify by hostname from
**outside** the LAN, and never with `-k`:

```bash
curl -sI https://chat.example.com/ | head -1       # must succeed without -k
echo | openssl s_client -connect chat.example.com:443 -servername chat.example.com \
  2>/dev/null | openssl x509 -noout -issuer -dates   # issuer: Let's Encrypt
ssh root@10.1.1.<ctid> 'journalctl -u caddy --no-pager | grep -iE "obtain|challenge|acme"'
```

If issuance fails, check public `:80` first (see
[Known gotchas](../README.md#known-gotchas)).

## Notes

- **`caddy_trusted_proxies` is only for clusters behind another proxy.** Caddy
  sets `X-Forwarded-*` from the connection it received and, by default, ignores
  what the client sent. That is correct for a directly-exposed edge: it stops a
  client forging `X-Forwarded-Proto: https`. It is wrong when a legitimate proxy
  sits in front, because that proxy's headers are the truthful ones and get
  overwritten with this connection's scheme, which is `http`. Apps then build
  `http://` absolute URLs for a client that arrived over HTTPS, breaking Django
  redirects and OIDC callbacks.

  The motivating topology is the home lab: a Caddy on a NAS holds the only public
  443, terminates TLS, and reverse-proxies plain HTTP over the LAN to the proxy
  CT, which routes per service by Host header. Production at Z-Space does not need
  it, because Cloudflare is the edge and this Caddy terminates the origin TLS
  itself. **Leave it empty unless something else really is in front**, and list
  only that edge's address.

- **Caddy is pinned to a backports version, and the first replay upgrades it.**
  Going from 2.6.2 to 2.11.2 restarts Caddy on the only LAN-facing CT. The
  Caddyfile is validated with the new binary before deployment and again after,
  so an incompatibility fails the play rather than leaving the edge down, but the
  restart itself is unavoidable. Do it deliberately rather than as a side effect
  of an unrelated replay, and do it on a staging cluster first.

  Caddy's official Cloudsmith repo was the alternative and was rejected: it would
  add a third-party trust root to obtain a capability backports already provides.
  If the goal ever becomes tracking upstream Caddy generally rather than clearing
  a version floor, that trade changes.

- **`acme` needs public `:80`, and that is not this role's to provide.** HTTP-01
  means Let's Encrypt connects to each hostname on port 80 from the internet, so
  the forward from the public address to the proxy CT is the first thing to
  check when issuance fails. The role cannot see or assert it. The same goes
  for the other direction: the proxy CT needs outbound HTTPS to Let's Encrypt's
  API to place the order at all. Caddy also tries
  TLS-ALPN-01 over `:443` by default, so a broken `:80` forward may not stop
  issuance outright. Don't rely on that: the `:80` redirect needs the port
  regardless, and a cluster that half-works is harder to debug than one that
  fails.

- **`acme` keeps cert state on the CT, and rebuilds spend Let's Encrypt quota.**
  The certs and the ACME account key live in Caddy's data directory
  (`/var/lib/caddy/.local/share/caddy`), not in git or the vault. That is still
  reproducible, since a rebuilt CT simply issues again, so it stays out of
  [`backup`](backup.md). The cost is rate limits: Let's Encrypt issues at most 5
  certs for the same exact set of hostnames per 7 days. Rebuilding a staging
  proxy CT repeatedly in one week can lock out issuance until the window rolls.
  Iterate destructively in `none` mode, then switch.

- **open-webui's internal hairpin works in every mode.** It resolves the public
  origin to the proxy CT and installs the Origin CA roots, but it points
  `SSL_CERT_FILE` at the **merged** system bundle (see
  [`open-webui`](open-webui.md)). That bundle also carries the public roots a
  Let's Encrypt cert chains to, so under `acme` the extra roots are just unused.

- **DNS-01 with a wildcard cert is the intended follow-on, and is not built.**
  It would mean one `*.example.com` + apex cert instead of one per hostname, and
  no inbound `:80` requirement at all. It is a separate piece of work because
  Caddy needs the `caddy-dns/cloudflare` module, which the Debian package does
  not carry. That means an `xcaddy` build, which breaks the "Debian-built,
  Debian-signed, version-pinned apt package" property above, plus a Cloudflare
  API token scoped to DNS edits on the zone, in the vault.

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
