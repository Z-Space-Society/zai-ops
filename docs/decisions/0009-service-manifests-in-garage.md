# ADR-0009: Service manifests live in Garage

> Status: **Accepted** (2026-09-16). Tracks
> [#6](https://github.com/Z-Space-Society/zai-ops/issues/6).

## Context

Corliss's `/systems/` page reports whether each service is up. It should also
report which zai-ops revision provisioned each service and which version that
service runs, so a service that has not been replayed since the blueprint
changed stands out.

Ansible knows both values at the moment it provisions a service. The problem is
delivery: getting a per-service record from the play that installed the service
to the page that displays it.

Three constraints shape the answer.

1. **The record has to come from the play that did the install.** A manifest
   rendered from any other play, such as a single file written by the corliss
   role, sees today's role defaults rather than what each CT actually got. It
   would report a July install at August's version and give every row the same
   revision. It would not just miss drift, it would hide it.
2. **Several services cannot serve anything themselves.** Redis speaks RESP, and
   Garage's admin API is loopback-only. Most of the rest are upstream software
   we do not control.
3. **The record must survive the incidents it exists to help with.** A proxy CT
   rebuild or a Redis restart should not blank the page.

Options weighed and rejected:

- **A manifest rendered onto the corliss CT.** Fails constraint 1.
- **Each service serves its own manifest.** Fails constraint 2 for two services,
  and needs a static file server per CT, which is itself unpinned software.
- **Manifests written to the proxy CT, served by Caddy.** Makes the edge a
  metadata store, and a proxy rebuild blanks every row until every play replays.
- **A table in the shared Postgres.** Gives Ansible a write path into the
  application database, a new direction of dependency.
- **Redis.** It is deliberately non-persistent, and that is what keeps it out of
  the backup set.

## Decision

- **Each service play writes `<service>.json` into a dedicated Garage bucket,
  `zai-manifests`, as its last task.** The shared
  [`manifest`](../roles/manifest.md) role does the write with
  `amazon.aws.s3_object`, delegated to the control node. Each calling role
  passes its service name and the version it installed:
  - distro packages (Caddy, PostgreSQL, Redis) are read back with `dpkg-query`,
    so a Debian point release shows up as drift without failing a replay
  - software fetched by tag reports its pinned variable

  The manifest shape:

  ```json
  {"service": "redis", "version": "5:8.0.2-1", "zai_ops": "v0.6.3-2-g00db3dd", "provisioned_at": "2026-09-16T14:22:07Z"}
  ```

- **The revision is `git describe --tags --always --dirty` of CT 100's
  checkout.** A `-dirty` suffix means the blueprint was hand-edited on the
  control node, and the page should show that rather than hide it.

- **Two scoped keys, both generated like every other secret.**
  `zai-manifest-writer` (read and write) is used only by the manifest role, and
  `zai-manifest-reader` (read only) only by Corliss. Neither has any grant on
  `zai-backups`, and the backup key never appears in a service play.

- **Reads are signed.** Garage has no anonymous access on its S3 API. Its
  website endpoint could serve unsigned reads, but only through a second
  listener and Host-header bucket routing, added purely to avoid a credential.
  Corliss holds a read-only key and uses boto3 on the S3 port it already probes.

- **Report, never raise.** A failed manifest write logs a warning and the
  service play carries on. A metadata write must not fail a service deploy. On
  a `--limit` run on a cluster whose object-store has never been provisioned,
  the write has nowhere to go and fills in on the next replay. (`provision.yml`
  runs object-store first for this reason; until v0.7.0 had deployed, the proxy
  play ran ahead of it and Caddy's first write on Heron warned.)

- **Manifests are not backed up.** A replay rebuilds every one of them, so they
  hold nothing unreproducible.

## Consequences

- A Garage outage blanks the version columns on `/systems/`. Statuses still
  render, so the page degrades to what it showed before this change.
- A manifest can outlive its subject. A CT rebuilt without replaying its play
  leaves the old manifest in place. The liveness probes are what contain that:
  a `Down` beside a version tells the reader not to trust the version. **The
  probes are load-bearing for this feature**, and weakening them would turn the
  version columns into claims nothing checks.
- Adding a service now means adding a manifest step and a Corliss probe as well
  as a role. [Adding a service](../adding-a-service.md) is the checklist.
- The control node gains `python3-boto3`. The `amazon.aws` collection already
  ships in Debian 13's ansible bundle.
