# Role: `beszel`

Installs the [Beszel](https://beszel.dev) **hub** natively as the cluster's
monitoring dashboard. The hub shows per-machine metrics and, since v0.21.0, the
OS updates pending on each machine. Each machine reports through a small agent,
installed by [`monitor_agent`](monitor_agent.md). It emails alerts through the
cluster's mail relay.

How to use it day to day, as an admin: [Using the system
monitor](../monitoring.md).

- **Source:** [`ansible/roles/beszel/`](../../ansible/roles/beszel/)
- **Applied by:** [`provision.yml`](../../ansible/provision.yml) (configure play, `hosts: beszel`, last)
- **Target:** the `beszel` CT (CTID 123 by convention, application tier, 120–129), over SSH. Public through the [`proxy`](proxy.md) at `monitor.<domain>`
- **Tracking:** [zai-ops#3](https://github.com/Z-Space-Society/zai-ops/issues/3), steps 2 and 5 of six

## Purpose

The hub is one Go binary from the upstream release tarball, verified by
SHA-256, and run as a dedicated system user under systemd. PocketBase, the
backend it embeds, keeps its state in SQLite in `/var/lib/beszel`.

It is an application, not platform, because of who uses it: admins sign in and
land on it. Its public route also lets agents on machines outside the cluster
dial in.

Admins sign in through Corliss (step 4,
[Corliss#25](https://github.com/Z-Space-Society/Corliss/issues/25)): the hub is
Corliss's second confidential OIDC client, and Corliss refuses anyone who is
not on the admin roster. Password login stays on beside it, as the
Ansible-owned admin below, until the sign-in is proven on Heron; turning it
off, and removing hub users who have left the roster, are the next steps on
that issue. The agents (step 3) are the [`monitor_agent`](monitor_agent.md)
role; this role writes the token they enroll with.

## SQLite: config is code, history is disposable

The backup tier is `pg_dumpall`, and the hub's database is SQLite, so the role
keeps nothing in it that cannot be rebuilt. Every run of the role re-asserts:

- **the hub key**, `id_ed25519` in the data dir, written from
  `/root/.zai-secrets/beszel_hub_key` before the hub's first start;
- **the superuser** Ansible uses on `127.0.0.1`;
- **the admin login**, its role, verification and password;
- **the agent token**, `/root/.zai-secrets/beszel_agent_token`, as the admin's
  permanent universal token;
- **alert mail**: the relay and the recipient (see [Alerts](#alerts));
- **the alert policy** on every system it knows.

A rebuilt CT therefore comes back with the same key, so agents still trust it,
the same login, and the same token, so every agent re-enrolls by itself. Its
systems come back as the agents redial, after this role's run, so a Monitoring
Agents run afterwards puts their alerts back. The only thing it loses is metrics history. That is
accepted, and there is **no Tier-2 backup** of this CT.

## Tasks

| Task | Module | Why |
| ---- | ------ | --- |
| Remove Postfix | `apt` (`absent`, `purge`) | PocketBase sends through sendmail whenever its SMTP setting is off, and the template's Postfix can't write its queue from inside the hardened unit: `postdrop` retries forever and hangs the send. Without it, such a send fails at once in the hub's log. Mail leaves only through the relay. |
| Create `beszel` group + user | `group`, `user` | Run the hub unprivileged, no login shell. |
| Create the download and data dirs | `ansible.builtin.file` | `/opt/beszel` (root, holds the tarball) and `/var/lib/beszel` (`beszel`, `0700`). |
| Download the release | `get_url` with `checksum: sha256:` | Named by version, so a replay downloads nothing and a bump fetches the new release. A mismatch fails the run. |
| Install the binary | `unarchive` (`include: beszel`) | Only when the download changed or `/usr/local/bin/beszel` is missing. Notifies restart. |
| Place the hub key | `copy` (`0600 beszel`, `no_log`) | Before the first start. The hub generates its own key only when this file is missing, and agents trust whichever key it had when they were enrolled. |
| Install the systemd unit | `template` | Hardened; `ReadWritePaths` is the data dir only; `Restart=always`. `APP_URL` is `https://monitor.<domain>`. `SHARE_ALL_SYSTEMS=true`, so every signed-in user sees every system whichever user owns the token. Every user is an admin, now and after step 4, so this opens nothing new. `USER_CREATION=true`, so an admin's first sign-in through Corliss can create their user: the hub rewrites `users.createRule` from it at every start, so it can't be set through the API. `OAUTH_DISABLE_POPUP=true`, so the sign-in runs in the same tab and Corliss returns to `https://monitor.<domain>/`, the redirect the corliss role registers; the popup flow ends on a `/_/` page the proxy blocks. Notifies reload + restart. |
| Ensure started + enabled, flush handlers | `systemd`, `meta` | Up with its final config before the smoke test. |
| Smoke test | `wait_for` `127.0.0.1:8090`, then `uri /api/health` | `/api/health` must return 200. |
| Assert the superuser | `command` → `runuser -u beszel -- beszel superuser upsert … --dir` | The PocketBase CLI is the only way to set a superuser without already having one. It runs as `beszel`, so it never leaves a root-owned SQLite file. `changed_when: false`: upsert always succeeds and only re-hashes. |
| Sign in, look up the admin | `uri` (`no_log`) | A superuser token from `/api/collections/_superusers/auth-with-password`, then the user by email. |
| Turn off the superuser's new-login alerts | `uri` PATCH `/api/collections/_superusers` | PocketBase mails a superuser on every sign-in from a new address or client. This one is Ansible's and signs in every run, so the notices to `ansible@<domain>` are noise. The admin login keeps its alerts. |
| Create the admin | `uri` POST (`no_log`) | Only when missing. `verified: true` (the users collection's `authRule` is `verified=true`) and `role: admin`. |
| Check, then restore the admin | `uri` (`no_log`) | Signs in as the admin. It PATCHes only what drifted, and sets the password only when signing in fails, because setting it rotates PocketBase's token key and would sign the admin out on every run. |
| Write the Corliss sign-in provider | `uri` PATCH `/api/collections/users` (`no_log`) | Provider `oidc`: `authURL` is Corliss's public `/oidc/authorize`; `tokenURL` and `extra.jwksURL` are its **internal** address; `extra.issuers` is the public origin, which is the `iss` Corliss signs; `userInfoURL` is empty, so PocketBase reads the `id_token` (Corliss has no userinfo endpoint). Without `jwksURL` PocketBase would not check the signature. Sent every run and reported unchanged: a GET leaves the secret out. Beszel leaves OAuth2 config alone at startup, so it survives a restart. |
| Verify the sign-in legs | `uri` to Corliss's internal JWKS, then GET `/api/collections/users` and `assert` | The JWKS must answer from this CT, and the hub must report the provider with the client id and internal token URL written above. |
| Look up, then write the agent token | `uri` (`no_log`) | `universal_tokens` has no API rules, so only a superuser can write it. POST when missing, PATCH when it differs. The hub's own `/api/beszel/universal-token` needs a user session, which step 4 removes. The admin owns it because that record is Ansible's and stays after step 4. |
| Set the mail settings | `uri` PATCH `/api/settings` (`no_log`, [`mail.yml`](../../ansible/roles/beszel/tasks/mail.yml)) | The relay, parsed from `smtp_url`, and the sender. Sent every run and reported unchanged: a GET masks the password, so there is nothing to compare. |
| Create, then set the admin's user settings | `uri` | `settings.emails` = `[alert_email]`, or `[]` when alerts are off. Every other key (chart range, units) is left as the admin set it. Created first and then patched, because the hub's create hook sets `emails` to the user's own address whatever the request says. |
| Put the alerts on every system | `uri` ([`alerts.yml`](../../ansible/roles/beszel/tasks/alerts.yml)) | `beszel_alerts` on every system the hub knows: POST what is missing, PATCH a value or delay that differs. Never deletes. Also run on its own as the last play of `monitor-agent.yml`. |
| Record the manifest | `include_role: manifest` (`beszel_version`) | Last, after the smoke test. Writes `beszel.json`. See [`manifest`](manifest.md). |

### Handlers

| Handler | Action |
| ------- | ------ |
| `reload systemd` | `systemd: daemon_reload=true` |
| `restart beszel` | `service: name=beszel state=restarted` |

## Variables

In [`group_vars/all/main.yml`](../../ansible/group_vars/all/main.yml), because
other roles read them too:

| Variable | Default | Meaning |
| -------- | ------- | ------- |
| `beszel_version` | `0.21.0` | The release for the hub, and for [`monitor_agent`](monitor_agent.md). |
| `beszel_host` | `monitor.{{ cluster_domain }}` | The public hostname. The proxy routes it; the hub's `APP_URL` is built from it. |
| `beszel_port` | `8090` | Listen port. The agents' `HUB_URL` names it. |
| `beszel_superuser_email` | `ansible@{{ cluster_domain }}` | Ansible's superuser, also used by the agents' smoke test on the hub's `127.0.0.1`. Receives no mail. |
| `beszel_agent_token` | generated | `/root/.zai-secrets/beszel_agent_token`, the token every agent enrolls with. |
| `beszel_hub_key` | generated | Ed25519 key in OpenSSH format, made once by `ssh-keygen` into `/root/.zai-secrets/beszel_hub_key`. |
| `beszel_superuser_password` / `beszel_admin_password` | generated | `password` lookups under `/root/.zai-secrets`. |

In [`defaults/main.yml`](../../ansible/roles/beszel/defaults/main.yml):

| Variable | Default | Meaning |
| -------- | ------- | ------- |
| `beszel_hub_url` / `beszel_hub_sha256` | the v`beszel_version` tarball | Bump the checksum with the version, taken from the release's `beszel_<version>_checksums.txt`. |
| `beszel_bind` | `0.0.0.0` | Listen address. The wildcard is mandatory, for the same cold-boot reason as `sync_relay_host`. |
| `beszel_data_dir` | `/var/lib/beszel` | PocketBase's `--dir`: the database and the hub key. |
| `beszel_app_url` | `https://{{ beszel_host }}` | Rendered as `APP_URL`. |
| `beszel_admin_email` | `admin@{{ cluster_domain }}` | The password login, kept beside Corliss sign-in until it is turned off. Receives no mail. |
| `beszel_oidc_client_id` | `beszel` | Must equal the corliss role's `corliss_beszel_oidc_client_id`. The secret is `corliss_beszel_oidc_client_secret` in group_vars, shared with the corliss role. |
| `beszel_oidc_display_name` | `{{ cluster_domain }}` | What the hub's sign-in button names. |
| `beszel_corliss_public_url` | `https://{{ cluster_domain }}` | Corliss's public origin: the browser's `authURL`, and the issuer. Mirrors `corliss_url`. |
| `beszel_corliss_internal_url` | `http://{{ hostvars['corliss'].ansible_host }}:8000` | Where the hub exchanges the code and fetches the JWKS, over vmbr1. Port mirrors `corliss_port`. |
| `beszel_email_from_address` / `beszel_email_from_name` | `monitor@{{ cluster_domain }}` / `SCN monitor` | The alerts' sender. The relay must accept it, as it must the PDS's. |
| `beszel_alerts` | Status 5 min, Disk 90% 10 min, Memory 90% 10 min, SystemdFailed | The alert policy for every system. Each entry is Beszel's alert `name`, a `value` (a percentage where the kind has one) and `min` (minutes). |

From the runtime inventory, set by [`set-smtp.yml`](../../ansible/set-smtp.yml)
(`scn-config` → Set SMTP → Alert recipient):

| Variable | Meaning |
| -------- | ------- |
| `alert_email` | Where alerts are mailed. Alerts are off unless this and `smtp_url` are both set. |

## Dependencies

- [`object_store`](object_store.md), for the manifest write.
- [`proxy`](proxy.md), for `monitor.<domain>`. DNS for that name must reach the
  edge before Caddy can issue its certificate.

Every service CT's agent, CT 100's and the Proxmox host's dial in to the hub
over vmbr1, and the inference nodes' through `monitor.<domain>` (see
[`monitor_agent`](monitor_agent.md)). Alerts go out through the relay in
`smtp_url`, set with `set-smtp.yml`. Corliss (v1.6.0+) probes the hub's
`/api/health` for `/systems/`, at the address in `corliss_beszel_hub_url`.

## Alerts

Every system the hub knows carries the alerts in `beszel_alerts`, owned by the
admin user. Each one mails the alert recipient (`alert_email`) through the
cluster relay (`smtp_url`).

- **Off unless both are set.** Then SMTP is disabled in the hub and the admin
  has no recipient. The empty recipient list is what actually stops mail: with
  SMTP off, PocketBase would fall back to `sendmail`.
- **Mail security:** `smtps://` is implicit TLS. `smtp://…?tls=required` is
  STARTTLS: PocketBase sends STARTTLS when the relay offers it, and Go's
  `PlainAuth` won't send a password over a connection that never upgraded. A
  relay that drops STARTTLS fails the send, so the password never goes out in
  clear.
- **Why the alerts are written every agent run.** Beszel has no alert for
  "every system, including later ones". An alert is a record per user, system
  and kind, so it can only be written once the system exists, and systems appear
  when an agent first enrolls. So `alerts.yml` runs here and again as the last
  play of `monitor-agent.yml`. That covers machines Ansible doesn't manage (the
  host, the nodes) and systems a rebuilt hub recreated after this role finished.
- **The policy is code.** A value or delay changed in the UI is put back on the
  next run. An extra alert added in the UI stays, because the role never deletes.
  Quiet hours are left alone.
- **One recipient.** The alerts belong to the admin user, so they go only to
  `alert_email`. Admins who sign in through Corliss after step 4 get no copies.

## Public surface

The proxy forwards `monitor.<domain>` except for these paths, which it answers
with a 404:

| Path | What it is |
| ---- | ---------- |
| `/_/*` | The PocketBase superuser dashboard. |
| `/api/collections/_superusers/*` | Superuser sign-in and records. |
| `/api/collections/pbc_3142635823/*` | The same collection by its id. PocketBase resolves a collection by name **or** id, and this id is fixed (`"pbc_" + crc32("auth_superusers")`), so blocking only the name leaves the door open. |
| `/api/beszel/create-user` | Creates the first user with no auth. The hub registers it only while the users table is empty at startup, which on a fresh CT is the moment before this role adds its admin. |

`/api/beszel/agent-connect` stays open on purpose: agents outside the cluster
(step 6) dial in through it, with the agent token.

Ansible uses all of these on `127.0.0.1`, inside the CT.

## Verify

From CT 100:

```sh
curl -s -o /dev/null -w '%{http_code}\n' http://10.1.1.<ctid>:8090/api/health   # 200
```

Through the edge:

```sh
D=$(awk '$1=="cluster_domain:"{print $2}' /opt/zai-ops/ansible/inventory/local.yml)
for p in /api/health /_/ /api/collections/_superusers/auth-with-password \
         /api/collections/pbc_3142635823/auth-with-password /api/beszel/create-user; do
  printf '%s ' "$p"; curl -s -o /dev/null -w '%{http_code}\n' "https://monitor.$D$p"
done   # 200, then 404 four times
```

Sign in at `https://monitor.<domain>` as `admin@<domain>` with
`cat /root/.zai-secrets/beszel_admin_password` from CT 100.

## Notes

> [!note] A throwaway superuser named `_@b.b`
> Beszel's first migration creates a superuser `_@b.b` with a random password
> nobody knows, unless `USER_EMAIL` and `USER_PASSWORD` are set. The role leaves
> it in place: it cannot sign in without that password, and its dashboard is
> blocked at the edge anyway.

> [!note] The superuser password is briefly in argv
> `beszel superuser upsert` takes the password as an argument and has no other
> input. It is visible in the CT's process list for the moment the command
> runs, and nothing else runs on this CT.
