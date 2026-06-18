# Role: `github_user`

Creates a human admin account from someone's GitHub public keys, with sudo —
the same username on the control node and every inference node.

- **Source:** [`ansible/roles/github_user/`](../../ansible/roles/github_user/)
- **Applied by:** [`add-github-user.yml`](../../ansible/add-github-user.yml) (`hosts: control_node:inference_nodes`, `become: true`)
- **Target:** CT 100 (local) and the bare-metal inference nodes, in one play

## Purpose

There's otherwise no way to give a *person* a login — only machine identities
(CT 100's root key, the `ansible` NOPASSWD user). This role takes a GitHub
username, pulls that user's public keys live from `https://github.com/<user>.keys`,
and creates a same-named account with those keys and `sudo` group membership.

A random 16-char temporary password is generated **once per run** (shared across
all hosts via a `run_once` fact) and printed at the end. The account is created
with that password **expired** (`chage -d 0`), so the user is forced to set their
own on first login; afterwards that password unlocks `sudo`.

## Tasks

| Task | Module | Why |
| ---- | ------ | --- |
| Generate a one-time temporary password | `ansible.builtin.set_fact` (`run_once`) | One random 16-char password for the whole run, so it's the same everywhere and printed once. |
| Create the admin account | `ansible.builtin.user` (`groups: sudo`, `append`) | The login itself; `sudo` group grants sudo. Registered to gate the creation-only steps. |
| Set the temporary password | `shell: … \| chpasswd` (`when: created`, `no_log`) | `chpasswd` uses libc crypt — no passlib (Debian 13's Python 3.13 dropped stdlib `crypt`). Creation-only so re-runs don't reset it. |
| Force a password change on first login | `command: chage -d 0` (`when: created`) | Expire the temp password so the user must set their own; idempotent (creation-only). |
| Install the GitHub public keys | `ansible.posix.authorized_key` | `url` lookup fetches `github.com/<user>.keys` (multiple keys handled); seeds key-only login. |
| Report the temporary password | `ansible.builtin.debug` (`run_once`, `when: created`) | Print the temp password to hand off out-of-band. |

## Variables

Defined in [`defaults/main.yml`](../../ansible/roles/github_user/defaults/main.yml):

| Variable | Default | Meaning |
| -------- | ------- | ------- |
| `github_user` | `jsayles` | GitHub username; also the local account name. Keys come from `https://github.com/<github_user>.keys`. Override with `-e github_user=<name>`. |

## Dependencies

- The `ansible.posix` collection (bundled with Debian 13's `ansible` 12).
- The control node needs internet (it has it via the host NAT) — the `url`
  lookup that fetches the keys runs there, not on the targets.

## Verify

```bash
# on the control node and an inference node (e.g. --limit salmon):
id jsayles                                   # exists, member of sudo
chage -l jsayles                             # password must be changed
diff <(sudo cat ~jsayles/.ssh/authorized_keys) <(curl -s https://github.com/jsayles.keys)
# re-run the playbook → PLAY RECAP changed=0 for an existing user (idempotent)
# then, as the user: ssh in with the key → forced to change password → sudo works
```

## Notes

- The forced first-login password change over SSH **key** auth relies on
  `UsePAM yes` (Debian's default) *and* the temp password existing — PAM asks for
  the current password to authorize the new one, which a locked/empty password
  can't satisfy. That's why the role seeds a real temp password rather than
  locking the account.
- Passwords are set with `chpasswd` on the target, **not** Ansible's
  `password_hash` filter: Debian 13's Python 3.13 removed the stdlib `crypt`
  module the filter relied on (it would otherwise need `python3-passlib` on the
  control node).
