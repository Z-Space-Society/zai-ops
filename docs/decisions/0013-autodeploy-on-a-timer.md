# ADR-0013: Apps on `latest` deploy on a timer, and every provision records itself

> Status: **Accepted** (2026-10-18). Settles ADR-0012 point 4 (zai-ops#33) and
> revises its point 3: Corliss's default is now `latest`.

## Context

ADR-0012 made an app's release runtime data: a tag or `latest`. That left one
gap: nothing in zai-ops provisioned on its own, so a `latest` app moved only
when someone provisioned it. App developers tag a release and then wait for an
operator, with no way to see whether it was noticed or how it went.

The first design (zai-ops#26) had a GitHub webhook call a receiver on the app
CT, which rebuilt `main` HEAD outside the role. Since ADR-0012 a deploy is
simply the role at a tag, so the trigger is the only thing missing. Every
trigger that reaches in from GitHub ran into one of these problems:
- An endpoint in front of CT 100, which holds every key.
- A self-hosted runner, while the app repos are public.
- A GitHub token on CT 100, with any log it posts there public.

## Decision

1. **CT 100 polls; nothing reaches in.** `zai-autodeploy.timer` runs
   [`bin/zai-autodeploy`](../../bin/zai-autodeploy) every 5 minutes. It reads
   each app's setting, newest tag and deployed version from `set-version.yml
   -e version_action=show`, the same data `scn-config`'s Versions screen
   shows. For each app set to `latest` whose newest tag differs from what is
   deployed, it runs `provision.yml --limit <app>`.

2. **A pin is never moved.** The timer acts only on `latest`, recorded or the
   role default. A pinned app stays where it is. An app with nothing deployed
   yet is left for its first provision by hand.

3. **A failed release isn't retried every tick.** Its tag is remembered under
   `/var/lib/zai-autodeploy`. A newer tag, or a successful provision by hand,
   moves the app on.

4. **Every provision records itself, from inside Ansible.** The `deploy_log`
   callback ([`ansible/callback_plugins/deploy_log.py`](../../ansible/callback_plugins/deploy_log.py),
   enabled in `ansible.cfg`) writes `deploys/<host>/<started>.json` and
   `.log` to the manifest bucket at the end of every `provision.yml` run. That
   covers success and failure, and runs from `scn-config`, from the timer, or
   typed by hand. The record says the result, the failing task, the installed
   version (from the manifest role, via `set_stats`), the zai-ops revision,
   and the trigger (`timer` or `cli`). Like the manifest write, a record that
   can't be written is a warning, never a failed run.

5. **Both apps default to `latest`.** scn-chat already did. Corliss moves from
   a pinned tag to `latest`, so its releases deploy on the timer as well.

6. **Corliss reads the records; it can't write them.** Corliss's key on the
   manifest bucket is read-only, so what it shows on its admin deploys page is
   what CT 100 wrote.

## Consequences

- **Both role defaults are `latest`,** so on any cluster that hasn't pinned
  them, every new scn-chat or Corliss release deploys within a tick once this
  lands. That includes Heron. Pin one (`scn-config nonint set-version <app>
  vX.Y.Z`) to opt out.
- **A Corliss release that needs new role wiring has to follow its zai-ops
  change, not lead it.** ADR-0012 kept Corliss pinned for exactly this case.
  Here, the role change is merged and deployed first, then the tag is pushed.
  Tagged out of order, the deploy fails, the record names the task, and the
  timer waits for the next tag while the previous release keeps serving if the
  failure came before the restart.
- The timer skips a tick while any `ansible-playbook` runs on CT 100, so it
  never races an operator.
- A `--check` run writes no record, and nor does a host whose every task was
  skipped.
- Records accumulate; nothing prunes them yet.
- A Deploy button on Corliss is a separate step (zai-ops#41). It builds on
  these records.
