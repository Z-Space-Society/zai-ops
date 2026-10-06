# Spec 0004 — LaSuite Docs on the SCN cluster

Status: **Draft** (plan only, nothing deployed)
Branch: `lasuite-docs-scn`
Owner: bmann · Agent: quill · Created: 2026-10-05

## 1. Context and goal

LaSuite **Docs** (`suitenumerique/docs`, the "impress" Django app) is a
verified Digital Public Good and the reference for SCN's shared knowledge
base — "the network's shared knowledge base, pairing with our Garage S3"
(vault `lasuite/docs.md`). We already run it as the destination for Meet
transcripts on the bringyourown.computer cluster.

**Goal:** deploy Docs into the SCN/**ZAI cluster** as a first-class service
(`docs`), following `docs/adding-a-service.md`, adapted from the proven
ovhproxmox install (VM 118) to the SCN LXC-per-service environment.

**Source of truth:** [`bmann/lasuitedocs`](https://github.com/bmann/lasuitedocs),
default branch **`byoc`** — the fork we build the backend and frontend images
from (never float the branch; pin to a commit, see §2).

This spec is the planning deliverable for issue
[Z-Space-Society/zai-ops#17](https://github.com/Z-Space-Society/zai-ops/issues/17)
("revamp st-ansible for SCN architecture") applied to the **docs** slice.

## 2. Source of truth — bmann/lasuitedocs @ byoc

- Fork of `suitenumerique/docs`; **default branch is `byoc`** (bmann). Not a
  release branch: v5.7.0-era code plus BYOC fixes —
  handle-first share UI (OVHP-121+), scoped-search fix (upstream #2589),
  OIDC-picture avatars, nginx static/HTML cache fixes, node-heap OOM cap for
  small guests. Head `1b3e9843` (2026-10-05); ~158 commits behind upstream
  (measured 2026-10-01).
- **y-provider is not fork-built**: official `lasuite/impress-y-provider:v5.7.0`
  image (holds its own env).
- MIT-only build: set `PUBLISH_AS_MIT=true` (drops the GPL "BlockNote XL"
  packages) if we ship a public image.
- **Pin policy:** each role default pins the fork **commit**, not the branch
  (`docs_fork_branch: byoc` + `docs_fork_commit: <sha>`; checkout `force:
  true` bumps deliberately). Track upstream-merge cadence as an ops note, not
  a floating deploy.

## 3. Reference deployment — ovhproxmox VM 118 (what we port from)

Single docker-compose stack on a Debian 13 VM (`docs.bringyourown.computer`),
provisioned by the onedev `ovhproxmox` repo (`playbooks/provision-docs.yml`
+ `roles/docs/`):

| Component | Detail |
|---|---|
| `backend` | built locally from `bmann/lasuitedocs@byoc` (`docker build -t docs-backend:byoc`) |
| `worker` | same local image, celery |
| `frontend` | local image, nginx on `:8083` (mounts a `default.conf.template`) |
| `y-provider` | `lasuite/impress-y-provider:v5.7.0` |
| `postgresql` / `redis` | `postgres:16` / `redis:8`, **bundled** in compose |
| Media | Garage S3 `http://10.0.0.11:3900`, bucket `docs`, path-style, region `garage` |
| OIDC | AIP (`login.bringyourown.computer`), `OIDC_RP_*`; ES256; scopes `openid profile email atproto transition:email`; identity by OIDC `sub` (DID), handle display via `OIDC_USERINFO_SHORTNAME_FIELD=preferred_username`, face via `name` |
| s2s | `DJANGO_SERVER_TO_SERVER_API_TOKENS` — Meet summary posts transcripts to the create-for-owner API |
| Mail | forwardemail catchall relay, per-app `From:`, patched email templates (logo 404 fix) |
| Secrets | controller-side files `~/.config/docs/{garage-credentials,aip-oidc-credentials,aip-rs-credentials,secrets.env}` + `~/.config/forwardemail/catchall` |

Key templates (reference for the role):
`roles/docs/templates/{compose.yaml.j2, env.common.j2, env.backend.j2,
env.yprovider.j2, env.postgresql.j2, default.conf.template.j2,
docs.service.j2}`. Guest is ~3–4 GiB RAM; compose at `/opt/docs/`.

## 4. SCN target environment

zai-ops constraints that shape the port (each is an ADR or a documented
convention):

- **Blueprint-generic repo** (ADR-0001): no this-cluster identity committed.
  CTIDs are runtime (`zai-assign docs <ctid>` → git-ignored
  `inventory/local.yml`); `cluster_domain` is runtime (`zai-set-domain`);
  `ansible_host = 10.1.1.{{ ctid }}` by construction.
- **LXC-per-service**, services as native apps inside CTs; the org's stated
  direction is **no Docker/Podman** (issue #17). Docs has no native-venv
  install path upstream — this is the central decision (§5, Q1).
- **Secrets** in `ansible-vault vault.yml` beside `group_vars/all` — never
  controller-side plaintext files as on ovhproxmox.
- **Identity:** Corliss is the cluster's ATProto→OIDC login bridge
  (ADR-0005/0006): issuer = **apex** `{{ cluster_domain }}`, discovery at
  `/.well-known/openid-configuration`; membership enforced at
  `/oidc/authorize` (corliss v0.5.0+); back-channel logout via the cluster
  Redis CT.
- **Storage:** Garage on the `object-store` CT,
  `http://{{ hostvars['object-store'].ansible_host }}:3900` (same endpoint
  the `manifest` role uses); scoped key pairs per bucket.
- **Provisioning:** `provision.yml` creates CTs from the Proxmox API; the
  `guest` role handles the host side; service configure plays run from the
  control node.
- **Observability:** per-service JSON manifest written to Garage at the end
  of the role (ADR-0009) + a Corliss probe — a service isn't finished until
  `/systems/` can attest up **and** version (adding-a-service.md).
- **Edge:** `proxy` CT (Caddy) routes from `caddy_proxy_hosts` in git.

### 4.1 Mapping (ovhproxmox → SCN)

| ovhproxmox (VM 118) | SCN / zai-ops |
|---|---|
| Debian 13 VM, docker compose at `/opt/docs` | LXC CT **`docs`** under `service_containers` (compose-in-CT for Phase 1, see Q1) |
| bundled `postgres:16` / `redis:8` | reuse cluster **`postgres`** / **`redis`** CTs (recommended; Q2) |
| AIP OIDC (VM 111) | **Corliss** — new OIDC client `docs`; secret via the `corliss_oidc_client_secret` shared-var pattern (cf. `openwebui_oidc_client_secret`) |
| Garage `10.0.0.11:3900`, bucket `docs` | Garage on `object-store` CT `:3900`, bucket **`docs`** + scoped key pair |
| Caddy LXC 112, `docs.bringyourown.computer` | `caddy_proxy_hosts` row `{ domain: "docs.{{ cluster_domain }}", service: docs, port: 8083 }` (public vs internal: Q4) |
| forwardemail SMTP | none yet — mail for invites is an open question (Q6) |
| Meet → Docs s2s token | keep `SERVER_TO_SERVER_API_TOKEN`; future `conversations`/scn-chat → Docs interop (issue #17 P6) |
| controller plaintext secrets | `ansible-vault` `vault.yml` entries (list in §5.4) |

### 4.2 Identity specifics

- Client: `docs_oidc_client_id: docs` (local default, kept in sync with the
  corliss role's client registry — the open-webui precedent);
  `docs_oidc_provider_url: https://{{ cluster_domain }}/.well-known/openid-configuration`;
  secret shared from corliss vars.
- Claim mapping: AIP's `preferred_username` (handle) / `name` (displayName)
  mapping must be re-verified against **Corliss's** userinfo claim names —
  flag for the Phase 1 spike (Q5). Keep handle-first identification:
  `OIDC_FALLBACK_TO_EMAIL_FOR_IDENTIFICATION=False`,
  `OIDC_ALLOW_DUPLICATE_EMAILS=True`.
- Docs needs the OIDC scopes Corliss issues (atproto/transition:email are
  AIP-specific; Corliss scope names = open question, Q5).

### 4.3 Storage specifics

- New bucket `docs` on `object-store` CT + scoped access/secret key (same
  mechanism as `manifest`/`zai-backups` — one key pair per purpose, no key
  with grants on `zai-backups`).
- Env: `AWS_S3_ENDPOINT_URL=http://{{ hostvars['object-store'].ansible_host }}:3900`,
  path-style, region `garage`, `MEDIA_BASE_URL` on the public route.

## 5. Implementation plan (adding-a-service.md checklist)

### 5.1 Inventory + CTID

- `ansible/inventory/hosts.yml`: add `docs` under `service_containers`,
  vmbr1-only (`net0: name=eth0,bridge=vmbr1,ip=10.1.1.{{ ctid }}/24,gw=10.1.1.1`).
- Sizing draft: `ct_cores: 2`, `ct_memory: 4096` (VM 118 runs ~3–4 GiB;
  compose + builds need the headroom), `ct_swap: 1024`, `ct_disk: 24` GB
  (Phase 1 keeps postgres/redis data in-CT if Q2 says bundled; 16 GB if we
  reuse the cluster DBs).
- `zai-assign docs <ctid>` at provision time (runtime, never committed).
- Ordering row in `docs/diagrams.md` after `postgres`/`redis`/`object-store`.

### 5.2 Role — `ansible/roles/docs/`

Model on the ovhproxmox `roles/docs` but consumed per SCN conventions
(native-app role docs pattern from `corliss`/`open-webui`; manifest as last
task; smoke test that proves the feature):

1. Checkout `bmann/lasuitedocs@byoc` pinned to `docs_fork_commit` into
   `/opt/docs/lasuitedocs` (`force: true`; git module).
2. Build backend + frontend images (Phase 1, docker-in-CT) with
   `PUBLISH_AS_MIT=true`; or native build (Phase 2, Q1).
3. Render `compose.yaml` + `env.d/*` + nginx template + systemd unit from
   templates (j2), values from vault + hostvars (postgres/redis are
   `hostvars['postgres'].ansible_host` style — the `openwebui_database_url`
   precedent).
4. Install + start the systemd unit (`docs.service`), recreate on render
   change (env-file hash bug — the VM-118 `--force-recreate` guard).
5. Migrate once (`.migrated` sentinel), ensure the admin superuser (vault:
   `ADMIN_EMAIL`, `ADMIN_PASSWORD`).
6. Smoke: backend container healthy + frontend answers `:8083`; then a real
   HTTP check (Corliss-level liveness probe, see 5.6).
7. **Manifest (last task):** `include_role: manifest` with
   `manifest_service: docs`, `manifest_version: {{ docs_fork_commit }}` →
   `docs.json` in the manifest bucket; add `docs.json` to
   `docs/roles/manifest.md` key table.

### 5.3 Play

Add to `ansible/provision.yml`, after `postgres`, `redis`, `object-store`
(and `corliss` — client must exist before docs starts):

```yaml
- name: Configure Docs
  hosts: docs
  gather_facts: true
  roles:
    - docs
```

Edge route (when decided public, §Q4): one row in
`ansible/roles/proxy/defaults/main.yml` `caddy_proxy_hosts`:
`{ domain: "docs.{{ cluster_domain }}", service: docs, port: 8083 }`.

### 5.4 Secrets — `ansible-vault vault.yml`

`docs_django_secret_key`, `docs_db_password`, `docs_y_provider_api_key`,
`docs_collab_secret`, `docs_admin_email`, `docs_admin_password`,
`docs_server_to_server_token`, `docs_oidc_client_secret` (shared corliss
var), `docs_garage_access_key`, `docs_garage_secret_key` (bucket `docs`).

### 5.5 Docs

`docs/roles/docs.md` (purpose, task table with the why, variables,
dependencies, verify, notes + manifest task), a Roles-table row in
`docs/README.md`, and the manifest key in `roles/manifest.md`.

### 5.6 Corliss probe + health

- `corliss/health.py`: probe the docs backend's own liveness endpoint
  (verify which URL — `manage.py check` healthcheck vs an app endpoint) over
  the internal address; add a `Probe` row with `manifest=docs.json`; add
  `DOCS_URL` setting + tests upstream in the Corliss repo (PR).
- zai-ops `corliss` role: `corliss_docs_url` default (derived from
  `hostvars['docs'].ansible_host`, unguarded, credential-free),
  rendered in `corliss.env.j2`; bump `corliss_version`.

### 5.7 Backup

Ensure docs state is inside the `backup` role's scope: if reusing the
cluster postgres CT → docs schema covered by the PG dump; if bundled PG in
CT (Q2) → add the docs CT's data dir to the restic file list. Media lives
in Garage — covered by object-store backup once bucket docs is added.

## 6. Phases

- **Phase 0 (this branch):** review spec, resolve Q1–Q10, ADR-0010 records
  the deployment-model decision.
- **Phase 1 — MVP (compose-in-CT):** inventory + role + deploy; Corliss
  client; Garage bucket; internal route; manifest + probe; admin smoke.
  Members sign in with their handles; docs reachable at
  `docs.<cluster_domain>` (or internal-only).
- **Phase 2 — converge with issue #17:** if we go native, port the
  stack to systemd units inside the CT (backend venv + gunicorn/celery,
  y-provider node service, frontend static nginx); back-channel logout
  integration; avatars from Corliss claims.
- **Phase 3 — interop:** `conversations`/scn-chat → Docs via the s2s token
  (Meet-style transcript ingestion); email invites when a relay exists.
- **Phase 4 — hardening:** MIT-only image verification, restore drills,
  off-site backups.

## 7. Risks

- **Docker-in-LXC contradicts issue #17's "no Docker/Podman".** Explicit
  ADR-0010, with Phase 2 native port as the convergence path. Compose-in-CT
  is staged as speed-to-value, not a permanent architecture (Q1).
- **Fork drift:** byoc floats ~158 behind upstream; pin commits; bookkeeping
  cadence in the role doc.
- **OIDC claim mismatch (Corliss vs AIP)** — spike first (Q5).
- **Build resources on the node:** backend sharp/node build memory (the fork
  caps node heap for small guests); size the CT accordingly; AVX2 present so
  no sharp stub needed.
- **Health honesty:** manifest records what was installed, not what runs —
  the Corliss probe is the liveness truth (ADR-0009 rationale).

## 8. References

- Source fork: https://github.com/bmann/lasuitedocs (default branch `byoc`)
- Upstream app: https://github.com/suitenumerique/docs
- Reference deploy: onedev ovhproxmox repo `roles/docs/` + `docs-staging.md`
- Parent initiative: zai-ops#17 (st-ansible revamp, SCN LXC model)
- Conventions: docs/adding-a-service.md · ADR-0001, -0005, -0006, -0009
- Vault: `lasuite/docs.md`, `lasuite-meet-architecture.canvas`