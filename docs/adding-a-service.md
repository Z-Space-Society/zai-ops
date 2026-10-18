# Adding a service

A checklist for adding a new service container to the cluster. Each step says
what to do, why it matters, and which existing role to copy.

A service is not finished when its play passes. It is finished when Corliss's
`/systems/` can say whether it is up **and** which version and zai-ops revision
it runs. That takes a health check in Corliss (steps 5 and 6) and a manifest at
the end of the role (step 3). Skipping either leaves a row that is missing or
that makes claims nothing checks.

## 1. Inventory and CTID

- Add the host to [`ansible/inventory/hosts.yml`](../ansible/inventory/hosts.yml)
  under `service_containers`, named as the service (e.g. `sync-relay`).
- Give it a `default_ctid` there: a free number in its tier's range (see
  [Networking](README.md#networking)). It is only what `scn-config` proposes.
- Assign it on each cluster with `scn-config`. The assignment is runtime data in
  the git-ignored `inventory/local.yml` and is never committed. See
  [Service CTID assignment](README.md#service-ctid-assignment).
- If it depends on another service's data (a Postgres database, Redis, the
  object store), add a row to the ordering table in
  [`diagrams.md`](diagrams.md).

## 2. Role

Create `ansible/roles/<role>/`. Copy [`sync_relay`](roles/sync_relay.md) for a
source build, [`litellm`](roles/litellm.md) for a pip install, or
[`redis`](roles/redis.md) for a Debian package.

- **Know the version you install.** For software fetched by tag or release,
  pin it in a `<service>_version` default. For a Debian package, don't pin:
  accept what apt ships, and read it back with `dpkg-query` in step 3. A pinned
  apt version fails a replay the day Debian ships a point release.
- **Build only when something moved.** For a source build, rebuild when the
  pinned checkout changes or the installed binary is missing, and remove the
  build tree after installing (copy `sync_relay`'s clone, `stat` and `when`).
  A replay should cost seconds, not a compile.
- **Run on generated secrets alone.** Secrets the play can mint are
  `lookup('ansible.builtin.password', ...)` recipes in
  `group_vars/all/main.yml`. Secrets only an operator can supply are read with
  `errors='ignore'` and `| default('', true)` (copy `smtp_url` there), so a
  missing file reads as empty and the feature it enables stays off. A new
  cluster must provision every service before anyone has supplied anything.
- **Write the EnvironmentFile one value per line.** systemd cannot read a value
  that spans lines, so render structured values with `to_json` (see the JWK set
  in `pds.env.j2`). Guard an optional value with `| default('', true)`, not
  `is defined`: a defined-but-empty variable still emits `KEY=`, which many
  services read as a bad value rather than an absent one. Bad operator input
  should fail when the template renders, not when the service starts. Write the
  file mode `0600`; it holds secrets.
- **Harden the unit.** Run as a system user and group, with
  `NoNewPrivileges=true`, `ProtectSystem=strict`, `ProtectHome=true` and
  `PrivateTmp=true`. The daemon's own data directory is its only
  `ReadWritePaths`, and a service that keeps its state elsewhere gets none (see
  `scn-sync-relay.service.j2`). Set `Restart=` with a comment on why that
  value, since the right one depends on how the daemon exits.
- **End with a smoke test** that proves the feature, not just the unit: a
  `wait_for` on the port, then a `uri` against a real endpoint (see the end of
  `sync_relay`'s tasks). systemd reporting "active" is not proof.

## 3. Manifest

The role's **last task**, after the smoke test, records what it installed:

```yaml
# --- Manifest (docs/roles/manifest.md) ----------------------------------------
- name: Record <Service>'s manifest
  ansible.builtin.include_role:
    name: manifest
  vars:
    manifest_version: "{{ <service>_version }}"
```

For a Debian package, read the installed version first (copy the end of
[`redis`](../ansible/roles/redis/tasks/main.yml)):

```yaml
- name: Read the installed <Service> package version
  ansible.builtin.command:
    cmd: "dpkg-query -W -f='${Version}' <package>"
  register: <service>_installed
  changed_when: false
  check_mode: false
```

Then pass `"{{ <service>_installed.stdout }}"` as `manifest_version`.

**Why it must be in the role:** a manifest written from any other play reports
the blueprint's current values instead of what this CT actually got, and hides
drift instead of showing it. See
[ADR-0009](decisions/0009-service-manifests-in-garage.md).

Add `<service>.json` to the key table in [`roles/manifest.md`](roles/manifest.md).
Corliss reads it by that exact name.

## 4. Play

Add a configure play to [`ansible/provision.yml`](../ansible/provision.yml),
after the services it depends on:

```yaml
- name: Configure <Service>
  hosts: <service>
  gather_facts: true
  roles:
    - <role>
```

If the service needs a public route, add it to the
[`proxy`](roles/proxy.md) role's Caddyfile. If it should stay internal, say so
in its role doc, as `sync_relay` does.

## 5. Health check in Corliss

In the [Corliss](https://github.com/Z-Space-Society/Corliss) repo,
`corliss/health.py`:

- Write a probe function that asks the service itself whether it is up, over
  its internal address. Use the service's own liveness endpoint if it has one
  (`_sync_relay`, `_litellm`); if it doesn't speak HTTP, open a socket and read
  its greeting (`_redis`).
- Add a `Probe` row to `STACK` in the right group, with `manifest=` set to the
  same key as step 3.
- Add a probe-target setting (`<SERVICE>_URL`, blank by default, blank meaning
  "unknown") to `settings.py`, `.env.example` and the README, unless an existing
  setting already holds the address. Add tests in `corliss/tests/test_health.py`.

**Why the probe matters even with a manifest:** a manifest records what was
installed, not what is running. A CT rebuilt without replaying its play keeps
its old manifest. The Status column next to the version is the only thing that
tells a reader whether to trust it.

## 6. Point Corliss at it

Back in zai-ops, in the [`corliss`](roles/corliss.md) role:

- Add `corliss_<service>_url` to the "Health probes" section of
  [`defaults/main.yml`](../ansible/roles/corliss/defaults/main.yml), derived from
  `hostvars['<service>'].ansible_host` and left unguarded, like its neighbours.
  The probe URL must never carry a credential.
- Render it in the health-probes block of
  [`corliss.env.j2`](../ansible/roles/corliss/templates/corliss.env.j2).
- Deploy this zai-ops change first, then tag the Corliss release that has the
  new probe. On `latest` (the default) the autodeploy timer picks it up; tagged
  before the env key is rendered, its deploy fails and waits for the next tag.

## A signing key

Skip this unless the service signs tokens with a key of its own: an atproto
OAuth confidential client, an OIDC provider, anything that publishes a JWKS.
The rules are in [ADR-0011](decisions/0011-signing-keys.md); the steps:

1. **Name the key** `<service>_<purpose>`, e.g. `scn_chat_oauth`.
2. **Read it in `group_vars/all/main.yml`**, beside the other signing keys:

   ```yaml
   <service>_<purpose>_keys: "{{ query('signing_keys', '<service>_<purpose>') }}"
   ```

   That is a list, current key first, then the previous key while a rotation
   is under way. The first run generates a P-256 key. Use `query`, not
   `lookup`, which would join the two PEMs into one string.
3. **Register it** in `signing_key_registry` in the same file: the service to
   provision after a rotation, the type, and one sentence on what retiring the
   previous key costs. That entry is what puts it in `scn-config`'s Signing
   Keys menu.
4. **Hand it to the service in the form it reads:**
   - a JSON array of private JWKs (scn-chat's `OAUTH_PRIVATE_KEYS`):
     `{{ <service>_<purpose>_keys | map('private_jwk') | list | to_json }}`
   - a JWK set (the PDS):
     `{{ {'keys': <service>_<purpose>_keys | map('private_jwk') | list} | to_json }}`
   - PEM files (Corliss): write `[0]` to the current path and, only when the
     list has two entries, `[1]` to a previous path, as the corliss role does.

   The service must sign with the first key and keep the second usable: still
   published for a verifier, still used to refresh the sessions that started
   on it for an atproto client. If the app cannot do that, it cannot rotate,
   and that belongs in its role doc.
5. **A key the service already generated for itself is adopted, never
   replaced.** Copy it into `<name>.pem` once, from the role, as the pds role
   does, and read it with `generate=false` so a failed copy stops the play.
   Changing an atproto client's key ends every member's session.

## 7. Docs

In the same change:

- `docs/roles/<role>.md`, from the pattern in the existing role docs (purpose,
  task table with the why, variables, dependencies, verify, notes). Include the
  manifest task.
- A row in the Roles table in [`README.md`](README.md#roles).
- The manifest key from step 3 in [`roles/manifest.md`](roles/manifest.md).
