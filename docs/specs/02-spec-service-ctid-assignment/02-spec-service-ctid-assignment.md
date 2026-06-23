# 02-spec-service-ctid-assignment.md

> Status: **Implemented** (as-built record, committed `e41548e` 2026-06-22).
> Supersedes: the CTID-as-variable / `reserved_ctids` portion of
> [Fable Security Review](../../../README.md) Issue 2.
> Out of scope: Postgres placement (its own decision — ADR-003, unwritten).
> Validation: [`02-validation-service-ctid-assignment.md`](02-validation-service-ctid-assignment.md).

This is the as-built spec: the original proposal lightly reconciled to what
actually shipped. Where the implementation diverged from the proposal, the
divergence is called out inline (**As-built:**).

## Introduction/Overview

The committed inventory used to weld a container's identity to its number: a host
named `ct101-nginx` *was* CT 101 at `10.1.1.101`. That makes the repo
non-reproducible against any host whose 100–104 are already taken — e.g. yvette,
where those CTIDs are live (nginxproxymanager, oikb, Boris's atlogin, litellm-gw)
and must not be stomped.

This spec replaces the welded inventory with a **runtime allocator**. The operator
assigns a service to a container ID once:

```
zai-assign nginx 104
```

…and from then on `nginx` is authoritatively CT 104 at `10.1.1.104` for every
playbook, cluster-wide. The assignment is persisted in a git-ignored runtime file;
the committed blueprint contains **no container numbers**. This unblocks
`provision.yml` against brownfield hosts and makes the COAI "any Steward stands up
a cluster on whatever CTIDs are free" story real — no hand-edited inventory.

## Goals

- The committed blueprint (`inventory/hosts.yml`) carries **no container numbers**;
  services are identified by **logical name** (`nginx`, `litellm`, …), never a CTID.
- An operator binds a service to a CTID once (`zai-assign <service> <ctid>`); the
  binding is per-cluster runtime data, git-ignored, machine-written.
- A service's IP is **derived** from its CTID (`10.1.1.{ctid}`), never stored — no
  second field to drift.
- The allocator refuses to assign over a `reserved_ctids` safety list (live or
  externally-managed containers).
- `provision.yml` consumes the assignment by logical name and **fails fast** on an
  unassigned service rather than inventing a number.

## Design

Separate two concerns previously tangled in `inventory/hosts.yml`:

- **Blueprint** (committed, generic, no numbers): which services exist, what role
  each runs, resource sizing. Lives in `hosts.yml`.
- **Assignment** (runtime, per-cluster, git-ignored, machine-written): which CTID
  each service got. Lives in `inventory/local.yml`.

Ansible loads both because the inventory is a directory (`inventory/`), so
`hosts.yml` + `local.yml` merge automatically.

**As-built — `local.yml` is shared.** The proposal sketched a bare top-level
`service_containers:` mapping. In reality `inventory/local.yml` already exists and
is git-ignored — it holds the bare-metal inference-node roster written by
`enroll-inference-node.yml` (`all.children.inference_nodes.hosts.*`). So service
assignments merge into the **same file** under
`all.children.service_containers.hosts.<name>.ctid`, and the allocator must
preserve the inference roster on every write. This matches the repo's actual
inventory structure (groups under `all.children`) rather than the proposal's
illustrative shorthand.

### Data model

`inventory/local.yml` (git-ignored) holds only the per-service CTID:

```yaml
all:
  children:
    service_containers:
      hosts:
        nginx:
          ctid: 104
    inference_nodes:        # written separately by enroll-inference-node.yml
      hosts:
        salmon: { ansible_host: 192.168.6.63, ansible_user: ansible }
```

IP and OS hostname are derived in group_vars, not stored per host:

```yaml
# group_vars/service_containers.yml  (new)
ansible_host: "10.1.1.{{ ctid }}"
ct_hostname:  "{{ inventory_hostname }}"
```

So CTID 104 ⇒ `10.1.1.104` everywhere, by construction; the container's hostname
is just its logical name.

**Control-node exception.** The control node's internal IP is pinned to
`10.1.1.100` by `bootstrap.sh` regardless of its CTID (199 on yvette). The
`10.1.1.{ctid}` convention applies to **service containers only**. The control
node carries no `ctid` and is never assigned. **As-built:** its inventory host was
also renamed `ct100` → `control` to drop the misleading number.

### Logical names

**As-built:** the proposal's Part A sketched role-named groups
(`reverse_proxy`/`database`/`gateway`/`frontend`, each containing one host). The
implementation kept the existing one-host-per-service shape and simply **stripped
the `ctNNN-` prefix**: `ct101-nginx` → `nginx`, `ct102-postgres` → `postgres`,
`ct103-litellm` → `litellm`, `ct104-open-webui` → `open-webui`, `object-store`
unchanged. Lighter, matches the existing role-per-host convention, and aligns with
the "name by function, not number" preference. Unspecced services (`postgres`,
`litellm`, `open-webui`) remain blueprint placeholders with no `ct_netif`, which
`provision.yml` skips.

### `reserved_ctids`

A per-cluster list the allocator refuses to assign over. Lives in
`group_vars/all/main.yml`:

```yaml
reserved_ctids: [100]          # default: just the control node
```

**As-built:** a flat list of integers, not the `{ctid, owner, purpose}` dicts the
proposal sketched — simpler to check and to widen. On yvette it would be set to
`[100, 101, 102, 103, 104, 199]` (eventual control-node target + live services +
the current control node) before any assign/provision points at it.

## The assign action (the engine)

New playbook `ansible/assign.yml`, invoked:

```
ansible-playbook assign.yml -e "service=nginx ctid=104"
ansible-playbook assign.yml -e "service=nginx ctid=104 reassign=true"
```

Behavior, in order:

1. **Validate** (fail with a clear message on any): `ctid` is an integer in
   100–999; not in `reserved_ctids`; not already held by a *different* service;
   `service` exists in the blueprint (`groups['service_containers']`); `service`
   does not already have an assignment unless `reassign=true`.
2. **Idempotent no-op:** assigning a service the CTID it already has exits 0.
3. **Read-modify-write** the whole `local.yml` structure and re-dump (load → load
   existing → `combine(recursive=True)` the new entry → `to_nice_yaml` via `copy`),
   preserving every other service's assignment **and** the inference roster. Never
   line-append. This mirrors the proven pattern in `enroll-inference-node.yml`.

## The CLI (thin wrapper)

`/usr/local/bin/zai-assign`, installed on the control node by the `control_node`
role. Sugar over the playbook — no logic lives here:

```bash
#!/usr/bin/env bash
set -euo pipefail
if [[ $# -lt 2 ]]; then
  echo "usage: zai-assign <service> <ctid> [extra ansible args]" >&2
  exit 64
fi
service="$1"; ctid="$2"; shift 2
exec ansible-playbook /opt/zai-ops/ansible/assign.yml \
  -e "service=$service ctid=$ctid" "$@"
```

So `zai-assign nginx 104` and `zai-assign nginx 105 -e reassign=true` both work.

## Consumption changes

- `provision.yml` create play targets the `service_containers` group; creates the
  CT with `vmid: "{{ ctid }}"` and netif `ip=10.1.1.{{ ctid }}/24`. No name parsing.
  Invocation becomes `ansible-playbook provision.yml --limit nginx`.
- A pre-task asserts `ctid is defined` (gated on `ct_netif is defined`) so an
  unassigned-but-specced service fails fast with
  "run: zai-assign <service> <ctid>".
- The configure play that applied the nginx role moved from `hosts: ct101-nginx`
  to `hosts: nginx`.
- `roles/nginx/defaults/main.yml` upstream example now references
  `hostvars['open-webui'].ansible_host`, not a pinned `10.1.1.104`.

## Files changed

- `ansible/inventory/hosts.yml` — number-free blueprint; `services` →
  `service_containers`; `ct100` → `control`.
- `ansible/inventory/local.yml` — runtime assignments (git-ignored; confirmed).
- `ansible/group_vars/all/main.yml` — `reserved_ctids: [100]`.
- `ansible/group_vars/service_containers.yml` — **new**; derived `ansible_host` +
  `ct_hostname`.
- `ansible/assign.yml` — **new** allocator.
- `ansible/provision.yml` — consume `ctid`; logical-name hosts; fail-fast assert.
- `ansible/roles/control_node/` — install `zai-assign`.
- Docs: `docs/README.md`, `docs/roles/nginx.md`, `docs/roles/backup.md`,
  `README.md`, `bootstrap.sh`.

## Acceptance criteria

1. `grep` for hardcoded `ct10[0-9]` / literal service CTIDs in committed files
   returns nothing in the blueprint (numbers exist only in git-ignored `local.yml`).
2. On clean alhambra: `zai-assign nginx 101` then `provision.yml --limit nginx
   --check` resolves nginx to CTID 101 / `10.1.1.101`; no other service touched.
3. `zai-assign litellm 100` fails (reserved).
4. `zai-assign openwebui 101` (after nginx has 101) fails (collision).
5. `zai-assign nginx 101` when nginx already has 101 succeeds as a no-op.
6. `zai-assign nginx 105 -e reassign=true` moves nginx; `local.yml` still contains
   every other service's assignment unchanged.
7. With `reserved_ctids: [100,101,102,103,104]` set, no assign or provision run can
   target yvette's live containers.

See [`02-validation-service-ctid-assignment.md`](02-validation-service-ctid-assignment.md)
for the evidence matrix.

## Security / safety considerations

- The allocator's only authority is writing `inventory/local.yml`; it creates no
  containers. `reserved_ctids` is the hard rail against stomping live or
  externally-managed CTs, enforced both at assign time and (via fail-fast) before
  any `provision.yml` create.
- Validate on alhambra (greenfield) with `--check` before any `provision.yml` run
  touches yvette. yvette stays brownfield-frozen until its `reserved_ctids` is set
  and the allocator is proven on alhambra.

## Open questions / follow-ups

1. **Postgres placement (ADR-003, unwritten)** — own CT vs co-located in LiteLLM.
   Deliberately out of scope here; this spec left Postgres wherever it sits and
   only unhardcoded its number.
2. **Arm yvette** — set `reserved_ctids: [100,101,102,103,104,199]` and re-prove the
   allocator there before pointing any run at it.
3. **`proxmox_node_name`** — the last this-cluster fact still in the committed tree;
   move it to runtime data to finish the ADR-0001 generic/runtime split.
4. **First real `provision.yml` run on alhambra** — allocator logic is proven
   (localhost/`--check`); the token-create path was proven separately (Issue 10).
   The create has not yet been run end-to-end under the new logical-name flow.
