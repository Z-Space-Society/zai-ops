# Role: `llama_server`

Builds llama.cpp with CUDA and installs `llama-server` as a systemd service on a
bare-metal inference node.

- **Source:** [`ansible/roles/llama_server/`](../../ansible/roles/llama_server/)
- **Applied by:** [`inference.yml`](../../ansible/inference.yml) (`hosts: inference_nodes`, `become: true`)
- **Target:** a bare-metal node (salmon, orca, …), over SSH as the `ansible` user

## Purpose

The inference node's one job is to serve a model. This role compiles llama.cpp
against the node's GPU(s) — architecture auto-detected from `nvidia-smi`, so no
per-host build config — and installs a `llama-server` unit. The unit is
**enabled but not started**: the GGUF is staged by hand (models are large), and
the service would fail without it. Runs after [`nvidia_cuda`](nvidia_cuda.md).

## Tasks

| Task | Module | Why |
| ---- | ------ | --- |
| Install the build toolchain | `ansible.builtin.apt` (`llama_build_packages`) | `build-essential`, `cmake`, `git` to fetch and compile (nvcc comes from `nvidia_cuda`). |
| Detect GPU compute capabilities | `command: nvidia-smi --query-gpu=compute_cap` (`changed_when: false`) | Drives the CUDA arch flag so the build matches the actual cards. |
| Derive the CUDA architecture string | `ansible.builtin.set_fact` | `8.6` → `86`; dedup + `;`-join handles mixed-arch nodes. |
| Create the `llama` service user | `ansible.builtin.user` (`system`, nologin) | Run the daemon unprivileged. |
| Create the model directory | `ansible.builtin.file` | Where GGUFs are staged (`llama_model_dir`). |
| Clone llama.cpp | `ansible.builtin.git` (`depth: 1`) | Source for the build; registered to gate rebuilds. |
| Configure the CUDA build | `command: cmake -B build …` (`creates:`) | `-DGGML_CUDA=ON` + detected arch. Skipped once `CMakeCache.txt` exists. |
| Build llama.cpp | `command: cmake --build …` (`creates:`) | Skipped once the `llama-server` binary exists — full rebuilds are slow. |
| Rebuild after a source update | `command: cmake --build …` (`when: src changed`) | Recompile when the checkout advanced even though the binary already exists. |
| Install the systemd unit | `ansible.builtin.template` | Renders `llama-server.service.j2`. Notifies `reload systemd`. |
| Enable (not start) llama-server | `ansible.builtin.systemd` (`enabled: true`) | On at boot once a model is staged; not started now (no GGUF yet). |

### Handler

| Handler | Action |
| ------- | ------ |
| `reload systemd` | `systemd: daemon_reload: true` |

## Variables

Defined in [`defaults/main.yml`](../../ansible/roles/llama_server/defaults/main.yml):

| Variable | Default | Meaning |
| -------- | ------- | ------- |
| `llama_build_dir` | `/opt/llama.cpp` | Clone + build location. |
| `llama_model_dir` | `/opt/models` | Where GGUFs are staged. |
| `llama_user` | `llama` | Service user. |
| `llama_build_packages` | `build-essential`, `cmake`, `git` | Build toolchain. |
| `llama_model_file` | `model.gguf` | GGUF filename under `llama_model_dir`. |
| `llama_port` | `8080` | Listen port. |
| `llama_host` | `0.0.0.0` | Listen address. |
| `llama_ngl` | `999` | `-ngl`: offload all layers to GPU. |
| `llama_extra_args` | `[]` | Extra `llama-server` flags appended verbatim. |

The serving vars are **this-cluster data** — set them per node in the runtime
inventory (via `enroll-inference-node.yml` or by editing `inventory/local.yml`),
not in the repo. For example:

- **salmon** (2× 1080 Ti, one model split): `llama_extra_args: ["--tensor-split", "1,1"]`,
  plus `--jinja` for Apertus.
- **orca** (1× 3090): defaults are usually fine; set `llama_model_file`.

## Template

[`templates/llama-server.service.j2`](../../ansible/roles/llama_server/templates/llama-server.service.j2)
renders the `ExecStart` from the vars above and appends each `llama_extra_args`
entry on its own continuation line.

## Dependencies

- Requires [`nvidia_cuda`](nvidia_cuda.md) to have run (nvcc + a loaded driver).
- Same node-prep prerequisites as `nvidia_cuda` (the `ansible` user, key, etc.).

## Verify

```bash
ssh ansible@<node> 'ls /opt/llama.cpp/build/bin/llama-server'  # built
ssh ansible@<node> 'systemctl is-enabled llama-server'         # enabled
# after staging a GGUF as /opt/models/<llama_model_file>:
ssh ansible@<node> 'sudo systemctl start llama-server && curl -s localhost:8080/health'
```

## Notes

- Model files are **not** downloaded by the role (large; chosen per node). Stage
  the GGUF named by `llama_model_file`, then start the service.
- Pascal cards (1080 Ti) want K-quants, not IQ-quants — see the vault's Models
  note. That's a model-selection concern, not a role setting.
