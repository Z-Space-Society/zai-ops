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
- Assign it a container ID on each cluster: `zai-assign <service> <ctid>`. The
  number is runtime data in the git-ignored `inventory/local.yml` and is never
  committed. See [Service CTID assignment](README.md#service-ctid-assignment).
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
    manifest_service: <service>
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
- Bump `corliss_version` to the release that has the new probe.

## 7. Docs

In the same change:

- `docs/roles/<role>.md`, from the pattern in the existing role docs (purpose,
  task table with the why, variables, dependencies, verify, notes). Include the
  manifest task.
- A row in the Roles table in [`README.md`](README.md#roles).
- The manifest key from step 3 in [`roles/manifest.md`](roles/manifest.md).
