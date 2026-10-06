# Known gotchas

Problems that were debugged the hard way, kept so they are not debugged twice.
Part of the [zai-ops docs](README.md). Add a new one here in the same change
that fixes it.

Hard-won lessons with `community.proxmox.proxmox`. The collection is pinned to
**>=1.6.0** in [`ansible/requirements.yml`](../ansible/requirements.yml), seeded
by `bootstrap.sh` and re-asserted by the `control_node` role, *not* the 1.3.0
bundled with Debian 13's `ansible` 12 (see the timeout lesson). These will recur
on the remaining service CTs:

- **Never pin the container template's point release.** Proxmox's `pveam` index
  only carries the current build of each template, so the day
  `debian-13-standard_13.6-1` shipped, the pinned `13.1-2` disappeared and a
  fresh `bootstrap.sh` died at step 6 with `400 Parameter verification failed.
  template: no such template`. Both consumers now resolve the newest
  `debian-13-standard_*_amd64.tar.zst` by pattern: `bootstrap.sh` against
  `local` first and the `pveam` index only if `local` has none, and
  `provision.yml` against `local` over the API
  (`proxmox_storage_contents_info`). A host that already holds an older build
  keeps using it; nothing forces a newer image under existing CTs.

- **`[WARNING]: GetObjectTagging is not implemented by your storage provider`
  is expected on manifest writes.** `amazon.aws.s3_object` reads an object's
  tags after every upload, with no option to skip it, and Garage has no object
  tagging. The module warns and carries on; the write has already succeeded.
  Ansible prints the identical warning once per run, however many services
  write. Leave it: silencing it means turning off Ansible warnings globally. See
  [`manifest`](roles/manifest.md).

