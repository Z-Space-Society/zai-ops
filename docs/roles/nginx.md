# Role: `nginx`

Installs and configures nginx as the ZAI cluster's reverse proxy (CT 101).

- **Source:** [`ansible/roles/nginx/`](../../ansible/roles/nginx/)
- **Applied by:** [`provision.yml`](../../ansible/provision.yml) (configure play, `hosts: ct101-nginx`)
- **Target:** CT 101, over SSH

## Purpose

CT 101 is the only LAN-facing container. This role installs nginx and renders a
reverse-proxy config that fronts the internal services (litellm, open-webui).
Because those upstreams don't exist yet, the role ships a working default that
serves a health endpoint, so the install is verifiable on its own.

## Tasks

| Task | Module | Why |
| ---- | ------ | --- |
| Install nginx | `ansible.builtin.apt` | Base package. |
| Disable the stock default site | `ansible.builtin.file` (`state: absent`) | Debian's default vhost also claims `listen 80 default_server`, which would collide with ours. |
| Deploy the reverse-proxy vhost | `ansible.builtin.template` → `/etc/nginx/sites-available/zai-reverse-proxy.conf` | Renders `reverse-proxy.conf.j2`. Notifies `reload nginx`. |
| Enable the reverse-proxy vhost | `ansible.builtin.file` (symlink into `sites-enabled/`) | Activate the vhost. Notifies `reload nginx`. |
| Validate nginx configuration | `ansible.builtin.command: nginx -t` (`changed_when: false`) | Fail the run immediately on a bad template, instead of at the deferred reload handler. |
| Ensure nginx is started and enabled | `ansible.builtin.service` | Running now + on boot. |

### Handler

| Handler | Action |
| ------- | ------ |
| `reload nginx` | `service: name=nginx state=reloaded` (graceful, no dropped connections) |

## Variables

Defined in [`defaults/main.yml`](../../ansible/roles/nginx/defaults/main.yml):

| Variable | Default | Meaning |
| -------- | ------- | ------- |
| `nginx_vhosts` | `[]` | List of reverse-proxy sites. Each item: `{ server_name, upstream_host, upstream_port }`. Empty by default — nginx still serves the health endpoint. |
| `nginx_health_port` | `80` | Port the default server listens on for `/healthz`. |

Example — once litellm/open-webui exist:

```yaml
nginx_vhosts:
  - server_name: chat.example.com
    upstream_host: 10.1.1.104
    upstream_port: 8080
  - server_name: api.example.com
    upstream_host: 10.1.1.103
    upstream_port: 4000
```

## Template

[`templates/reverse-proxy.conf.j2`](../../ansible/roles/nginx/templates/reverse-proxy.conf.j2)
renders:

- A **default server** on `nginx_health_port` that returns `200 ok` at
  `/healthz` (and `404` elsewhere) — the verifiable baseline.
- One `upstream` + `server` block per entry in `nginx_vhosts`, proxying `/` to
  the upstream with the usual `X-Forwarded-*` / `Host` headers set.

## Verify

```bash
ssh root@10.1.1.101 'systemctl is-active nginx && nginx -t'
ssh root@10.1.1.101 'curl -s localhost/healthz'   # → ok
```

## Notes

- The config is deployed as a single file (`zai-reverse-proxy.conf`); add sites
  by extending `nginx_vhosts`, not by hand-editing on the box.
- `nginx -t` runs as an explicit task (not just in the handler) so config errors
  surface during the play.
- For how CT 101 is created and reached, see the
  [main docs](../README.md#networking) and [`provision.yml`](../../ansible/provision.yml).
