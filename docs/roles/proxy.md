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
committed, and the TLS certs are reissued by Caddy on demand. Nothing for the
[`backup`](backup.md) role to capture.

## TLS modes

Where the certificate comes from is a fact about what sits in front of the proxy
CT, so it is a per-cluster choice, `caddy_tls_mode`:

| Mode | Use when | What Caddy does |
| ---- | -------- | --------------- |
| `acme` | DNS points **straight at this edge** | Obtains and renews its own **Let's Encrypt** cert, one per hostname in `caddy_proxy_hosts`. No vault material. Public `:80` and `:443` must reach the proxy CT. |
| `none` | No cert yet, or an external edge terminates TLS in front | Plain HTTP on `:80` (`auto_https off`). Pair with `caddy_trusted_proxies` in the external-edge case. |

In `acme` mode `:80` redirects to `:443` (except `/healthz`), and
`caddy_tls_enabled` is true. That boolean is derived from the mode, not set.
Overriding it does nothing useful, because the template and tasks branch on the
mode.

**`acme` is the default.** A cluster that never records a mode is its own edge.
`none` is an explicit per-cluster choice, recorded with
[`scn-config`'s Set TLS entry](../README.md#cluster-tls-mode) in the
git-ignored runtime inventory alongside `cluster_domain`, or scripted:

```bash
scn-config nonint set-tls acme ops@example.org      # explicit acme, optional Let's Encrypt contact
scn-config nonint set-tls none                      # external edge in front, or pre-DNS smoke test
```

Then replay the proxy (`ansible-playbook provision.yml --limit proxy`) to apply
it; the menu offers to. The setter is the only supported way to choose the mode.
`-e caddy_tls_mode=...` is not persisted, so the next plain replay renders the
default again, and that failure is silent: the Caddyfile still validates and
deploys. The email is cleared whenever the mode leaves `acme`, or when `acme` is
recorded again without one.

A new cluster that wants to **stand the proxy up HTTP-only** and smoke-test it
before DNS and the public `:80`/`:443` forwards exist runs `scn-config nonint set-tls none`
first. Under the `acme` default, that proxy redirects to an HTTPS it has no cert
for, and Caddy keeps retrying issuance.

### `origin_ca` was removed

There used to be a third mode, `origin_ca`, for a domain that Cloudflare
proxied: Caddy served a long-lived Cloudflare Origin CA cert from the vault
(`cloudflare_origin_cert` / `cloudflare_origin_key`) and never ran ACME. It was
first inferred from the vault
([#10](https://github.com/Z-Space-Society/zai-ops/issues/10),
[#11](https://github.com/Z-Space-Society/zai-ops/pull/11)), then made an explicit
recorded choice with `acme` as the default
([#12](https://github.com/Z-Space-Society/zai-ops/issues/12)), and is now gone.
Every cluster is its own edge, so there is one cert story to run and debug, and
an Origin CA cert is trusted by Cloudflare and nobody else, which is what made
server-side calls to the cluster's own public origin need special handling.

**A cluster that still has `caddy_tls_mode: origin_ca` recorded gets a failed
proxy run**, not a changed Caddyfile. The role's first TLS assert stops before
anything on the proxy CT is touched, and names the fix. Rendering `acme` for such
a cluster would be worse: Let's Encrypt attempted from behind Cloudflare, and a
52x on every public route. `scn-config nonint show-tls` says the same thing
before a provision does.

To move such a cluster, in this order:

1. Confirm public `:80` and `:443` reach the proxy CT from anywhere, not only
   from Cloudflare's address ranges.
2. Set every DNS record that points at the cluster to **DNS only**. From this
   moment until step 3 finishes, browsers reach Caddy directly and reject the
   Origin CA cert, so have step 3 ready.
3. `scn-config nonint set-tls acme [email]`, then provision the proxy.
4. Check each hostname from outside (see [Verify](#verify)).

Do the move **before** pulling the commit that removes the mode, or from the
last checkout that still has it. The setter there can still record `origin_ca`,
which is the way back if issuance fails.

Once the cluster is settled on `acme`, two things are left over, and neither is
read by anything in this repo:

- **The cert and key on the proxy CT.** The role removes
  `/etc/caddy/cloudflare-origin.pem` and `.key` on the next proxy provision.
- **`cloudflare_origin_cert` and `cloudflare_origin_key` in the vault.** Revoke
  the certificate in the Cloudflare dashboard (SSL/TLS → Origin Server), which
  also deals with the copies in older vault backups, then delete both keys with
  `ansible-vault edit group_vars/all/vault.yml`.

## Tasks

| Task | Module | Why |
| ---- | ------ | --- |
| Add backports + pin caddy | `apt` (`python3-debian`), `deb822_repository`, `copy` (apt preferences) | Debian 13's base suite ships caddy **2.6.2**; several options this cluster needs landed in 2.7+. Backports carries 2.11.2 and is still Debian-signed, so no third-party key. The pin is scoped to `caddy` by name so backports supplies nothing else. |
| Install Caddy | `apt` (`caddy={{ caddy_apt_version }}`) | Version-pinned, like Garage and uv. Pulls the `caddy` user, `/etc/caddy/`, `/var/lib/caddy`, and `caddy.service`. **This upgrades an existing 2.6.2 install and restarts Caddy**, which is a brief outage of every public route on the LAN-facing edge. |
| Refuse the removed `origin_ca` mode | `assert` | A cluster that still has `origin_ca` recorded gets a failed run that names the fix, before anything on the CT changes. See [`origin_ca` was removed](#origin_ca-was-removed). |
| Assert the TLS mode | `assert` | The template branches on the exact mode string, so a typo would render a config for no mode, and `caddy validate` checks syntax, not intent. |
| Remove the Origin CA cert + key | `file` (`state: absent`) | Cleans up what `origin_ca` mode installed, so a cluster that moved to `acme` does not keep an unused private key on disk. A no-op everywhere else. |
| Deploy the Caddyfile | `template` (`validate: caddy validate`) | Renders `caddy_proxy_hosts`. `validate` is the `nginx -t` analog — a bad config fails the task instead of deploying. |
| Start + enable `caddy` | `systemd` | Running now + on boot. |
| Validate the deployed config | `command: caddy validate` (`changed_when: false`) | Final guard that the live file is valid. |
| Read the installed package version | `command` → `dpkg-query -W -f='${Version}' caddy` (`changed_when: false`, `check_mode: false`) | Records what this CT has. `caddy_apt_version` is what the pin should produce, not proof it did. |
| Record the manifest | `include_role: manifest` (`caddy`, `installed version`) | Last task, after the smoke test, so a service that failed it never claims a version. Writes `caddy.json` to Garage for Corliss's `/systems/`. Warns and carries on if the write fails. See [`manifest`](manifest.md). |

### Handlers

| Handler | Action |
| ------- | ------ |
| `reload caddy` | `systemctl reload caddy` — the Debian unit runs `caddy reload`, a graceful zero-downtime config swap |

## Variables

Defined in [`defaults/main.yml`](../../ansible/roles/proxy/defaults/main.yml):

| Variable | Default | Meaning |
| -------- | ------- | ------- |
| `caddy_tls_mode` | `acme` | `acme` or `none`. See [TLS modes](#tls-modes). Recorded per cluster with `scn-config` (Set TLS), never by hand. The role asserts the value is one of the two, and refuses the removed `origin_ca` with a message naming the fix. |
| `caddy_tls_enabled` | `{{ caddy_tls_mode != 'none' }}` | Derived: does this edge serve `:443`? Drives the `:80` redirect and the `https://` site addresses. Set the mode, not this. |
| `caddy_acme_email` | `""` | Let's Encrypt account contact, `acme` mode only, rendered as the global `email` option when non-empty. Not a secret. Recorded by `scn-config nonint set-tls acme <email>` and removed whenever the recorded mode leaves `acme`. Let's Encrypt no longer sends expiry reminders, so it is not a renewal alarm. |
| `caddy_backports_suite` | `{{ ansible_distribution_release }}-backports` | Derived from the CT's own release, so the blueprint stays generic. |
| `caddy_apt_version` | `2.11.2-1~bpo13+1` | Exact apt version pin. **Not** generic: `~bpo13` names Debian 13, so a base-image change means re-pinning here. It fails loudly at apt rather than installing something else. |
| `caddy_trusted_proxies` | `[]` | Addresses whose `X-Forwarded-*` headers Caddy trusts instead of overwriting. Empty renders no `servers` block at all, so a cluster where this Caddy is the outermost proxy is unaffected. See [Notes](#notes). |
| `caddy_proxy_hosts` | *(litellm)* | The routes. Each entry `{ domain, service, port }` maps a public domain to an internal service; the upstream IP is derived from that service's CTID via `hostvars[service].ansible_host` (`10.1.1.<ctid>`), never hardcoded. Ships with the live `litellm` route (`api.{{ cluster_domain }}`); the `:80` health/redirect site keeps the config sound even before a CTID is assigned. An entry may also carry `redirects: [{ from, to, code, skip_if_cookie }]` — edge-level `handle <from> { redir <to> <code> }` blocks, evaluated before the catch-all `reverse_proxy`, for cases the upstream app can't redirect itself (e.g. open-webui's `/auth*` → `/oauth/oidc/login`, since it has no native "skip the login page when OAuth is the only option"). `skip_if_cookie` names a cookie whose presence lets the request fall through to the real app instead of redirecting — see [Notes](#notes) below, it's load-bearing for open-webui, not optional. |

The committed default carries one live route, with the **domain derived from
`cluster_domain`** (set per cluster with `scn-config`, Cluster Settings) so the route holds no
this-cluster facts — the same number-free principle the inventory follows. The
role asserts `cluster_domain` is set when routes exist, failing with `run:
scn-config nonint set-domain <domain>` instead of a raw undefined-variable error.

```yaml
caddy_proxy_hosts:
  - { domain: "api.{{ cluster_domain }}", service: litellm, port: 4000 }
#  - { domain: "chat.{{ cluster_domain }}", service: open-webui, port: 8080 }
```

## Secrets

None. Neither mode reads the vault: in `acme` mode the certs and the ACME
account key live in Caddy's data directory on the CT and are reissued on a
rebuild.

## Verify

```bash
ssh root@10.1.1.<ctid> 'systemctl is-active caddy && \
  caddy validate --adapter caddyfile --config /etc/caddy/Caddyfile'
curl -s  http://10.1.1.<ctid>/healthz     # -> ok
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
[Known gotchas](../gotchas.md)).

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
  CT, which routes per service by Host header. A cluster in `acme` mode does not
  need it, because this Caddy is the outermost proxy and terminates TLS itself.
  **Leave it empty unless something else really is in front**, and list only
  that edge's address.

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
  Iterate destructively in `none` mode (`scn-config nonint set-tls none`), then switch with
  `scn-config nonint set-tls acme`.

- **open-webui's internal hairpin works under `acme`.** It resolves the public
  origin to the proxy CT and points `SSL_CERT_FILE` at the **merged** system
  bundle (see [`open-webui`](open-webui.md)), which carries the public roots a
  Let's Encrypt cert chains to. That role still installs the Cloudflare Origin
  CA roots from when Caddy served such a cert. They are unused now and are that
  role's to remove.

- **DNS-01 with a wildcard cert is the intended follow-on, and is not built.**
  It would mean one `*.example.com` + apex cert instead of one per hostname, and
  no inbound `:80` requirement at all. It is a separate piece of work because
  Caddy needs the `caddy-dns/cloudflare` module, which the Debian package does
  not carry. That means an `xcaddy` build, which breaks the "Debian-built,
  Debian-signed, version-pinned apt package" property above, plus a Cloudflare
  API token scoped to DNS edits on the zone, in the vault.

- **A route to an unassigned service is skipped, not an error.** The upstream
  address is derived from the service's CTID, so a route whose service has no
  CTID yet has nothing to render. The template leaves a comment naming the
  domain and the service in its place, and the route appears on the first
  proxy run after the service is assigned in `scn-config`. This is what lets a new service's route sit in
  the committed defaults before every cluster runs that service. The cost: a
  core service that was never assigned no longer fails the proxy play, so
  check the rendered Caddyfile for `not routed` lines if a hostname is missing.
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
