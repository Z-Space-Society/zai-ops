# Role: `manifest`

Writes one service's manifest into Garage: which version the play just
installed, which zai-ops revision installed it, and when. Corliss's `/systems/`
reads these. See [ADR-0009](../decisions/0009-service-manifests-in-garage.md).

- **Source:** [`ansible/roles/manifest/`](../../ansible/roles/manifest/)
- **Applied by:** every service role, as its last task, via `include_role`. It
  is not listed in any playbook.
- **Target:** the calling play's host for the assert. The write is delegated to
  CT 100 (`delegate_to: localhost`), which reaches Garage over `vmbr1`.

## Purpose

This is the first shared role in the repo. The write lives inside the play that
installs the service on purpose: rendered from any other play, it would report
the blueprint's current values rather than what this CT actually got, which
hides drift instead of showing it.

## Calling it

```yaml
- name: Record Redis's manifest
  ansible.builtin.include_role:
    name: manifest
  vars:
    manifest_version: "{{ redis_installed.stdout }}"
```

Put it **after** the role's smoke test, so a service that fails its smoke test
never claims a version.

## Tasks

| Task | Module | Why |
| ---- | ------ | --- |
| Assert the caller passed a version | `ansible.builtin.assert` | A missing version is a bug in the calling role, so this one raises. |
| Write the manifest to Garage | `amazon.aws.s3_object` (`mode: put`, delegated to localhost) | `permission: []` and `encrypt: false` because Garage has no ACLs or SSE-S3 and the module turns both on by default. `overwrite: always` because every manifest carries a fresh timestamp. `failed_when: false`: a metadata write must never fail a service deploy. Skipped when `object-store` has no CTID. Under `--check` the module reports without writing. |
| Hand the installed version to the deploy log | `ansible.builtin.set_stats` (`per_host`) | The [`deploy_log` callback](#deploy-records) puts the version in its record from here. Set whether or not the write landed: the version is what the CT got either way. |
| Warn that the manifest was not written | `ansible.builtin.debug` | Makes a skipped or failed write visible in the run output without failing the play. Prints the module's `msg`, or its whole result (minus `invocation`) when there is none: Heron's first v0.7.0 run failed a write with no `msg`, and the warning said nothing useful. |

## Manifest keys

**The key is the inventory name**, `<inventory_hostname>.json`, and no caller
passes one. The inventory name is how every part of zai-ops refers to a service:
manifests, deploy records, `--limit`. Corliss keys its `/systems/` rows the same
way and maps each to a display name (Garage, Caddy), so a display name never
leaks back into a key. Renaming an inventory host renames its manifest, which
Corliss reads, so do both sides in the same change.

Until v0.11.1 two roles named the software instead: `caddy.json` and
`garage.json`. Those objects stay in the bucket until deleted; nothing reads
them now.

| Key | Written by | `manifest_version` source |
| --- | ---------- | ------------------------- |
| `proxy.json` | [`proxy`](proxy.md) | `dpkg-query caddy` |
| `object-store.json` | [`object_store`](object_store.md) | `garage_version` |
| `postgres.json` | [`postgres`](postgres.md) | `dpkg-query postgresql-{{ postgres_version }}` |
| `redis.json` | [`redis`](redis.md) | `dpkg-query {{ redis_package }}` |
| `litellm.json` | [`litellm`](litellm.md) | `litellm_version` |
| `corliss.json` | [`corliss`](corliss.md) | the tag `corliss_version` resolved to |
| `open-webui.json` | [`open-webui`](open-webui.md) | `openwebui_version` |
| `happyview.json` | [`happyview`](happyview.md) | `happyview_version` |
| `sync-relay.json` | [`sync_relay`](sync_relay.md) | `sync_relay_version` |
| `pds.json` | [`pds`](pds.md) | `pds_version` |
| `beszel.json` | [`beszel`](beszel.md) | `beszel_version` |
| `scn-chat.json` | [`scn_chat`](scn_chat.md) | `git describe` of the checkout `scn_chat_version` resolved to |

Each object is:

```json
{"service": "redis", "version": "5:8.0.2-1", "zai_ops": "v0.6.3-2-g00db3dd", "provisioned_at": "2026-09-16T14:22:07Z"}
```

## Deploy records

Beside the manifests, the `deploy_log` callback
([`ansible/callback_plugins/deploy_log.py`](../../ansible/callback_plugins/deploy_log.py),
enabled in `ansible.cfg`) records every `provision.yml` run, per host, at the
end of the run, whether it succeeded or failed:

- `deploys/<host>/<YYYYMMDDTHHMMSSZ>.json`
- `deploys/<host>/<YYYYMMDDTHHMMSSZ>.log`: one line per task (`ok`, `changed`,
  `skipped`, `FAILED`, …) with the tail of a failed task's output

```json
{"service": "corliss", "result": "failed", "failed_task": "corliss : Sync the venv from the checkout's uv.lock",
 "version": null, "zai_ops": "v0.11.1", "trigger": "timer",
 "started_at": "2026-10-18T14:02:11Z", "finished_at": "2026-10-18T14:04:40Z",
 "recap": {"ok": 41, "failures": 1, "unreachable": 0, "changed": 3, "skipped": 6, "rescued": 0, "ignored": 0}}
```

`<host>` is the inventory name, the same key as the manifest. `version` is null when the run failed before this role. `trigger`
is `timer` for [`zai-autodeploy`](../decisions/0013-autodeploy-on-a-timer.md)
and `cli` otherwise. A `--check` run writes nothing, and nor does a host whose
every task was skipped. The callback templates the endpoint, bucket, writer key
and revision from the definitions below, and a failed write is a warning. Corliss
reads these with the reader key for its admin deploys page.

## Variables

Role defaults in [`defaults/main.yml`](../../ansible/roles/manifest/defaults/main.yml):

| Variable | Default | Meaning |
| -------- | ------- | ------- |
| `manifest_version` | *(required)* | What the play actually installed. |
| `manifest_endpoint_url` | `http://<object-store>:3900` | Garage's S3 API over `vmbr1`. |
| `manifest_region` | `garage` | Must match `garage_region` in `object_store`. Restated because role defaults are not visible to plays that don't apply the role. |

From [`group_vars/all/main.yml`](../../ansible/group_vars/all/main.yml):

| Variable | Meaning |
| -------- | ------- |
| `manifest_bucket` | `zai-manifests`. Shared with `object_store` (creates it) and `corliss` (reads it). |
| `manifest_writer_access_key` / `manifest_writer_secret_key` | Generated under `/root/.zai-secrets`. Read and write on `zai-manifests` only. |
| `zai_ops_revision` | `git describe --tags --always --dirty` of `/opt/zai-ops`. `-dirty` means the blueprint was hand-edited on CT 100. |

## Dependencies

- `object_store` must have run once, to create the bucket and import the writer
  key. `provision.yml` runs that play first; a `--limit` run on a cluster where
  it never has warns on every write and carries on.
- `python3-boto3` on CT 100 (installed by [`control_node`](control_node.md)).
  The `amazon.aws` collection ships in Debian 13's ansible 12 bundle.

## Verify

On CT 100, read one back with the reader key:

```bash
cd /opt/zai-ops/ansible
ansible localhost -m amazon.aws.s3_object -a "mode=getstr bucket=zai-manifests object=redis.json \
  endpoint_url=http://{{ hostvars['object-store'].ansible_host }}:3900 region=garage \
  access_key={{ manifest_reader_access_key }} secret_key={{ manifest_reader_secret_key }}"
```

## Notes

- Manifests are not backed up. A replay of each service play rebuilds them.
- `[WARNING]: GetObjectTagging is not implemented by your storage provider.` is
  expected. The module reads tags after every upload and Garage has no tagging;
  the write has already succeeded. See
  [Known gotchas](../gotchas.md).
- A manifest can outlive its subject: a CT rebuilt without replaying its play
  keeps the old one. The Status column on `/systems/` is what qualifies it.
