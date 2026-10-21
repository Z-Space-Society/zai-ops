# Role: `monitor_agent`

Installs the [Beszel](https://beszel.dev) **agent** natively on the control node
and every service container, so each one reports its metrics and its pending
apt updates to the [`beszel`](beszel.md) hub. It also adds a daily apt list
refresh, without which the updates count goes stale.

- **Source:** [`ansible/roles/monitor_agent/`](../../ansible/roles/monitor_agent/)
- **Applied by:** [`monitor-agent.yml`](../../ansible/monitor-agent.yml) (`hosts: service_containers:control_node`), which [`provision.yml`](../../ansible/provision.yml) imports after the hub's play. `scn-config`'s **Monitoring Agents** runs it on its own
- **Target:** CT 100 (in place) and every service CT, over SSH. Internal only
- **Tracking:** [zai-ops#3](https://github.com/Z-Space-Society/zai-ops/issues/3), step 3 of six

## Purpose

The agent is one Go binary from the upstream release tarball, verified by
SHA-256, and run as a dedicated system user under systemd. It runs in
**WebSocket mode only**: it dials out to the hub, and `DISABLE_SSH=true` turns
off the fallback listener on `:45876`, so it opens no port.

Agents inside the cluster reach the hub at its **internal address**,
`http://10.1.1.<beszel ctid>:8090` over vmbr1, never through `monitor.<domain>`.
There is no reason to hairpin through the proxy, and this way they work before
the edge has a certificate for that name (Ronchamp, until #48). Plain HTTP on
the bridge does not let something else pose as the hub: the agent checks the
hub's signature against the hub key's public half (`KEY`) before it sends
anything.

Not covered here: the Proxmox host and the inference nodes (step 6), and alerts
(step 5).

## Enrollment is config, not a click

The hub accepts any agent that presents a token from its `universal_tokens`
collection. When no known fingerprint matches, the hub creates the system record
itself, named by the agent's `SYSTEM_NAME` (the inventory name). So nothing is
added by hand:

- the [`beszel`](beszel.md) role writes `beszel_agent_token` (generated once in
  `/root/.zai-secrets`) as the admin's permanent token on every run;
- this role gives every agent the same token, in a root-only env file.

**A rebuilt hub** (same key, same token, empty database) takes the agents back
with nothing touched on them. They keep redialling while it is gone. When it
returns, each one presents the token, matches no fingerprint, and gets its
record recreated. Only metrics history is lost.

**A rebuilt agent CT** gets back its old record and history, because this role
writes the agent's fingerprint as a hash of the cluster domain and inventory
name rather than letting the agent derive one from the machine id. Otherwise a
rebuilt CT would show up as a second system, with the old one left down.

## Tasks

| Task | Module | Why |
| ---- | ------ | --- |
| Skip a service with no CTID | `meta: end_host` | No container to configure. The control node has no ctid by design and is never skipped for it. |
| Check that the hub is up, or skip | `uri` (from CT 100), `debug`, `meta: end_host` | On a new cluster services are provisioned in CTID order and the hub comes late, so each one skips with a message here. Monitoring Agents installs them afterwards. |
| Create `beszel-agent` group + user, then the dirs | `group`, `user`, `file` | Unprivileged, no login shell. `/opt/beszel-agent` (root, the tarball) and `/var/lib/beszel-agent` (agent, `0700`). |
| Download the release | `get_url` with `checksum: sha256:` | Named by version, so a replay downloads nothing and a bump fetches the new release. A mismatch fails the run. |
| Install the binary | `unarchive` (`include: beszel-agent`) | Only when the download changed or the binary is missing. Notifies restart. |
| Place the fingerprint | `copy` | Before the first start, so a rebuilt CT reattaches to its record (above). |
| Write the env file | `template` (`0600 root`, `no_log`) | `HUB_URL`, `TOKEN`, `KEY`, `SYSTEM_NAME`, `DATA_DIR`, `DISABLE_SSH`. systemd reads it as root before dropping privileges. |
| Install the unit, start + enable | `template`, `systemd` | Hardened; `Restart=on-failure` (see Notes). |
| Install the apt list refresh | `copy`, `systemd` | `apt-lists-refresh.timer`, daily with up to an hour's random delay, `Persistent=true`. See Notes for why not `APT::Periodic`. |
| Refresh the lists if a day old | `apt` (`cache_valid_time: 86400`) | A new CT gets current lists before the agent's first check, not whatever the template shipped. |
| Smoke test | `uri` on the hub CT's `127.0.0.1`, `delegate_to: beszel` | Signs in as the superuser and waits until this host's system record is `up`. That proves the agent enrolled and reported, which `systemctl` can't. It does not wait for the updates count, which arrives on a later report. |

There is **no manifest**. The manifest key is the inventory name, so an agent
manifest would overwrite the service's own `<service>.json`. Nothing reaches the
agent as a service either. Its version is `beszel_version`, which the hub's
`beszel.json` already records, and the hub shows each agent's version itself.

### Handlers

| Handler | Action |
| ------- | ------ |
| `reload systemd` | `systemd: daemon_reload=true` |
| `restart beszel-agent` | `service: name=beszel-agent state=restarted` |

## Variables

In [`group_vars/all/main.yml`](../../ansible/group_vars/all/main.yml), shared with the hub role:

| Variable | Meaning |
| -------- | ------- |
| `beszel_version` | The release, the same for hub and agents. |
| `beszel_port` / `beszel_superuser_email` / `beszel_superuser_password` | The hub's port and the superuser the smoke test signs in as. |
| `beszel_agent_token` | Generated once in `/root/.zai-secrets/beszel_agent_token`. |
| `beszel_hub_public_key` | Derived from `/root/.zai-secrets/beszel_hub_key` with `ssh-keygen -y`, so it always matches the hub's key. |

In [`defaults/main.yml`](../../ansible/roles/monitor_agent/defaults/main.yml):

| Variable | Default | Meaning |
| -------- | ------- | ------- |
| `monitor_agent_url` / `monitor_agent_sha256` | the v`beszel_version` `beszel-agent_linux_amd64.tar.gz` | Bump the checksum with the version, taken from the release's `beszel_<version>_checksums.txt`. |
| `monitor_agent_hub_url` | `http://<beszel ansible_host>:<beszel_port>` | The hub over vmbr1. |
| `monitor_agent_data_dir` | `/var/lib/beszel-agent` | `DATA_DIR`: the fingerprint, and the unit's only writable path. |
| `monitor_agent_fingerprint` | 48 hex chars of `sha256(<domain>/<inventory name>)` | What the hub knows this machine by. |

## Dependencies

- [`beszel`](beszel.md): provisioned and answering, with the token written.
  Without it every host is skipped, not failed.

## Verify

From CT 100, every system the hub knows, whether it is up, how fresh its record
is, and its pending updates as `[total, security]`:

```sh
D=$(awk '$1=="cluster_domain:"{print $2}' /opt/zai-ops/ansible/inventory/local.yml)
D=$D python3 - <<'EOF'
import json, os, urllib.request
from datetime import datetime, timezone
H = 'http://10.1.1.123:8090'
def call(path, body=None, tok=None):
    hdr = {'Content-Type': 'application/json', **({'Authorization': tok} if tok else {})}
    req = urllib.request.Request(H + path, json.dumps(body).encode() if body else None, hdr)
    return json.load(urllib.request.urlopen(req))
pw = open('/root/.zai-secrets/beszel_superuser_password').read().strip()
tok = call('/api/collections/_superusers/auth-with-password',
           {'identity': 'ansible@' + os.environ['D'], 'password': pw})['token']
now = datetime.now(timezone.utc)
for s in call('/api/collections/systems/records?perPage=50&sort=name', tok=tok)['items']:
    age = int((now - datetime.fromisoformat(s['updated'].replace(' ', 'T'))).total_seconds())
    print(f"{s['name']:14} {s['status']:6} {age:5}s ago  updates={s['info'].get('pu')}")
EOF
```

On one CT, the refresh timer: `systemctl list-timers apt-lists-refresh.timer`.

## Notes

> [!note] Updates inside an LXC
> The agent skips the updates check only when `/.dockerenv` or
> `/run/.containerenv` exists, which marks a Docker or Podman container. A
> Proxmox LXC has neither, so its updates are counted.

> [!note] Why `Restart=on-failure`
> The hub uses `always`; the agent doesn't need to. It exits 0 only when
> signalled to stop, its own errors exit 1, and losing the hub is not an exit at
> all, because it keeps redialling. A stopped agent is also not silent: the hub
> marks its system down. Nothing would report a stopped hub.

> [!note] What the hardening must still allow
> The hourly check runs `apt-get -s dist-upgrade` as `beszel-agent`. A
> simulation takes no lock and only reads `/etc/apt`, `/var/lib/apt/lists` and
> `/var/lib/dpkg/status`. Its pkgcache write to `/var/cache/apt` fails, and apt
> builds the cache in memory instead. `ProtectSystem=strict` is enough, but
> nothing added to the unit may hide `/var/lib` or `/etc/apt`, or filter the
> fork and exec of `apt-get` and `dpkg`.

> [!note] Why a timer of our own, not `APT::Periodic`
> The agent never runs `apt-get update`
> ([beszel#2606](https://github.com/henrygd/beszel/issues/2606)), so its count is
> only as fresh as the lists. `APT::Periodic::Update-Package-Lists` acts only
> through `apt-daily.timer` and `apt.systemd.daily`, whose stamp files and random
> sleeps decide whether a run happens and leave no record when one doesn't.
> `apt-lists-refresh.timer` shows its last run in `systemctl list-timers` and its
> result in the journal. It also leaves apt's periodic settings alone, so
> unattended upgrades stay a separate decision.

> [!note] The control node, and copied units
> CT 100 is covered like any CT. `site.yml` leaves the agent out, because a new
> cluster has no hub when it runs, so after a CT 100 rebuild Monitoring Agents
> is the step that brings its agent back. The role copies its units into
> `/etc/systemd/system` on every host, CT 100 included, because the service
> CTs have no repo clone to link to. On CT 100 a unit change therefore needs a
> Monitoring Agents run, not just a `git pull`.

> [!note] The token is a live credential
> `/api/beszel/agent-connect` stays open on the public route for agents outside
> the cluster (step 6), and whoever holds `beszel_agent_token` can add systems
> to the hub. It lives only in `/root/.zai-secrets` and in each host's
> root-only `/etc/beszel-agent.env`.
