# Role: `redis`

Installs [Redis](https://redis.io/) **natively** as the cluster's revocation
store — the piece that lets [`open-webui`](open-webui.md) invalidate a session
JWT it has already issued, which is what makes OIDC back-channel logout from
[`corliss`](corliss.md) actually end somebody's chat session.

- **Source:** [`ansible/roles/redis/`](../../ansible/roles/redis/)
- **Applied by:** [`provision.yml`](../../ansible/provision.yml) (configure play, `hosts: redis`, immediately after the postgres play)
- **Target:** the `redis` CT (whatever CTID it was assigned — **core infra, 100–109**), over SSH, internal-only on `vmbr1`

## Purpose

One apt package and one config file. Redis runs under systemd directly — **no
Docker** (per the [prime directive](../../CLAUDE.md)) — from Debian 13's own
repo, so there's no third-party apt key to manage (same reasoning as
[`postgres`](postgres.md) taking PG 17 from the distro rather than PGDG).

**What it is actually for**, because "we added a cache" would be the wrong
story and would invite someone to point other things at it:

Open WebUI mints its own session JWT after the OIDC exchange and authenticates
from it thereafter, so corliss is **not consulted again** until that token
expires. corliss enforces membership at `/oidc/authorize` (GATE, v0.5.0), which
Open WebUI only reaches when it has no valid session of its own — so revoking a
member used to leave them chatting until `JWT_EXPIRES_IN` ran out. corliss now
POSTs a signed `logout_token` on sign-out and on revocation, and Open WebUI
handles it at `POST /oauth/backchannel-logout`. **Without Redis that handler
can only delete stored OAuth sessions** — it cannot invalidate the JWT the
browser is authenticating with, which is the only thing that matters. Redis is
where it writes "every token issued for this user before now is dead"
(`open-webui:auth:user:<id>:revoked_at`).

So the contents are a handful of TTL'd markers: a few kilobytes, 30-day
expiry, nothing derived from anything and nothing anyone can miss once the
relying party's own tokens have aged out.

### Why core infra (100–109) and not platform

