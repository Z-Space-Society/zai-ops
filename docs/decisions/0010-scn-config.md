# ADR-0010: scn-config, default CTIDs, and set-once assignment

> Status: **Accepted** (2026-10-06).

## Context

Binding services to container IDs took one `zai-assign <service> <ctid>` call
per service, nine in all, copied from the README. The numbers in the README were
the only record of the intended layout, and the blueprint deliberately carried no
container numbers at all ([ADR-0001](0001-repo-stays-generic.md)), so nothing
could propose them.

`zai-assign` also offered `-e reassign=true` as a routine option. A service's
CTID is its container's VMID and its address (`10.1.1.{ctid}`). Changing it
after the service is provisioned moves nothing: the next provision creates a
second, empty container at the new number, the old one keeps running with the
data, and every other service's rendered config still points at the old address.

## Decision

1. **`scn-config` replaces `zai-assign`.** It is one operator command in `bin/`,
   patterned on `raspi-config`: a whiptail menu when run bare, and `scn-config
   nonint <command>` for scripts. It is meant to grow into the single place the
   cluster is configured; today it has two screens, container assignment and
   provisioning (a front end to `provision.yml --limit <service>`, run in
   dependency order). `assign.yml` stays underneath as the engine that validates and writes
   `inventory/local.yml`.
2. **The blueprint carries a suggested `default_ctid` per service.** It follows
   the tier convention and is what `scn-config` pre-fills. Nothing else reads
   it: every playbook still uses only the runtime `ctid`. A default that is
   reserved or already taken on a given cluster is not proposed.
3. **An assignment is set once.** The menu lists only unassigned services and
   shows the rest as locked. `assign.yml` refuses to change a recorded
   assignment unless `reassign=true`, which only
   `scn-config nonint assign-ctid … --reassign` passes, and which is safe only
   before the service has been provisioned.

## Options weighed and rejected

- **Python curses** instead of whiptail. More layout control, but the
  raspi-config look has to be drawn by hand, and whiptail's menu, checklist and
  input box cover what the tool needs.
- **Tier only in the blueprint, numbers picked automatically.** Keeps the
  blueprint free of numbers, but the proposal then depends on ordering and on
  what is free, so two clusters built from the same repo could differ for no
  reason.
- **Reassignment in the menu behind a confirmation.** A confirmation does not
  make the operation safe; it is a migration, and it needs its own procedure.

## Consequences

- A fresh cluster is assigned in one screen, and clusters built from this repo
  share a layout unless the operator chooses otherwise.
- ADR-0001's "no container numbers in the committed tree" becomes "no container
  *assignments*". The defaults are a blueprint constant, like the tier ranges.
- `whiptail` is a control-node dependency, seeded by `bootstrap.sh` and owned by
  the `control_node` role.
- The command is named `scn-config` while the other operator commands are still
  `zai-*`. They are expected to move into its menu over time.
- There is no supported way to renumber a provisioned service. Telling
  "provisioned" from "only assigned" needs a Proxmox API lookup; with that, the
  menu could later unlock services that have no container yet.

## References

- [`docs/README.md`](../README.md#service-ctid-assignment) — Service CTID assignment
- `bin/scn-config`, `ansible/assign.yml`, `ansible/inventory/hosts.yml`
