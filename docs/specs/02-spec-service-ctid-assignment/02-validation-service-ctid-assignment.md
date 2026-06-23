# Validation Report — Spec 02: Service CTID Assignment

Spec: [`02-spec-service-ctid-assignment.md`](02-spec-service-ctid-assignment.md) ·
Commit: `e41548e` (2026-06-22) · Supersedes Fable Security Review Issue 2 (CTID
portion).

## 1) Executive Summary

- **Overall: PASS** — all 7 acceptance criteria verified; no gates tripped.
- **Implementation Ready: Yes**, with one scoping caveat: validation is
  **localhost / `--check` only**. The allocator (`assign.yml`), the inventory
  merge, and `provision.yml`'s fail-fast were exercised against a throwaway
  runtime file and the real blueprint; **no `provision.yml` create has run against
  live Proxmox** under the new logical-name flow. The API-create path itself was
  proven separately (Issue 10, CT 199 create/destroy under token auth).
- **Key metrics:**
  - Acceptance criteria verified: **7 / 7 (100%)**
  - Allocator behavior cases passing: **10 / 10**
  - Blueprint hardcoded-number grep: **0 data-level hits** (comments only)

**Gates:** A (no CRITICAL/HIGH open) ✅ · B (no unverified requirement) ✅ · C
(evidence reproducible) ✅ · D (changed files listed/justified — see spec "Files
changed") ✅ · E (repo standards: YAML parses, idempotent, validate-before-done) ✅
· F (no secrets; `local.yml` git-ignored) ✅.

## 2) Coverage Matrix

### Acceptance criteria (from the spec)

| # | Criterion | Status | Evidence |
| --- | --- | --- | --- |
| 1 | Blueprint carries no hardcoded service CTIDs/IPs | Verified | `grep -E 'ct10[0-9]|10.1.1.10[0-9]|ctid:\s*[0-9]'` over `inventory/hosts.yml` + `group_vars/` → only comment/example lines and the control node's genuinely-pinned `.100`; no data-level numbers |
| 2 | `zai-assign nginx 101` → provision resolves nginx to 101 / `10.1.1.101`; no other service touched | Verified | debug play on merged inventory: `nginx ctid=101 ip=10.1.1.101 hostname=nginx net1=...ip=10.1.1.101/24`; `--limit nginx` touches only nginx |
| 3 | `zai-assign litellm 100` fails (reserved) | Verified | assert `(ctid|int) not in reserved_ctids` → `failed=1` |
| 4 | `zai-assign openwebui 101` after nginx=101 fails (collision) | Verified | collision assert → `failed=1` |
| 5 | `zai-assign nginx 101` when already 101 → no-op success | Verified | `is_noop` path → "nothing to do", exit 0, no write |
| 6 | `zai-assign nginx 105 -e reassign=true` moves nginx; other assignments unchanged | Verified | post-run `local.yml`: `nginx.ctid` updated, `litellm`/`open-webui` + `salmon` inference node all intact |
| 7 | With `reserved_ctids:[100..104]` no run can target yvette's live CTs | Verified | reserved assert covers each; fail-fast in `provision.yml` blocks create on unassigned/blocked service |

### Allocator behavior matrix (10/10)

| Case | Expected | Result |
| --- | --- | --- |
| `nginx 101` (clean) | OK | ✔ |
| `litellm 100` (reserved) | FAIL | ✔ |
| `open-webui 101` (collision) | FAIL | ✔ |
| `nginx 101` again (no-op) | OK | ✔ |
| `bogus 110` (unknown service) | FAIL | ✔ |
| `nginx 99` (out of range) | FAIL | ✔ |
| `litellm 105` (ok) | OK | ✔ |
| `nginx 105 reassign` (target held by litellm) | FAIL | ✔ |
| `open-webui 102` (second clean assign) | OK | ✔ |
| `nginx 109 reassign` (free target) | OK + siblings preserved | ✔ |

### Repository standards

| Area | Status | Evidence & notes |
| --- | --- | --- |
| Reproducibility / no committed specifics | Verified | CTIDs + IPs now runtime-only; blueprint is generic. Only `proxmox_node_name` remains committed (tracked as follow-up) |
| No committed secrets / runtime state | Verified | `git check-ignore ansible/inventory/local.yml` → ignored; assignments never committed |
| Idempotency | Verified | re-assigning the same ctid is a clean no-op; read-modify-write preserves siblings + inference roster |
| Validate-before-done (CLAUDE.md) | Verified | `ansible-playbook --syntax-check` clean on assign/provision/site; `ansible-inventory --list` parses; functional runs green (`failed=0` on success paths) |
| Docs maintenance (docs/ tree) | Verified | new "Service CTID assignment" section + Playbooks/Roles tables + TODO in `docs/README.md`; `docs/roles/nginx.md`, `docs/roles/backup.md`, `README.md`, `bootstrap.sh` synced |

## 3) Validation Issues

| Severity | Issue | Impact | Recommendation |
| --- | --- | --- | --- |
| MEDIUM | No live `provision.yml` create under the new flow | Allocator + merge proven, but the first real CT create by logical name is unrun | Run `zai-assign nginx 101` → `provision.yml --limit nginx` on alhambra |
| LOW | `proxmox_node_name` still committed | One this-cluster fact remains in the tree | Move to runtime data (ADR-0001 follow-up) |
| INFO | Postgres placement undecided | Not a blocker; number already unhardcoded | Resolve in ADR-003 |

### Fixed during validation

- **`fail_msg` eager templating.** Ansible finalizes a task's `fail_msg` even when
  the assertion passes; the collision message's `... | map(attribute='key') |
  first` raised "No first item, sequence was empty" on every non-colliding assign.
  Guarded with `| default('another service')`. Caught and fixed before commit;
  re-ran the full 10-case matrix green afterward.

## 4) Reproduce

From `ansible/` on a host with `ansible` installed (a dummy
`ANSIBLE_VAULT_PASSWORD_FILE` suffices — no vault content is decrypted):

```bash
ansible-playbook --syntax-check assign.yml provision.yml      # parse
ansible-inventory --list | jq '.service_containers.hosts'     # number-free blueprint
# allocator matrix against a throwaway runtime file:
ansible-playbook assign.yml -e "runtime_inventory=/tmp/x.yml" -e "service=nginx ctid=101"
ansible-playbook assign.yml -e "runtime_inventory=/tmp/x.yml" -e "service=litellm ctid=100"  # reserved -> fails
```
