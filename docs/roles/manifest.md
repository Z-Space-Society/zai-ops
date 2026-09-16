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
    manifest_service: redis
    manifest_version: "{{ redis_installed.stdout }}"
```

Put it **after** the role's smoke test, so a service that fails its smoke test
never claims a version.

## Tasks

| Task | Module | Why |
| ---- | ------ | --- |
| Assert the caller passed a service and a version | `ansible.builtin.assert` | A missing var is a bug in the calling role, so this one raises. |
| Write the manifest to Garage | `amazon.aws.s3_object` (`mode: put`, delegated to localhost) | `permission: []` and `encrypt: false` because Garage has no ACLs or SSE-S3 and the module turns both on by default. `overwrite: always` because every manifest carries a fresh timestamp. `failed_when: false`: a metadata write must never fail a service deploy. Skipped when `object-store` has no CTID. Under `--check` the module reports without writing. |
| Warn that the manifest was not written | `ansible.builtin.debug` | Makes a skipped or failed write visible in the run output without failing the play. |

## Manifest keys

Corliss reads these exact names, so they are an interface. Rename one on both
sides in the same change.

| Key | Written by | `manifest_version` source |
| --- | ---------- | ------------------------- |
| `caddy.json` | [`proxy`](proxy.md) | `dpkg-query caddy` |
| `garage.json` | [`object_store`](object_store.md) | `garage_version` |
| `postgres.json` | [`postgres`](postgres.md) | `dpkg-query postgresql-{{ postgres_version }}` |
| `redis.json` | [`redis`](redis.md) | `dpkg-query {{ redis_package }}` |
| `litellm.json` | [`litellm`](litellm.md) | `litellm_version` |
| `corliss.json` | [`corliss`](corliss.md) | `corliss_version` |
| `open-webui.json` | [`open-webui`](open-webui.md) | `openwebui_version` |
| `happyview.json` | [`happyview`](happyview.md) | `happyview_version` |
| `sync-relay.json` | [`sync_relay`](sync_relay.md) | `sync_relay_version` |

Each object is:

```json
{"service": "redis", "version": "5:8.0.2-1", "zai_ops": "v0.6.3-2-g00db3dd", "provisioned_at": "2026-09-16T14:22:07Z"}
```

## Variables

Role defaults in [`defaults/main.yml`](../../ansible/roles/manifest/defaults/main.yml):

| Variable | Default | Meaning |
| -------- | ------- | ------- |
| `manifest_service` | *(required)* | Object key without `.json`. See the table above. |
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
  key. Until it has, every write warns and the plays carry on.
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
  [Known gotchas](../README.md#known-gotchas).
- A manifest can outlive its subject: a CT rebuilt without replaying its play
  keeps the old one. The Status column on `/systems/` is what qualifies it.
