# ADR-0008: Host scripts run from a clone of the repo on the host

> Status: **Accepted** (2026-09-11). Replaces the "one host script, fetched with
> curl" half of the bootstrap decision. Everything after CT 100 exists is
> unchanged.

## Context

A Proxmox host used to come up with one command:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/Z-Space-Society/zai-ops/main/bootstrap.sh)"
```

The host never held the repo. CT 100 got its own clone, and everything after
the bootstrap ran from there.

Two things pushed against that.

1. **A script fetched straight into a root shell leaves nothing to read.** There
   is no copy on disk to open, diff or `git log` before it runs, and no record
   afterwards of which version ran.
2. **A second host-side job turned up: human logins on the host itself.**
   `add-github-user.yml` creates accounts on CT 100 and the inference nodes, but
   it cannot reach the host. The inventory has no entry for the host, and CT 100
   talks to Proxmox only through the API token, which cannot create Linux
   accounts. So host logins were made by hand, which is exactly what the
   reproducibility rule forbids. There was also nowhere in the repo for a
   host-side script to live, since the host never had the repo.

Base Proxmox does not ship git, which is why the curl form existed.

## Decision

- **The host holds a clone at `/root/zai-ops`.** Installing git is the one manual
  step, then the operator clones and runs from that clone:

  ```bash
  apt-get update; apt-get install -y git
  git clone https://github.com/Z-Space-Society/zai-ops.git /root/zai-ops
  /root/zai-ops/host/bootstrap.sh
  ```

  Root's home, not `/opt`, on purpose. CT 100's clone lives at `/opt/zai-ops`,
  and a second copy at the same path one shell away (`pct enter`) is easy to
  mistake for the first.

  On a fresh host the enterprise repo is still enabled and 401s during that
  update. The bootstrap disables it.

- **Scripts that run on the Proxmox host live in `host/`**, run as root by path.
  They are not put on PATH. The first two are `host/bootstrap.sh` (moved from the
  repo root, logic unchanged) and `host/import-github-user.sh`, which creates a
  sudo account from a GitHub user's public keys.

- **`bin/` is unchanged.** It holds the control node's operator commands, on
  CT 100's PATH. The line between the two directories is where the script runs:
  `host/` does what only the host can (`pct`, `pveum`, host accounts), `bin/`
  drives Ansible from CT 100.

- **The two clones stay independent.** The bootstrap still clones a separate
  copy into CT 100. The host's copy is not bind-mounted into the container:
  CT 100 writes its runtime inventory and vault into its own tree, its root maps
  to an unprivileged uid on the host, and the container should stay
  self-contained. The host's copy only matters while a host script runs, so
  `git pull` there first.

- **CT 100 still has no SSH path to the host.** Ansible still cannot reach it.
  Host scripts are run by a person with host root, which is already the trust
  boundary.

- **`import-github-user.sh` imports and nothing more.** Running it again adds keys
  that are new on GitHub. It never removes keys or accounts.

## Consequences

Positive:
- The code that runs as root on the host is on disk first, readable and
  diffable, with its commit recorded in the clone.
- Host-side work has a home in the repo, so host logins stop being hand edits.
  A rebuilt host gets them back with one command per person.

Negative / tradeoffs:
- One more manual step before the bootstrap: installing git.
- **Trust is unchanged.** Cloning `main` trusts GitHub and TLS exactly as much as
  fetching the script with curl did. The gain is inspection, not integrity. A
  stronger posture would check out a signed tag and verify it before running;
  that is not done here.
- Two clones can drift. A stale host clone runs stale host scripts until someone
  pulls.
- A key deleted on GitHub stays on the host until someone removes it by hand.
- The repo has to stay publicly cloneable without credentials, as the curl form
  already required.
