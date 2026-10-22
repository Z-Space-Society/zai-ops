# Using the system monitor

For cluster admins: how to use the cluster's monitor day to day. It covers what
it shows, how to read it, how alerts reach you, and how to add, remove or fix a
machine. How it is built is in the role docs: [`beszel`](roles/beszel.md) (the
hub) and [`monitor_agent`](roles/monitor_agent.md) (the agents).

Commands marked **CT 100** run on the control node (`pct enter 100` from the
Proxmox host). Commands marked **host** run on the Proxmox host as root.

## What it is

[Beszel](https://beszel.dev) shows every machine in the cluster on one page:
CPU, memory, disk, network, temperatures, systemd services, and the OS updates
waiting on each machine. It also emails an alert when a machine goes down or
runs short of disk or memory.

It lives at `https://monitor.<domain>`, for example
https://monitor.sharedcomputer.network on Heron. On Ronchamp it is reachable
only from CT 100 (`http://10.1.1.123:8090`) until
[#48](https://github.com/Z-Space-Society/zai-ops/issues/48) lands.

There is one row per machine:

| Row | Machine | How its agent gets there |
| --- | ------- | ------------------------ |
| `proxy`, `postgres`, … `beszel` | each service container | automatically, when the container is provisioned |
| `control` | CT 100, the control node | Monitoring Agents in `scn-config` |
| the Proxmox host's name (`heron`) | the Proxmox host | `host/install-monitor-agent.sh` |
| `salmon`, `orca`, … | the inference nodes | a script printed on CT 100, run on the node |

## Signing in

Cluster admins sign in through Corliss: the button under the password form
names the cluster's domain. Anyone not on the admin roster is refused at
Corliss. Your first sign-in creates your user on the hub, and you see every
system.

Removing someone as an admin reaches the hub when it is next provisioned:
`scn-config` → Cluster Admins → Apply does that, and deletes their hub user,
which signs them out.

The password login, `admin@<domain>`, stays as a backup for when Corliss is
down. Its password is on CT 100:

```sh
cat /root/.zai-secrets/beszel_admin_password      # CT 100
```

Rebuilding the hub signs everyone out. Sign in again with the same password.

A sign-in from a new address or browser sends `admin@<domain>` a "Login from a
new location" email. That's the hub's own security notice: if it wasn't you,
tell the other admins and provision `beszel`, which puts the password back.

## Reading the dashboard

Most columns are what they say. Four need a note:

- **The dot before a name** is green when the machine is reporting, and red
  when it has stopped (the agent is down, or the machine is).
- **Updates** counts the packages waiting on that machine. The dot beside it is
  **red** when any of them is a security update, **yellow** when some are
  waiting but none is a security update, and **green** with "Up to date" when
  nothing is waiting. Click a machine to see the list.
- **Agent** is the agent's version. Every agent should match the hub's version,
  in the footer.
- **Services** is the number of systemd units. A failed one sends an alert
  (below).

Two limits of the Updates count:

- **It can be up to a day old.** Each machine refreshes its package lists once a
  day, and the count is taken from those lists. To see the current count for the
  containers, run the report below.
- **Pinned packages don't show.** Caddy on the `proxy` container is pinned to an
  exact version, so a newer Caddy never shows as waiting. `apt-upgrade.yml`'s
  summary is the authority for pins: it says when the archive has a newer one.

## Acting on pending updates

For the containers (**CT 100**):

```sh
cd /opt/zai-ops/ansible
ansible-playbook apt-upgrade.yml                                         # report only
ansible-playbook apt-upgrade.yml -e upgrade_action=apply --limit redis   # upgrade one
```

Upgrade a few at a time with `--limit`. Upgrading Postgres restarts it under
every app that uses it. The report ends with one line per container, and
`current` means nothing is waiting and no pin is behind. Apply never removes a
package, so if anything is left, it needs a deliberate `apt full-upgrade` on
that container.

The Proxmox host and the inference nodes are upgraded by hand on each machine,
with `apt update && apt full-upgrade`.

The row's count updates on the agent's next hourly check, or straight away if
you restart the agent on that machine (`systemctl restart beszel-agent`).

## Alerts

Every machine is watched for:

| Alert | Sends when |
| ----- | ---------- |
| Status | the machine has been down for 5 minutes, and again when it is back |
| Disk | disk use has been over 90% for 10 minutes |
| Memory | memory use has been over 90% for 10 minutes |
| Failed service | any systemd unit has failed |

**Who gets them:** one address, the alert recipient. Alerts are emailed through
the cluster's mail relay, so they are **off** unless both the relay and the
recipient are set.

**Setting the recipient** (**CT 100**): `scn-config` → Set SMTP → Alert
recipient. Then let it provision the monitoring hub when it offers. Scripted:

```sh
scn-config nonint set-alert-email ops@example.org
scn-config nonint show-smtp          # shows the relay and the recipient
ansible-playbook provision.yml --limit beszel
```

**Changing what is watched:** the list above is `beszel_alerts` in
[`roles/beszel/defaults/main.yml`](../ansible/roles/beszel/defaults/main.yml).
Change it in a pull request, then run Monitoring Agents. Don't change a
threshold in the hub's UI: the next run puts it back. An extra alert you add in
the UI stays, but only you get it, and it isn't rebuilt with the hub.

**Silencing for maintenance:** Settings → Notifications → Quiet Hours in the
hub. Ansible leaves quiet hours alone.

## Adding a machine

Never use "Add System" in the hub. Each kind of machine has its own way in, and
each way also gives the machine its alerts.

- **A new service container:** nothing to do. Provisioning it installs its
  agent.
- **A new cluster:** the containers provisioned before the hub skip their agent,
  with a message saying so. After the last provision, run `scn-config` →
  Monitoring Agents (**CT 100**).
- **The Proxmox host** (**host**):

  ```sh
  git -C /root/zai-ops pull
  /root/zai-ops/host/install-monitor-agent.sh        # add the control node's CTID if it isn't 100
  ```

  It reads what it needs from CT 100, ends with `connected to …`, and puts the
  host's alerts on.

- **An inference node:** on **CT 100**, print the node's install script:

  ```sh
  scn-config nonint print-monitor-agent-install salmon > /root/salmon-agent.sh
  ```

  Copy it to the node and run it as root (`bash salmon-agent.sh`). Delete both
  copies afterwards, because the script holds the agent token. Then, on CT 100,
  run `scn-config nonint apply-monitor-agents beszel` to put the node's alerts
  on. The node reaches the hub through `https://monitor.<domain>`, so the public
  route must be working.

## Removing a machine

1. On the machine: `systemctl disable --now beszel-agent`.
2. In the hub, open the row's menu (the `…` at the end) and delete it.

This is the one thing done in the UI. A row for a machine that no longer exists
isn't configuration, so there is nothing for Ansible to keep. If you skip step 1,
the machine comes back on its own.

## Routine operations

| After | Do |
| ----- | -- |
| a `beszel_version` bump (a PR that changes it and the two checksums) | provision `beszel`, run Monitoring Agents, re-run `host/install-monitor-agent.sh` on the host, and print and run a new script on each inference node |
| a hub rebuild | run Monitoring Agents. The agents come back on their own, and this run puts their alerts back. The metrics history is gone |
| a CT 100 rebuild | run Monitoring Agents, which reinstalls CT 100's own agent |
| a container rebuild | nothing: provisioning reinstalls its agent, and it returns to its old row with its history |

## When something looks wrong

| You see | Check |
| ------- | ----- |
| A row is red (down) | On that machine: `systemctl status beszel-agent`, then `journalctl -u beszel-agent -n 50` |
| A Monitoring Agents run says a host "gets no agent this run" | The hub was not answering. Check the `beszel` row, provision `beszel` if needed, then run it again |
| The Updates count looks stale | On that machine: `systemctl list-timers apt-lists-refresh.timer` (on the host, `pve-daily-update.timer`) |
| No alert email arrives | `scn-config nonint show-smtp` should show a relay and a recipient. Then provision `beszel`. The hub's log names the failure: `ssh root@10.1.1.123 'journalctl -u beszel -n 30 --no-pager'` (**CT 100**) |
| A machine appears twice | An old row from before a reinstall under another name. Delete the one that is down |
| You can't sign in | `cat /root/.zai-secrets/beszel_admin_password` on CT 100. A provision of `beszel` resets the login if it drifted |