The tier rule in the [main docs](../README.md#ctid-tiers) that separates
platform from applications — *who talks to it* — doesn't settle core vs.
platform, and by itself would put Redis in either. The rule that does settle it
is what core infra **is**: the data foundations, with the dependency arrows
pointing downward. Redis is storage with no logic of its own, consumed by an
app — exactly the shape [`postgres`](postgres.md) and
[`object_store`](object_store.md) have. The platform tier holds services *with*
logic: the edge, the AppView, the gateway.

**One thing "core infra" would otherwise imply and shouldn't here: this CT is
deliberately outside the backup set.** Its data is disposable by design (see
persistence below), so [`backup`](backup.md) has nothing to take from it. That
is a property of the data, not an oversight.

## Tasks

| Task | Module | Why |
| ---- | ------ | --- |
| Install Redis | `apt` | Debian's `redis-server` package. It creates the `redis` user, `/var/lib/redis`, and the `redis-server` unit; the template below replaces the config it ships. |
| Deploy `redis.conf` | `template` (`0640 root:redis`, `no_log`) | Full-file, not a drop-in — see notes. `0640` root:redis because it carries `requirepass`: the daemon must read it and nobody else should. Notifies restart. |
| Ensure started + enabled | `ansible.builtin.service` | Running now + on boot. |
| Flush handlers | `meta: flush_handlers` | Bring the daemon up on the final config *before* proving it works — otherwise the checks below can pass against the still-running old config and report a bind or auth change that never took. Same idiom as [`postgres`](postgres.md). |
| Verify PING on the internal address | `command` → `redis-cli -h {{ ansible_host }}` (`failed_when`), password via `REDISCLI_AUTH` | The listener on the **internal** address is the feature being shipped: open-webui reaches it from a different CT, so a loopback PING would prove nothing about the thing that has to work. Authenticating proves `requirepass` and the password open-webui will put in its `REDIS_URL` agree. The password rides an env var rather than `-a` so it stays out of `ps` on the CT — and so the task needs no `no_log`, which would hide the error in the one run where it matters. |
| Assert `maxmemory-policy` | `command` → `config get` (`failed_when`) | The one setting whose wrong value fails **silently and dangerously** — see below. Asserted against the *running server*, not assumed from the template having rendered. |

### Handlers

| Handler | Action |
| ------- | ------ |
| `restart redis` | `service: name=redis-server state=restarted` — bind/port/`requirepass` are none of them re-read on SIGHUP |

## Variables

Defined in [`defaults/main.yml`](../../ansible/roles/redis/defaults/main.yml):

| Variable | Default | Meaning |
| -------- | ------- | ------- |
| `redis_port` | `6379` | Listen port. |
| `redis_bind` | `* -::*` | Wildcard **on purpose** — see [Notes](#notes). Redis's all-interfaces form: all IPv4, IPv6 optional (the `-` prefix), which matters on this IPv4-only CT. |
| `redis_package` / `redis_service` / `redis_config_file` | `redis-server` / `redis-server` / `/etc/redis/redis.conf` | Debian's names, held in variables so the tasks read as intent. |
| `redis_maxmemory` | `64mb` | Two orders of magnitude above what actually lives here — a guard against a runaway, not a capacity plan. |
| `redis_maxmemory_policy` | `noeviction` | **Load-bearing. Do not change to an LRU/TTL policy** — see below. |
| `redis_password` | `{{ redis_auth_password }}` | Wraps the shared group_vars secret, same idiom as `openwebui_oidc_client_secret` wrapping `corliss_oidc_client_secret`. |

### Two config choices that are load-bearing, not tuning

Both are places where the obvious setting **fails open**, quietly.

**`maxmemory-policy noeviction`.** Every eviction policy silently drops keys
under memory pressure, and the keys here are *revocations* — so `volatile-ttl`
or `allkeys-lru` would mean a full Redis quietly restores a revoked member's
chat access, with nothing anywhere reporting it. `noeviction` makes the write
fail instead: Open WebUI errors on the logout POST, corliss logs a delivery
failure, and the problem is visible. Fail-visible over fail-open. The role
asserts the *running* value rather than trusting the template.

**No persistence (`save ""`, `appendonly no`).** Correct — this is TTL'd state
that nothing can rebuild and nobody needs once the relying party's tokens have
aged out, and it is what keeps the CT free of unreproducible state. But be
clear-eyed about the direction it fails: **a `redis-server` restart resurrects
every revoked member's session**, because the marker that killed their token is
gone. That window is bounded by open-webui's `JWT_EXPIRES_IN` (4h) and by
nothing else, which is the single sharpest reason
[that value stays short](open-webui.md#sessions-outlive-corliss).

### Secret (auto-generated — no manual step)

`redis_auth_password` is generated on first run by the `password` lookup in
[`group_vars/all/main.yml`](../../ansible/group_vars/all/main.yml) and persisted
under `/root/.zai-secrets` on CT 100 (same posture as the litellm/openwebui/
garage/restic secrets, **not** the vault). Because the lookup runs on the control
node, this role's `requirepass` and the open-webui role's `REDIS_URL` resolve to
the identical value, and both stay stable across rebuilds. Hex, so it needs no
percent-encoding inside that URL.

## Dependencies

**None of its own** — one apt package and one config file, no database, no other
CT. Its position after [`postgres`](postgres.md) in `provision.yml` groups the
two data stores; it isn't an ordering requirement.

It is depended *upon*, though, and asymmetrically:

- **[`open-webui`](open-webui.md)** must be provisioned after it and builds
  `REDIS_URL` from this host's derived address, so a `--limit open-webui` run
  needs the `redis` host assigned (its `ansible_host` is required to render the
  env file). **At runtime the coupling is much stronger than "needs it for
  logout" — see below.**
- **[`corliss`](corliss.md)** never talks to Redis at all. It POSTs a
  `logout_token` to open-webui and knows nothing about how open-webui honours it.

## The trade this role makes, stated deliberately

**Once `REDIS_URL` is set, Redis is a hard dependency of the entire chat
surface, not just of logout.** This was established from Open WebUI 0.11.0's
source before the role was built, because the alternative was discovering it
during an outage:

- `get_current_user` calls `is_valid_token(data, request.app.state.redis)` on
  **every** authenticated request. The `await redis.get(...)` inside it is
  unguarded, so a `ConnectionError` propagates into the surrounding
  `except Exception`, which **deletes the `token` cookie** and re-raises → 500.
- It loops rather than degrades: the OIDC round trip itself doesn't touch
  Redis, so login succeeds and mints a fresh cookie — and the next authenticated
  call 500s and wipes it again.
- There is no "unreachable at boot ⇒ run without it" path. `get_redis_client`
  returns `None` only when `REDIS_URL` is *unset*; `redis.asyncio.from_url`
  connects lazily and never fails at startup.

**Why the trade was accepted:** both CTs live on the same Proxmox host and the
same `vmbr1` bridge, so there is no network between them to partition. The
genuinely new failure modes are narrow — this CT failing to boot, or
`redis-server` crashing — and the alternative (co-locating Redis on the
open-webui CT) would have bought only those two back at the cost of the
one-service-per-CT convention the whole inventory is built on.

**Two things that hold the blast radius down**, both deliberate:

- **`REDIS_SOCKET_CONNECT_TIMEOUT` / `REDIS_SOCKET_TIMEOUT` are set to `2` in
  the open-webui role.** Both are *unset* upstream, meaning no timeout at all:
  against a CT that is down (SYN into the void, no RST) every authenticated
  request blocks for the OS TCP timeout, ~130 s, before failing. That turns an
  outage from fast and obvious into a hung site.
- **`ENABLE_STAR_SESSIONS_MIDDLEWARE` is left off**, which is its default
  *independently* of `REDIS_URL`. That is the property this deployment relies
  on: with it off, the OAuth handshake's own state stays in a signed cookie, so
  Redis sits on the authenticated-request path but **not on the login path**.

## Verify

**`redis-cli` is on the redis CT, not on CT 100.** It arrives with the
`redis-server` package, and CT 100 is the control node — nothing installs redis
tooling there. So these run *on the CT*, reading the password out of the config
file beside them, which also keeps it off the wire and out of CT 100's shell
history:

```bash
ssh root@10.1.1.<ctid>
export REDISCLI_AUTH=$(awk '/^requirepass/{print $2}' /etc/redis/redis.conf)

systemctl is-active redis-server
redis-cli ping                              # PONG
redis-cli config get maxmemory-policy
# → noeviction. Anything else is a silent fail-open; see above.

# What open-webui has actually written. Empty db0 means it has never reached
# Redis at all — a bigger signal than any single key being absent.
redis-cli info keyspace
redis-cli keys 'open-webui:auth:user:*'
```

The end-to-end check is the only one that proves the point of the role: with a
test member signed in to chat, revoke them in the console and confirm their
session ends within seconds. A `…:auth:user:<id>:revoked_at` key appearing is
the half that says corliss delivered and open-webui accepted; the session
actually ending is the half that says open-webui then *read* it.

When those two disagree, the logs say which leg broke — corliss reports a
delivery failure, open-webui reports a rejected token and why:

```bash
ssh root@<corliss-ip>    "journalctl -u corliss --since '1 hour ago' | grep -i back-channel"
ssh root@<open-webui-ip> "journalctl -u open-webui --since '1 hour ago' | grep -i backchannel"
```

## Notes

- **The config is a full file, not a drop-in, and Debian's defaults are NOT
  inherited.** Redis has no `conf.d` include directory the way Postgres does, so
  the choice was a rendered file or in-place edits to a 2000-line vendor file;
  a rendered file is the one that keeps configuration in git and makes a drifted
  CT impossible (same reasoning as the [`proxy`](proxy.md) role's git-tracked
  Caddyfile). The cost is real: anything the template doesn't say takes
  **Redis's own built-in default**, not the distro's, so add settings there
  rather than assuming Debian's are in force.
- **`bind` is the wildcard, and must stay that way.** Naming the CT's internal
  address literally loses a race with `systemd-networkd` on a cold boot: the
  address doesn't exist yet, and Redis treats a failed bind as **fatal** and
  refuses to start. That's the loud version of the race — it lands in `systemctl
  --failed`, unlike [`postgres`](postgres.md#notes), which hit the identical race
  and merely warned before serving loopback only. Loud is not harmless, though:
  a missing revocation store fails in the direction that *resurrects* revoked
  members' sessions (see the persistence trade above). `* -::*` binds whatever
  exists whenever it exists, deleting the ordering dependency rather than
  sequencing around it with `After=network-online.target`. Use that exact form,
  not a bare `*`: the `-` prefix makes a failed IPv6 bind non-fatal, and this CT
  is IPv4-only. See [Known gotchas](../README.md#known-gotchas).
- **`protected-mode yes` is not what's protecting this.** With an explicit
  `bind` and a `requirepass`, protected mode never comes into play — the actual
  boundary is the `vmbr1`-only NAT network with no LAN route, reinforced by the
  password. Same posture as [`postgres`](postgres.md). The wildcard `bind`
  doesn't change that: protected mode only engages when there is *no* password,
  and there is one.
- **Nothing else should be pointed at this without re-reading the trade above.**
  It is sized and configured for one job. In particular, don't set
  `WEBSOCKET_MANAGER=redis` on open-webui casually — that widens the runtime
  coupling beyond the request path this role's failure analysis covers.
- For how the CT is assigned a CTID, created and reached, see
  [`provision.yml`](../../ansible/provision.yml) and the
  [main docs](../README.md#service-ctid-assignment).