- **Cloudflare 403s server-side Python fetches of our own public endpoints.**
  Browser Integrity Check is on by default for the zone and refuses known
  non-browser User-Agents with `error code: 1010` — `Python-urllib/3.x` among
  them. It is UA-based only: `curl`, a browser and `httpx` all get 200 from the
  same URL at the same moment. This is nasty precisely because it splits a
  library ecosystem in half — OIDC **login** worked fine (authlib uses `httpx`)
  while back-channel **logout** silently failed forever (PyJWT's `PyJWKClient`
  uses bare `urllib` to fetch the JWKS, so validation 403'd and the endpoint
  returned 400). Symptom to recognise: an inter-service call that works from the
  shell but not from the app, or vice versa. Any service-to-service call must
  resolve the public origin **internally** rather than traverse the edge — see
  [`open-webui`](roles/open-webui.md#the-public-origin-is-resolved-internally)
  for the pattern (`/etc/hosts` → proxy CT, Origin CA roots in the trust store,
  `SSL_CERT_FILE` at the merged bundle). The **unfixable** cousin: our atproto
  `client_id` document `/auth/client-metadata.json` is fetched server-side by
  each member's PDS. Bluesky's is Go/TS and passes today, but a Python-based PDS
  or authorization server would be refused, and no change on our side reaches
  that client — it needs a Cloudflare rule, or no Cloudflare.

- **A static Caddy route serves `index.html` for files that don't exist.** The
  SPA fallback such a route needs (`try_files {path} /index.html`) is
  indiscriminate: a file the build *failed to emit* is served as HTML rather
  than 404ing. The concrete case was `client-metadata.json` — with
  `VITE_OAUTH_CLIENT_ID` unset, scn-member-registry's `prebuild` hook printed a
  note and **exited 0**, so the build succeeded, the file was absent, the
  member's PDS fetched HTML where it expected JSON, and sign-in died at the
  consent screen with nothing in any log pointing at the cause. Assert the file
  exists after any static deploy; don't wait for a 404 that will never come.
  Kept after the `manage_console` role was deleted because the `root:` branch in
  [`Caddyfile.j2`](../ansible/roles/proxy/templates/Caddyfile.j2) survives it,
  so the next `root:` route inherits the same trap.

- **Renumbering a CT breaks CT 100's `known_hosts`, and the error accuses you of
  a MITM.** Addresses derive from the CTID, so reassigning numbers recycles IPs
  between services — `10.1.1.120` can be Open WebUI in the morning and Corliss
  in the afternoon, with a different host key. Ansible then fails
  `UNREACHABLE!` with `REMOTE HOST IDENTIFICATION HAS CHANGED!`, which reads
  alarming and is simply true: it *is* a different machine. Clear the stale
  entry for **every IP whose occupant changed**, not just the one that failed,
  or the run fails again on the next host:

  ```bash
  ssh-keygen -f /root/.ssh/known_hosts -R 10.1.1.<ctid>
  ```

  Do **not** reach for `host_key_checking = False` in `ansible.cfg` — that
  trades a once-per-renumber annoyance for permanently unverified SSH to every
  CT. `StrictHostKeyChecking=accept-new` does not help either: it accepts keys
  for *unknown* hosts, and these hosts are known with the wrong key.

- **Downloads over the host NAT drop intermittently.** Release fetches from
  GitHub's CDN fail with `Remote end closed connection without response` on an
  otherwise healthy link — the same flakiness `corliss_uv_http_timeout: 180`
  exists to absorb. Any `get_url` for a release artifact wants
  `retries`/`until`; a checksum makes the retry safe, because a truncated or
  substituted file still fails hard instead of being papered over.

- **Create needs `api_timeout`, not just `timeout`.** Bundled 1.3.0 calls
  `ProxmoxAPI()` without a connection timeout, so proxmoxer falls back to a 5s
  read timeout that the LXC-create POST exceeds on a fresh node
  (`Read timed out. (read timeout=5)`). The module's `timeout:` only bounds its
  task-wait loop, *not* the HTTP read, so raising it changes nothing. `api_timeout`
  (added in 1.6.0 — hence the pin) is the read timeout; both are set in
  [`provision.yml`](../ansible/provision.yml)'s `module_defaults`. Symptom: the
  "Create the LXC container" task fails in ~5s with `read timeout=5` and no CT is
  left behind.
- **Disk must use the `storage:size` form.** Use `disk: "local-lvm:8"`, *not*
  `disk: 8` with a separate `storage:` — the latter renders a pathless rootfs
  that PVE rejects under token auth ("Only root can pass arbitrary filesystem
  paths").
- **Start tasks need `hostname`.** `state: started` with only `vmid` hits a
  KeyError `'name'` on freshly created CTs ([community.proxmox #98]). Pass
  `hostname:` on the start task; a small `retries`/`until` covers the race.
- **Systemd 257 wants nesting.** Unprivileged CTs warn "you may need to enable
  nesting"; service CTs are created with `features: [nesting=1]`.
- **HTTP 595 on create = wrong `node:`, not a network problem.** `595 Errors
  during connection establishment, proxy handshake: Connection timed out` is a
  Proxmox status: `pveproxy` accepted the request but failed trying to *proxy* it
  to the node named in the call — because that node isn't this host. The cause is
  a stale/wrong `proxmox_node_name`. It's recorded as runtime data from the host's
  `hostname` (`bootstrap.sh` / `zai-set-node`); fix it with
  `zai-set-node <node>`. With too short an `api_timeout` the same root cause
  instead surfaces as a misleading `read timeout=5` (proxmoxer gives up before
  pveproxy returns the 595).

[community.proxmox #98]: https://github.com/ansible-collections/community.proxmox/issues/98

On the **control node** itself:

- **`pct enter` is a non-login shell, so `/etc/profile.d` never loads.** Putting
  the repo's `bin/` on PATH via a `/etc/profile.d/zai-ops.sh` snippet alone left
  `scn-config` "command not found" inside `pct enter 100` — that shell is
  interactive but *non-login*, and only login shells (and ssh) source
  `/etc/profile.d`. Fix: also source the snippet from `/etc/bash.bashrc`, which
  Debian's interactive *non-login* bash does read. Both the bootstrap seed and the
  `control_node` role install both hooks; the snippet case-guards `$PATH` so nested
  shells don't keep prepending. (Check which you're in: `shopt -q login_shell`.)
  The same snippet also `cd`s a fresh login/`pct enter` into `/opt/zai-ops` (guarded
  on `$PWD = $HOME`, so a shell that's already navigated elsewhere is left alone) —
  no more manual `cd /opt/zai-ops` on every session.

On the **Proxmox host** itself:

- **Appending to a deb822 `.sources` file can silently do nothing.** The
  `pve-enterprise.sources` / `ceph.sources` files are stanza-based: a field
  only applies if it's contiguous with the rest of the stanza, with no
  intervening blank line. Proxmox ships these files with a trailing blank
  line, so a naive `echo 'Enabled: false' >> file` lands *after* that blank
  line — outside the stanza — and is silently ignored; the repo stays enabled
  and `apt-get update` keeps throwing 401s with no error pointing at the
  cause. `bootstrap.sh` instead inserts the field inside the stanza (before
  `Types:`) with a guarded `sed`, which is also idempotent across re-runs.
- **The "No valid subscription" nag comes back after every upgrade.** The popup
  is a client-side check in `proxmox-widget-toolkit`; a one-shot `sed` on
  `proxmoxlib.js` is undone the moment `apt full-upgrade` ships a fresh copy of
  that package. `bootstrap.sh` instead writes a tiny idempotent patcher
  (`/usr/local/sbin/pve-no-nag`, marker-guarded so re-runs are no-ops) and wires
  it as a dpkg `Post-Invoke` hook (`/etc/apt/apt.conf.d/00-zai-no-nag`), so the
  patch is re-applied automatically after any package operation that replaces the
  file. It restarts `pveproxy` only when it actually patches; hard-refresh the
  browser afterward to clear the cached JS.

Lessons on third-party apt repos under **Debian 13** (any CT):

- **Debian 13 verifies apt signatures with Sequoia (`sqv`), which rejects SHA1
  key self-signatures from 2026-02-01.** A third-party repo whose signing key is
  SHA1-bound (e.g. OpenResty) fails the apt update with `Sub-process /usr/bin/sqv
  returned an error code … not signed`, even though the signature is valid. Fix:
  drop an apt.conf so apt uses the classic `gpgv` verifier
  (`APT::Key::gpgvcommand "gpgv";`) — it checks the same signature but accepts
  SHA1 self-sigs, so authenticity is kept rather than disabled. **Install `gpgv`
  first** (Debian 13 ships none — sqv replaced it), in its own task before the
  override and before the third-party repo, or the override breaks every repo
  including the ones needed to install gpgv (`Cannot find gpgv`). No current role
  needs this — Caddy and Postgres use Debian's own repos — but it's kept here as a
  forward lesson for the next SHA1-bound third-party repo.

Hard-won lessons on the bare-metal **inference nodes** (Debian 13 + NVIDIA):

- **Secure Boot must be disabled** in BIOS — unsigned NVIDIA kernel modules
  won't load otherwise. Manual BIOS step; `nvidia_cuda` asserts it and fails fast.
- **Trixie needs `contrib non-free`.** The minimal install only enables
  `main non-free-firmware`; the NVIDIA packages aren't visible until you add them.
- **`nvidia-cuda-toolkit-gcc` bridges the GCC 14 / nvcc 12.4 mismatch.** Without
  it the CUDA build fails on a compiler-version check.
- **`systemd-networkd` won't DHCP without a `.network` file.** Create
  `/etc/systemd/network/20-wired.network` with `DHCP=yes` during node prep, or the
  node never comes up on the network.
- **CUDA arch is auto-detected** from `nvidia-smi` (`compute_cap`) — no per-host
  build flag to maintain.

Lessons on the **`litellm` CT floor embedder** (the always-on CPU embedding model):

- **The prebuilt `ubuntu-x64` llama.cpp binary runs on Debian 13 via glibc backward
  compat.** It links an older glibc; the host's newer glibc runs it fine (the failing
  case is the reverse — a binary needing a *newer* glibc than the host). The
  `litellm` role's verify step `ldd`-checks for `not found` to catch that early. If a
  future release ever needs a newer glibc, pin an older release rather than switch to
  a source build (which would drag the C++ toolchain onto the lean proxy CT).
- **nomic embeddings need `search_document:` / `search_query:` prefixes, added by the
  client.** `nomic-embed-text-v1.5` degrades without them; LiteLLM passes input
  through verbatim, so the *client* (a future OpenWebUI RAG pipeline) must prepend
  them. Noted so mediocre retrieval isn't re-debugged as a model fault. nomic's full
  8192 context also needs the unit's yarn rope flags (llama.cpp defaults to 2048).

Lessons on **LiteLLM virtual-key management** (F1 fix — Open WebUI's key,
`bin/zai-litellm-key`, and Corliss's provisioner key):

- **`/key/generate` returns a brand-new key on every call — there's no
  "generate if absent" on litellm's side.** Any Ansible task that mints a key
  must guard reuse itself, the same shape as the `happyview_token_encryption_key`
  idiom in `group_vars/all/main.yml`: check whether the secret file already
  exists on the control node first, and only call the API when it's absent.
  The [`litellm`](roles/litellm.md) role does this for
  `openwebui_litellm_key`. `bin/zai-litellm-key create` deliberately does
  **not** do this — an operator minting a named key expects a new key each
  time, so repeat calls mint distinct keys by design (see the script's header
  comment).
- **A generated secret that requires a live API call, not a pure lookup, makes
  the consuming role's *provisioning* fail if the producing role hasn't run
  yet.** Every other generated secret in this repo (`litellm_master_key`,
  `openwebui_secret_key`, …) is a `password`/`pipe` lookup — resolvable in any
  play order, no network dependency. `openwebui_litellm_key` breaks that
  pattern: it can only be produced by calling litellm's own `/key/generate`
  endpoint, so [`open-webui`](roles/open-webui.md)'s play now hard-fails
  (missing-file error) if litellm's play has never successfully minted it —
  previously litellm being down only broke chat at *runtime*. A full
  `provision.yml` run satisfies the order; there's no path to provisioning
  open-webui before litellm has run at least once.
- **That pattern now has a second instance, and it moved a play.**
  `corliss_litellm_provisioner_key` is minted the same way, so
  [`corliss`](roles/corliss.md) inherited the same hard dependency — and unlike
  open-webui, corliss's play used to run *before* litellm's. It was moved below
  it in [`provision.yml`](../ansible/provision.yml), which is safe because
  litellm depends only on postgres. Worth knowing because the failure it
  prevents is not loud on a rebuild of an existing cluster (the secrets file
  survives in `/root/.zai-secrets`) but is total on a fresh one: an empty
  `LITELLM_PROVISIONER_KEY` renders fine, boots fine, and leaves `/api/`
  permanently unable to issue a key.
- **Tiers are LiteLLM teams, and the team ids never leave the CT.** `/team/new`
  mints a fresh `team_id` every call with no generate-if-absent, so the role
  reads `/team/list` first and fills only the gaps — and Corliss resolves a
  member's tier by `team_alias` at runtime rather than holding an id, precisely
  so no generated identifier has to be carried out of Ansible by hand. Editing
  a tier's budget in `litellm_tiers` is picked up by a separate `/team/update`
  pass; creation alone would silently leave the numbers in git describing
  whatever the first run happened to set.

Lessons on **Open WebUI's `PersistentConfig` settings** (`ENABLE_LOGIN_FORM`,
`ENABLE_SIGNUP`, and others):

- **Env var changes silently stop applying after the first boot.** Open WebUI reads
  `PersistentConfig`-wrapped settings from the environment only on its *very first*
  start, then writes them to its own database and reads from **there** on every
  restart after — a changed env value in `open-webui.env.j2` deploys cleanly
  (`provision.yml` shows no error) but has zero effect. This bit deploying corliss
  as the sole login provider: `ENABLE_LOGIN_FORM=false` was set correctly from the
  start, but open-webui had already booted once (with the setting unset, i.e. true)
  before that env line existed, so the local email/password form kept showing.
  Fix: `ENABLE_PERSISTENT_CONFIG=false` makes Open WebUI always trust the
  environment over its DB-cached copy — the correct default for this repo anyway,
  since config is meant to live in git, not a mutable runtime database. See
  [`open-webui`](roles/open-webui.md#oidc-login-corliss-is-the-only-way-in).
- **No native way to skip the login page when OAuth is the only option.** Visiting
  `chat.{{ cluster_domain }}` always lands on `/auth` first, showing a "Continue
  with ZAI" button rather than redirecting straight into the OIDC flow
  ([open-webui/open-webui#24325](https://github.com/open-webui/open-webui/issues/24325)
  is the open feature request). Closed at the edge instead: the [`proxy`](roles/proxy.md)
  role's `caddy_proxy_hosts` supports a per-route `redirects` list, used here to
  302 `/auth*` straight to `/oauth/oidc/login`.
- **A blind `/auth*` redirect breaks its own OIDC login and manifests as an
  infinite redirect loop that looks like an OIDC failure but isn't.**
  open-webui's OIDC callback always finishes a successful login by
  redirecting the browser *back* to `/auth` (its frontend reads the just-set
  `token` session cookie there and completes login client-side) — an edge
  redirect on `/auth*` with no exception also catches that completion
  request and bounces it into another OIDC round-trip, forever. The browser
  loops entirely on `chat.{{ cluster_domain }}/auth`, never visibly reaching
  corliss again, while `journalctl -u open-webui` shows a *successful*
  token exchange on every single cycle — easy to chase as an OIDC config bug
  when the login is actually succeeding every time and the edge redirect is
  what's discarding it. Fix: the `redirects` entry's `skip_if_cookie: token`
  field only fires the redirect when open-webui's session cookie is absent;
  a request already carrying it falls through to the real app. See
  [`proxy`](roles/proxy.md#notes).
- **Setting `REDIS_URL` makes Redis a hard dependency of every authenticated
  request — it does not degrade, it takes chat down and logs everyone out.**
  Established by reading open-webui 0.11.0's source *before* building the
  [`redis`](roles/redis.md) role, on the theory that this is exactly the kind of
  thing that should not be discovered during an outage. `get_current_user` calls
  `is_valid_token(data, request.app.state.redis)` on every request; the
  `await redis.get(...)` inside is unguarded, so a `ConnectionError` propagates
  into the surrounding `except Exception` — which **deletes the `token` cookie**
  and re-raises → 500. And it loops rather than failing once: the OIDC round
  trip doesn't touch Redis, so login succeeds and mints a fresh cookie, then the
  next authenticated call 500s and wipes it again. There is no fallback path
  either — `get_redis_client` returns `None` only when `REDIS_URL` is *unset*,
  and `redis.asyncio.from_url` connects lazily, so "unreachable at boot" is
  indistinguishable from configured. Two mitigations, both deliberate:
  `REDIS_SOCKET_CONNECT_TIMEOUT`/`REDIS_SOCKET_TIMEOUT` are set explicitly
  (unset upstream = no timeout = ~130 s hang per request against a dead CT), and
  `ENABLE_STAR_SESSIONS_MIDDLEWARE` is left off — it defaults off
  *independently* of `REDIS_URL`, which is what keeps the OAuth handshake's own
  state in a signed cookie and Redis off the **login** path. Never set it.

Lessons on **uv-provisioned Python services** ([`corliss`](roles/corliss.md),
[`open-webui`](roles/open-webui.md)) — both need a Python that Debian 13 doesn't
ship, so uv fetches a managed CPython:

- **Point `UV_PYTHON_INSTALL_DIR` under the role's `/opt` home, or the daemon
  can't exec its own interpreter.** uv's default is
  `/root/.local/share/uv/python` — which the units' `ProtectHome=true` hides,
  and which the roles' recursive chown never reaches. Provisioning goes green
  and the unit then fails to start. Both roles place it under the service home
  and follow the install with a `sys._base_executable` check that fails the play
  if it ever lands elsewhere.
- **`uv sync` says "Checked", `uv pip install` says "Audited".** The two have
  *different* no-op wording, so a `changed_when` copied from one to the other
  silently reports changed on every run and notifies a needless service restart.
  Both messages go to **stderr**, not stdout. (Verified on uv 0.11.)
- **`uv sync` builds `.venv` inside the source tree unless
  `UV_PROJECT_ENVIRONMENT` says otherwise.** Every role addresses its venv by
  absolute path (`ExecStart`, the `manage.py` tasks, the chown), so a project
  sync without that variable puts the environment somewhere none of them look
  while still exiting 0.
- **`--check` skips plain `command` probes, so anything keyed on their output
  misfires.** A `command` task without `creates`/`removes` does not run in check
  mode, and its registered stdout comes back empty. corliss and open-webui gate
  the uv install on `uv --version`, so every check run simulated a download
  (nothing fetched) and then failed at extract with `Source
  '/tmp/uv-<version>.tar.gz' does not exist`, on CTs whose uv was already
  current. Read-only probes set `check_mode: false` so check mode sees the real
  box. A different shape of the same limit is not a bug: when a task needs an
  earlier task's real effect, check mode cannot follow. A proxy CT that has
  never had the Caddy backports repo fails `--check` at "Install Caddy",
  because the repo was only simulated. Run the real replay once and the check
  works from then on.

Hard-won lessons about **binding a listen socket at boot**:

- **A service that binds a literal `10.1.1.x` loses a race with
  `systemd-networkd` on a cold boot.** The address doesn't exist yet when the
  daemon starts, the bind fails, and what happens next is the daemon's choice —
  which is the whole problem, because the quiet choice is the common one.
  **Postgres logs a `WARNING` and keeps running on loopback alone**: systemd sees
  a clean start, `systemctl --failed` stays empty, and the only symptom is that
  four downstream services can't reach their database. This caused a production
  outage. **Redis, given the same bad config, refuses to start outright** — the
  loud version of the identical race, and the reason to fix a literal bind
  wherever it appears rather than only where it happened to fail quietly. The fix
  in both cases is to bind the **wildcard** rather than to order the unit
  `After=network-online.target` — the wildcard binds whatever exists whenever it
  exists, so there is no ordering dependency left to get lost in a future unit
  edit, a `Type=` change, or a distro's own ordering. It costs nothing here
  because every service CT is `vmbr1`-only on a no-uplink NAT bridge: "all
  interfaces" *is* the internal network, and the app's own auth (Postgres's
  `pg_hba.conf`, Redis's `requirepass`) is the actual access control. Reach for a
  literal bind only where the box is multi-homed and the bind is the boundary —
  and `proxy` is the one dual-homed CT. **Spell the wildcard the daemon's way:**
  Postgres wants `'*'`, Redis wants `* -::*` (all IPv4 plus *optional* IPv6 — a
  bare `*` makes a failed IPv6 bind fatal and reintroduces a startup failure on
  an IPv4-only CT, which all of these are).
- **Anything postmaster-context needs a *restart*, and a reload will lie about
  it.** `listen_addresses` is the case in point: on SIGHUP Postgres parses the new
  value, reports success, and goes on using the old one until the process cycles.
  An Ansible template that notifies a `reload` handler for such a setting is
  therefore green on every run while never taking effect — the config on disk and
  the running server disagree indefinitely, and the divergence only surfaces at
  the next reboot. Check which handler a template notifies, not just that it
  notifies one.
- **Corollary — `systemctl --failed` is not a health check.** Both failure modes
  above leave systemd perfectly happy. Roles assert the *feature*, not the unit:
  the postgres role's `pg_isready -h {{ ansible_host }}` passes only if the
  listener really came up on the internal IP, which a `systemctl is-active` or a
  local-socket check would not have caught. Prefer that shape of verify.
- **Those asserts run once, at provision time.** They prove a service came up on
  the run that built it and say nothing about it an hour later. Corliss's
  [`/systems/`](roles/corliss.md) is the standing version of the same shape — it
  asks each service the same feature-level question on demand — but it is a
  status page for an admin who is already looking, not monitoring: nothing polls
  it and nothing alerts. The cluster still has no monitoring; see [TODO](README.md#todo).

Hard-won lessons wiring **identity** ([`corliss`](roles/corliss.md)):

- **The atproto `client_id` IS a URL** — specifically
  `<PUBLIC_BASE_URL>/auth/client-metadata.json`. Change the domain *or* that
  path and you have minted a brand-new client identity: every member must
  re-consent at their PDS, and in-flight sessions die. Nothing errors; it just
  silently becomes a different client. Bundle any such move into a single
  cutover rather than paying the re-consent twice.
- **A wildcard origin cert does not cover the apex.** `*.example.com` matches
  `chat.example.com` but *not* `example.com`, and corliss is served at the
  apex — so a wildcard-only Cloudflare Origin CA cert makes the bare domain
  answer **526** under Full (strict) while every subdomain keeps working. Issue
  the cert for `example.com, *.example.com`.

Lessons on **Caddy obtaining its own certs** (the [`proxy`](roles/proxy.md)
role's `caddy_tls_mode: acme`). Both of these look like bugs in the rendered
Caddyfile and are not, so don't "fix" them:

- **`auto_https disable_redirects` does not disable certificate management.**
  The name suggests it switches automatic HTTPS off. It removes only Caddy's
  automatic HTTP-to-HTTPS redirect, and Caddy still obtains and renews every
  cert. The redirect is off on purpose, because the Caddyfile's own `:80` block
  already does it (with a `/healthz` exception) in both cert-bearing modes. The
  setting that really stops issuance is `auto_https off`, which is `none` mode.
- **The explicit `:80` site block does not shadow the HTTP-01 challenge.** Its
  catch-all `redir https://{host}{uri}` looks as if it would bounce Let's
  Encrypt's `/.well-known/acme-challenge/` request to HTTPS. It never sees it:
  Caddy wires its challenge handler in ahead of site routes on the HTTP port.
- **When issuance fails, check public `:80` first.** HTTP-01 means Let's Encrypt
  connects to the domain on port 80 from the internet, so the forward from the
  public address to the proxy CT has to exist and actually work. That is
  deployment config the role cannot see or assert. `journalctl -u caddy` shows
  the CA's own error, which names the address it tried and what came back.

Hard-won lessons writing **Ansible tasks** in this repo:

- **`ansible_managed` only exists in the `template` module.** It is undefined in a
  `copy` task with inline `content:`, and the failure comes at argument
  finalisation rather than at render time:

  ```
  Error while resolving value for 'content': 'ansible_managed' is undefined
  ```

  which names the `content` key and does not read like a templating problem.
  Every managed config here is a `.j2` carrying a `# {{ ansible_managed }}`
  header; keep short files that way too rather than inlining them into `copy`.
- **`collectstatic` keeps a stale file when the collected copy is newer.** It
  compares modification times and skips a source that is older than what is
  already in `STATIC_ROOT`. So when a file that shadowed another is removed (a
  Corliss theme dropping its `theme.css`), the app's older original is skipped
  and the theme's copy goes on being served, with nothing in the output to say
  so. The corliss role adds `--clear` when its checkout changed, and only then,
  since clearing every run would report changed and restart the daemon each
  time.

Hard-won lessons provisioning **human accounts** (`add-github-user.yml`):

- **Forced first-login password change needs a real temp password.** Over SSH
  *key* auth, PAM (`UsePAM yes`, Debian default) asks for the *current* password
  to authorize the new one — a locked/empty account can't complete the change. So
  the account is seeded with a printed temp password rather than locked.
- **Set passwords with `chpasswd`, not `password_hash`.** Debian 13's Python 3.13
  removed the stdlib `crypt` module Ansible's `password_hash` filter used;
  `chpasswd` on the target uses libc crypt instead, so no `python3-passlib` on
  the control node.
