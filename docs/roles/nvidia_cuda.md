# Role: `nvidia_cuda`

Installs the NVIDIA driver and CUDA toolkit on a bare-metal inference node
(Debian 13 / Trixie).

- **Source:** [`ansible/roles/nvidia_cuda/`](../../ansible/roles/nvidia_cuda/)
- **Applied by:** [`inference.yml`](../../ansible/inference.yml) (`hosts: inference_nodes`, `become: true`)
- **Target:** a bare-metal node (salmon, orca, …), over SSH as the `ansible` user

## Purpose

Inference nodes need a working CUDA stack before [`llama_server`](llama_server.md)
can build llama.cpp. This role enables the right APT components, installs the
driver + toolkit from the Debian repos, reboots so the kernel module loads, and
verifies `nvidia-smi`. It runs first in `inference.yml`.

## Tasks

| Task | Module | Why |
| ---- | ------ | --- |
| Enable `contrib` + `non-free` APT components | `ansible.builtin.replace` | Trixie's minimal install only enables `main non-free-firmware`; the NVIDIA packages live in contrib/non-free. Idempotent. |
| Update the APT cache | `ansible.builtin.apt` | Pick up the newly-enabled components. |
| Install `mokutil` | `ansible.builtin.apt` | Needed to read the Secure Boot state in the next task. |
| Read the Secure Boot state | `ansible.builtin.command: mokutil --sb-state` (`changed_when/failed_when: false`) | Capture the state without failing the run yet. |
| Fail if Secure Boot is enabled | `ansible.builtin.assert` | Unsigned NVIDIA modules won't load under Secure Boot — fail fast with a fix message instead of rebooting into a driver that never loads. |
| Install kernel headers | `ansible.builtin.apt` (`linux-headers-{{ ansible_kernel }}`) | DKMS builds the NVIDIA module against the running kernel's headers. |
| Install the driver + CUDA toolkit | `ansible.builtin.apt` (`nvidia_packages`) | The driver, `nvidia-cuda-toolkit`, and `nvidia-cuda-toolkit-gcc`. Notifies `reboot`. |
| Reboot now if the driver was installed | `ansible.builtin.meta: flush_handlers` | The module must be loaded before later tasks / the `llama_server` build — can't defer to end-of-play. |
| Wait for the host to come back | `ansible.builtin.wait_for_connection` | Re-establish the connection after reboot. |
| Verify the driver loaded | `ansible.builtin.command: nvidia-smi` | Confirm the GPUs are visible before handing off to `llama_server`. |

### Handler

| Handler | Action |
| ------- | ------ |
| `reboot` | `ansible.builtin.reboot` with `nvidia_reboot_timeout` |

## Variables

Defined in [`defaults/main.yml`](../../ansible/roles/nvidia_cuda/defaults/main.yml):

| Variable | Default | Meaning |
| -------- | ------- | ------- |
| `nvidia_packages` | driver + `nvidia-cuda-toolkit` + `nvidia-cuda-toolkit-gcc` | Packages to install. `nvidia-cuda-toolkit-gcc` is load-bearing — it bridges the Trixie GCC 14 / nvcc 12.4 mismatch that otherwise breaks the CUDA build. |
| `nvidia_reboot_timeout` | `300` | Seconds to wait for the node after the post-install reboot. |

## Dependencies

- Runs before [`llama_server`](llama_server.md) in `inference.yml`.
- **Node prep (manual, per node) — prerequisites this role does not do:**
  - **Secure Boot disabled** in BIOS (the role asserts it; it can't change it).
  - An **`ansible` user with NOPASSWD sudo**, and **CT 100's root ed25519 public
    key** in that user's `authorized_keys`, so the control node can reach it.
  - On Trixie, `systemd-networkd` needs a `.network` file (e.g.
    `/etc/systemd/network/20-wired.network` with `DHCP=yes`) to get an address —
    the node must already be reachable at the `ansible_host` it was enrolled with.

## Verify

```bash
ssh ansible@<node> 'nvidia-smi'            # driver loaded, all GPUs listed
ssh ansible@<node> 'mokutil --sb-state'    # SecureBoot disabled
```

## Notes

- The `contrib`/`non-free` enable assumes the classic `/etc/apt/sources.list`
  format (confirmed present on salmon/orca). A deb822-only node would need that
  handled differently.
- Secure Boot disable is a manual BIOS step, like the Proxmox-side prerequisites
  elsewhere in this repo — see the [Known gotchas](../README.md#known-gotchas).
