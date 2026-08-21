# Role: `manage_console`

Builds and serves the **SCN admin console** — the surface where an admin
approves applications, grants and revokes membership, sets tiers, and manages
the roster — at `manage.<cluster_domain>`.

- **Source:** [`ansible/roles/manage_console/`](../../ansible/roles/manage_console/)
- **Applied by:** [`provision.yml`](../../ansible/provision.yml) (its own play, `hosts: proxy`)
  — a placement that is **deliberately temporary**; see
  [Placement, and how to remove this cleanly](#placement-and-how-to-remove-this-cleanly)
- **Target:** the `proxy` service CT, over SSH — with every build task delegated
  to the control node (CT 100)
- **Upstream app:** [Z-Space-Society/member-registry](https://github.com/Z-Space-Society/member-registry)
  at a pinned tag

Named for the **surface**, not the repo: the app lives in `member-registry`
(named `scn-ops` until the repo rename), but the thing an operator points a
browser at is "the manage console" either way.

## Purpose

Two things make this role shaped unlike every other app role here.

**It has no container.** The console is a pre-built browser bundle that talks to
[HappyView](happyview.md) directly from the browser — there is no server behind
it, so a whole CT to run a file server would be the wrong shape. Caddy's
`file_server` serves it, which is core Caddy function, so the role targets the
proxy CT and [`proxy`](proxy.md)'s `caddy_proxy_hosts` carries a **static
`root:` entry** for it instead of a `service`/`port` pair.

Hosting it on the [`corliss`](corliss.md) CT instead would keep the browser
origin separate but re-entangle the two deploys and widen `ALLOWED_HOSTS` /
`CSRF_TRUSTED_ORIGINS` on the exact app the origin split exists to hold at arm's
length.

**It builds somewhere other than where it runs.** npm, a `node_modules` tree and
a TypeScript toolchain have no business on the only LAN-facing CT in the
cluster. CT 100 already has git and is where Ansible runs from, so it clones and
builds, and Ansible copies the emitted bundle across. The edge CT keeps exactly
the static files and nothing that produced them.

**The console holds no credential.** Its write credential is the *admin's own*
atproto session, obtained in the browser at sign-in — which is what lets it be a
static bundle, and what keeps grants admin-authored (see
[`Cluster Access Use Cases`]). Nothing in the bundle is a secret.

[`Cluster Access Use Cases`]: https://github.com/Z-Space-Society/member-registry

## Tasks

| Task | Module | Why |
| ---- | ------ | --- |
| Require `cluster_domain` | `assert` | The OAuth client id and redirect URI are derived from it. Fails naming `zai-set-domain` rather than surfacing later as a broken consent screen. |
| Require the HappyView client key | `assert` | Fail closed and name the fix (`zai-set-console client_key …`); without it the console cannot reach HappyView at all. |
| Install the node toolchain | `apt` (delegated to CT 100) | `nodejs` + `npm` from Debian's own repos — no third-party repo, same posture as Caddy. |
| Require a node version vite can build under | `assert` (delegated) | Vite 8 needs `^20.19 \|\| >=22.12`; Debian 13 ships 20.19.x, clearing it by a hair. Without the assert the failure is an npm error deep in a build log instead of a sentence naming the cause. |
| Clone the source at the pinned tag | `git` (delegated) | Full clone, **no `depth:`** — a shallow clone breaks `git describe --tags`, and the checked-out SHA stamps the deployed bundle. |
| Render the build-time config | `template` → `.env` (delegated) | Vite inlines `VITE_*` at build time; `prebuild` reads the same file to generate `public/client-metadata.json`. Gitignored upstream, so it survives the clone's `force`. |
| Install dependencies | `command: npm ci` (delegated) | `ci` (not `install`) installs exactly `package-lock.json` and fails if lock and manifest disagree — the reproducibility guarantee the pinned tag is meant to give. Guarded on the clone changing or `node_modules` being absent. |
| Build the console | `command: npm run build` (delegated) | Guarded on the source ref or the inlined config moving, so a replay isn't perpetually `changed`. |
| Assert the build emitted client metadata | `stat` + `failed_when` (delegated) | **The load-bearing check** — see the trap below. |
| Remove the previous bundle when the build changed | `file: state=absent` | Vite emits content-hashed asset names, so a plain copy never overwrites the old ones. Wiping on a real change also makes an upstream-deleted file actually disappear. |
| Create the console root | `file: state=directory` | Owned by `caddy`. |
| Install the built bundle | `copy` (directory, trailing slash) | `src` is read from the *controller's* filesystem — which is the control node, the same host that just built it — so this needs no rsync and no intermediate tarball. |
| Stamp the deployed build id | `copy` (`content:`) | Written **last**, so an interrupted deploy leaves a stale id and the next run redeploys rather than trusting a half-copied tree. |
| Verify the served client metadata | `stat` + `failed_when` | The same assertion against what Caddy will actually serve — catches a copy that dropped the file. |

### The build id

The bundle is a function of **both** the source SHA and the inlined config, so
the stamp at `<root>/.build-id` is `<git sha>-<.env checksum>`. A config-only
change (a rotated client key, say) leaves the SHA identical while producing
genuinely different files; keying on the SHA alone would silently skip the
redeploy.

## Variables

| Variable | Default | Notes |
| -------- | ------- | ----- |
| `manage_console_repo_url` | `…/member-registry.git` | Public repo; the clone needs no credentials (CT 100 reaches GitHub over the host's NAT). |
| `manage_console_version` | `v0.1.0` | **A tag, never a branch** — same rule as `corliss_version`. Bumping it is a reviewable commit. |
| `manage_console_src` | `/opt/manage-console-src` | On the **control** node, never the proxy. |
| `manage_console_build_host` | `groups['control_node'][0]` | Resolved from the group so the blueprint survives a rename. |
| `manage_console_root` | `/srv/manage-console` | In [`group_vars/all/main.yml`](../../ansible/group_vars/all/main.yml), not role defaults: the `proxy` role's Caddy route needs the same string and role defaults aren't visible across roles. |
| `manage_console_happyview_url` | `https://view.{{ cluster_domain }}` | Derived — no separate setting to drift. |
| `manage_console_client_id` | `https://manage.{{ cluster_domain }}/client-metadata.json` | The client id **is a URL**, fetched by the member's PDS. |
| `manage_console_redirect_uri` | `https://manage.{{ cluster_domain }}/oauth/callback` | Must match the registered client. |
| `manage_console_scope` | `atproto transition:email repo:…` | Must match the **registered** HappyView client's scopes exactly or sign-in fails at the consent screen. |
| `console_client_key` | `""` | Set with `zai-set-console client_key`. |
| `scn_service_did` | `""` | Set with `zai-set-console service_did`. |
| `scn_registry_space_uri` | `""` | Set with `zai-set-console registry_space_uri`; optional. |

### Why the last three are inventory, not vault

**None of them is a secret.** All three ship inside the browser bundle or are
published at a public URL — the `hvc_` client key is *origin-bound*, which is
what carries the security, not its secrecy. They are this-cluster **identity**
(which HappyView client, which registry DID), the same category as
`cluster_domain` and the CTID assignments, so they live in the git-ignored
runtime inventory and the committed tree stays generic (ADR-0001). Putting a
non-secret in the vault is the mistake this repo's conventions call out by name.

`scn_service_did` and `scn_registry_space_uri` are deliberately **not** prefixed
`manage_console_*`: the service DID is the *registry's* identity, needed
identically by [`corliss`](corliss.md) for its roster read (`SCN_SERVICE_DID`),
and one identity should not be recorded twice under two names in the same file.

## Placement, and how to remove this cleanly

**This console is expected to be stripped out**, whether because the admin
surface moves into Corliss as Django views or because `member-registry` is
replaced. The placement below was chosen for speed of landing it, not because
it is the right long-term shape — recorded here so the decision is visible
rather than archaeology, and so the removal is a checklist rather than a hunt.

> [!important] Decided 2026-08-18 — the first of those two reasons happened
> Corliss now serves its own console at `/manage/`, and it **supersedes this
> one**. So this section is no longer hypothetical: it is the plan, waiting on
> one thing.
>
> What has moved: the member roll, the admin roster, and reconciliation. What
> has **not**: the write surface — approve, revoke, set tier, roster edits — which
> is still only here. Those are *writes to the registry space* and must keep
> requiring a current-admin caller, so they need per-admin HappyView auth in
> Corliss rather than the read-only service token reconciliation uses. **Do not
> remove this role until that lands**, or admins lose the ability to approve
> anyone.
>
> One coupling already broken in advance: corliss's reconciliation deliberately
> does **not** read `manage_console_happyview_url`, even though the value is
> identical. It carries its own `corliss_membership_registry_url` so that
> deleting this role cannot break the cache rebuild. `scn_service_did` and
> `console_client_key` are still genuinely shared — see the warning below.

### What was done, and the alternative that was preferred

The role is attached as **a second play inside
[`provision.yml`](../../ansible/provision.yml)** (`hosts: proxy`), immediately
after the `proxy` play. That reuses the existing create-then-configure ordering
and makes `--limit proxy` deploy the edge and the console together.

The alternative — **a self-contained `manage.yml` playbook** — is the cleaner
shape and was not built. It would make the console independently deployable
(`ansible-playbook manage.yml`), keep an unrelated concern out of the cluster's
core provisioning path, and make removal a single file deletion instead of the
list below.

What the current placement costs:

- **The console is not independently deployable.** There is no way to redeploy
  it without going through `provision.yml`, and `--limit proxy` always couples
  it to the edge — a console build failure and a Caddy failure arrive through
  the same command. (The separate *play* limits the blast radius: a broken
  console build does not stop the `proxy` play from having already run. It does
  not make the two separately *invocable*.)
- **It is not discoverable.** Someone asking "how do I deploy the admin
  console?" has to already know it lives inside the provisioning playbook.
- **It puts an application concern in the base-system path**, which is the line
  this repo otherwise holds.

If this outlives the next cleanup, promoting it to `manage.yml` is mechanical:
move the play body out of `provision.yml`, add a row to the Playbooks table in
[`docs/README.md`](../README.md#playbooks), and it is done — nothing in the role
itself assumes where it is invoked from.

### Removal checklist

Delete outright:

| Path | Note |
| ---- | ---- |
| [`ansible/roles/manage_console/`](../../ansible/roles/manage_console/) | The whole role |
| [`ansible/set-console.yml`](../../ansible/set-console.yml) | The setter playbook |
| [`bin/zai-set-console`](../../bin/zai-set-console) | The operator command |
| `docs/roles/manage_console.md` | This file |

Edit out:

| File | What |
| ---- | ---- |
| [`ansible/provision.yml`](../../ansible/provision.yml) | The `Configure the SCN admin console` play |
| [`ansible/roles/proxy/defaults/main.yml`](../../ansible/roles/proxy/defaults/main.yml) | The `manage.{{ cluster_domain }}` entry in `caddy_proxy_hosts` |
| [`ansible/group_vars/all/main.yml`](../../ansible/group_vars/all/main.yml) | `manage_console_root` |
| [`docs/README.md`](../README.md) | Roles-table row, Playbooks-table row, operator-command row, and the `try_files` gotcha |

Two things that are **not** simple deletions:

> [!warning] `scn_service_did` **and `console_client_key`** are shared with
> corliss — don't delete either blindly
> `scn_service_did` is deliberately unprefixed because it is the *registry's*
> identity, not the console's: corliss needs the identical value for its ELEVATE
> roster read (`SCN_SERVICE_DID`).
>
> `console_client_key` became shared on 2026-08-18: corliss passes the same
> public, origin-bound HappyView key as `MEMBERSHIP_REGISTRY_CLIENT_KEY` for its
> reconciliation reads. Reused rather than copied under a second name, on the
> same principle `set-console.yml` records — one identity should not appear twice
> in one file. The cost is a `console_*` name outliving the console, which is a
> rename to make **after** this role is gone, alongside the `SCN_SERVICE_DID` →
> `ADMIN_ROSTER_DID` rename deferred for the same reason. Both are naming, not
> behaviour; neither is urgent, and doing them while two consumers still exist is
> what makes them expensive.
>
> So: removing the console must not remove *either* variable from the runtime
> inventory, and if `zai-set-console` goes away, whatever replaces it still has
> to be able to set both. `scn_registry_space_uri` *is* console-only (the
> registry's Lua carries its own copy in the HappyView script env) and can go
> with it.

> [!note] The Caddy `root:` support is generic — keep or drop on its own merits
> The `{% if h.root is defined %}` branch in
> [`Caddyfile.j2`](../../ansible/roles/proxy/templates/Caddyfile.j2) is not
> console-specific: it is static-file serving for any route, and it was a
> separate commit for that reason. It becomes dead code when the last `root:`
> entry goes, but it is correct and inert, so deleting it is a tidiness call
> rather than part of this removal.

Also clean up on the hosts themselves (nothing in git tracks these):
`/opt/manage-console-src` on CT 100, `/srv/manage-console` on the proxy CT, and
the `manage.` DNS record.

## Dependencies

- [`proxy`](proxy.md) — supplies the Caddy `root:`/`file_server` route. Runs in
  the play immediately before this one, on the same CT.
- The **control node** must be reachable as an Ansible host (it always is — it's
  where Ansible runs), because every build task is delegated to it.
- Nothing else. In particular **not** `happyview`: the console talks to it from
  the *browser* at runtime, so HappyView being down doesn't block a deploy.

## Verify

```bash
# The bundle is served, and — the one that matters — the client metadata is JSON
curl -fsS https://manage.<domain>/ | head -5
curl -fsS https://manage.<domain>/client-metadata.json | jq .client_id

# What's actually deployed
ssh proxy 'cat /srv/manage-console/.build-id'
```

The `jq` line is the real smoke test: if it prints HTML-flavoured garbage
instead of a URL, you have hit the `try_files` trap below.

## Notes

> [!warning] The `try_files` trap — a missing file serves `index.html`
> Caddy's SPA fallback (`try_files {path} /index.html`) is load-bearing: the
> console's OAuth callback lands on `/oauth/callback`, which has no file behind
> it, and without the fallback sign-in fails **silently** — the token exchange
> has already succeeded, so the only symptom is a user who never lands.
>
> The same fallback is a trap in the other direction. With
> `VITE_OAUTH_CLIENT_ID` unset, `scripts/client-metadata.mjs` prints a note and
> **exits 0** — the build succeeds and the file is simply absent. `try_files`
> then serves `index.html` at the client-id URL, the member's PDS gets HTML
> where it expects JSON, and sign-in dies at the consent screen with nothing in
> any log pointing here. That is why the role asserts the file exists on **both**
> sides of the copy rather than trusting the build's exit code.

> [!note] `transition:email` is dead weight, but can't be dropped alone
> Nothing has read it since the registry's LiteLLM strip. Removing it from
> `manage_console_scope` **alone** would break the match against the registered
> HappyView client and fail every sign-in at the consent screen — it has to come
> out of the registered client and this default in the same change.

> [!note] Why build-time config and not a runtime `/config.json`
> Vite inlines `VITE_*` into the bundle, which would matter if a **prebuilt**
> artifact were ever distributed across clusters. It isn't: this role builds
> per-cluster from source, so the bundle never has to be cluster-independent and
> the extra fetch-at-boot moving part (plus its own "reject non-JSON responses"
> guard against the same `try_files` trap) earns nothing.
