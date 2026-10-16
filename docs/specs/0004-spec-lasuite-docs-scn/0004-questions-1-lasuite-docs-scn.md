# 0004 — LaSuite Docs on SCN: open questions

Phase-0 decisions to resolve before (or during) implementation. Each has a
recommended default; Boris to confirm. See the spec for framing.

## Q1 — Deployment model inside the docs CT (the big one)

Issue #17 states SCN's architecture is "LXC-per-service on Proxmox, **no
Docker/Podman**", yet Docs upstream only ships container/Nix/YunoHost
installs and the proven reference (VM 118) is docker compose.

- **Option A (recommended for Phase 1):** docker-compose inside the `docs`
  LXC — exact stack shape as VM 118, endpoints re-pointed (Corliss, cluster
  Garage/postgres/redis). Days, proven, low-risk. Recorded as interim in
  ADR-0010; revisit for Phase 2.
- **Option B (issue #17-aligned):** native systemd services in the CT —
  backend venv + gunicorn/celery, y-provider as a node service, frontend as
  static nginx. Most engineering; needs a port of the Dockerfile build steps
  (sharp/node native deps) and per-component env translation.

**Outcome:** ADR-0010 with the chosen model + the Phase-2 trigger.

## Q2 — Databases: reuse cluster postgres/redis, or bundle in-CT?

- **Reuse (recommended):** docs DB as a schema on the `postgres` CT
  (openwebui precedent); redis on the `redis` CT with a dedicated DB index.
  One backup path, fewer moving parts. Docs pins `postgres:16`/`redis:8` —
  confirm cluster postgres version compatibility (16+).
- **Bundle:** matches VM 118 exactly; self-contained CT; but duplicates
  stateful infra and needs its own backup scope.

## Q3 — Who is the first Docs admin / how does bootstrap work?

`ADMIN_EMAIL`/`ADMIN_PASSWORD` from vault today (VM 118 does this). For SCN,
should the admin be bmann's handle, an org role in Corliss, or keep the
vault superuser? (Docs has no CLI user-provisioning flow beyond this.)

## Q4 — Route: public or internal-first?

- **Internal-first (recommended, sync-relay precedent):** docs CT vmbr1-only
  initially; no `caddy_proxy_hosts` row until login/membership is proven.
- **Public:** `{ domain: "docs.{{ cluster_domain }}", service: docs,
  port: 8083 }` from day 1 — members reach it through Corliss-gated login.

## Q5 — Corliss OIDC claim/scope mapping (spike)

VM 118 maps AIP claims (`preferred_username` = handle, `name` = displayName,
scopes `openid profile email atproto transition:email`). Corliss's userinfo
shape and issued scopes may differ (it mints `id_token`s; handles come from
the ATProto login, emails from the PDS). Must be verified before the docs
env is rendered — wrong field names silently produce blank names/avatars.

## Q6 — Email

Docs invite/notify flows want SMTP. SCN has no relay. Options: reuse
forwardemail catchall (as VM 118), dev catcher only, or defer invites
(manual grants) — decide with the deputy/ops posture for member email.

## Q7 — y-provider version

VM 118 runs `lasuite/impress-y-provider:v5.7.0` matching the fork era. As
byoc advances (or we rebase onto newer upstream), does y-provider track the
same tag, or is it pinned independently? (Probably: same tag as the fork
baseline; verify API compatibility on upgrade.)

## Q8 — Backups scope

Confirm the `backup` role covers: docs schema (if on cluster postgres) and
media (Garage bucket `docs` added to the restic/garage backup job). Restore
drill = Phase 4.

## Q9 — Do we keep `scn-member-registry` / membership gating in front of
Docs, or is Corliss membership enough?

Corliss v0.5.0+ refuses non-members at authorize; docs content-ACL is
per-document. Decision: org-wide access at login is sufficient for Phase 1?

## Q10 — Spec numbering / ADR linkage

Confirm `0004` is the right spec number (0001–0003 taken) and that ADR-0010
will record the deployment-model decision when made (0001–0009 taken;
decisions/ numbering continues).

**Recommended resolutions:** A (Phase 1), reuse DBs, bmann admin, internal
route, claims spike first, defer email, same y-provider tag, backup scope
yes, Corliss membership enough, 0004/ADR-0010 yes.