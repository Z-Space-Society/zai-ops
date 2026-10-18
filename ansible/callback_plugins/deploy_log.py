# Record every provision.yml run, per service, in the manifest bucket.
#
#   zai-manifests/deploys/<host>/<started>.json   what happened
#   zai-manifests/deploys/<host>/<started>.log    the run's task lines
#
# A callback rather than a wrapper script, so a run is recorded however it was
# started: scn-config, zai-autodeploy, or ansible-playbook typed by hand on
# CT 100. Corliss's admin deploys page reads these with the read-only manifest
# key; Corliss can read a record but never write one.
#
# The record is written in v2_playbook_on_stats, which fires when a run fails as
# well as when it succeeds. Like the manifest role, it never fails a run: a
# record that can't be written is a warning.
#
# The installed version comes from the manifest role, which hands it over with
# set_stats (per host) right after it writes <service>.json. A run that fails
# before that point records no version.
#
# Endpoint, bucket, keys and the zai-ops revision are templated from where they
# are already defined (group_vars/all, the manifest role's defaults), so there
# is no second copy of any of them here.
#
# Runs on the control node, like every callback. Enabled in ansible.cfg.

import json
import os
from datetime import datetime, timezone

from ansible import context
from ansible.plugins.callback import CallbackBase
from ansible.template import Templar

DOCUMENTATION = """
    name: deploy_log
    type: notification
    short_description: record each provision.yml run per service in Garage
    description:
      - Writes a JSON record and a log per host to the manifest bucket under deploys/.
"""

PLAYBOOK = "provision.yml"
# Set by zai-autodeploy; anything else is a person at a shell.
TRIGGER_ENV = "ZAI_DEPLOY_TRIGGER"
# How much of a failed task's output reaches the log.
TAIL_LINES = 40


def _now():
    return datetime.now(timezone.utc)


def _stamp(when):
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def _tail(text):
    lines = str(text).rstrip().splitlines()
    return "\n".join(lines[-TAIL_LINES:])


class CallbackModule(CallbackBase):
    CALLBACK_VERSION = 2.0
    CALLBACK_TYPE = "notification"
    CALLBACK_NAME = "deploy_log"
    CALLBACK_NEEDS_ENABLED = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._active = False
        self._play = None
        self._task = None
        self._started = {}   # host -> first event time
        self._lines = {}     # host -> log lines
        self._failed = {}    # host -> name of the task that failed it
        self._s3 = None

    # --- what to record -----------------------------------------------------

    def v2_playbook_on_start(self, playbook):
        name = os.path.basename(getattr(playbook, "_file_name", "") or "")
        # A --check run changes nothing, so it is not a deploy.
        self._active = name == PLAYBOOK and not context.CLIARGS.get("check")

    def v2_playbook_on_play_start(self, play):
        self._play = play

    def v2_playbook_on_task_start(self, task, is_conditional):
        self._task = task.get_name()

    def v2_playbook_on_handler_task_start(self, task):
        self._task = "handler: " + task.get_name()

    def _add(self, result, status, detail=None):
        if not self._active:
            return
        host = result.host.get_name()
        when = _now()
        self._started.setdefault(host, when)
        line = f"{_stamp(when)}  {status:<9} {self._task or result.task.get_name()}"
        self._lines.setdefault(host, []).append(line)
        if detail:
            self._lines[host].extend("    " + d for d in detail.splitlines())

    def _detail(self, result):
        r = result.result
        parts = [str(r[k]) for k in ("msg", "stderr", "stdout") if r.get(k)]
        return _tail("\n".join(parts)) if parts else None

    def v2_runner_on_ok(self, result):
        self._add(result, "changed" if result.result.get("changed") else "ok")

    def v2_runner_on_skipped(self, result):
        self._add(result, "skipped")

    def v2_runner_on_failed(self, result, ignore_errors=False):
        self._add(result, "ignored" if ignore_errors else "FAILED", self._detail(result))
        if not ignore_errors:
            self._failed[result.host.get_name()] = self._task or result.task.get_name()

    def v2_runner_on_unreachable(self, result):
        self._add(result, "UNREACH", self._detail(result))
        self._failed[result.host.get_name()] = self._task or result.task.get_name()

    # --- writing it ---------------------------------------------------------

    def v2_playbook_on_stats(self, stats):
        if not self._active or self._play is None:
            return
        finished = _now()
        try:
            target = self._target()
        except Exception as exc:  # never fail a run over its record
            self._display.warning(f"deploy_log: could not read the manifest settings: {exc}")
            return
        if target is None:
            self._display.warning("deploy_log: object-store has no CTID assigned; no deploy records written.")
            return

        trigger = os.environ.get(TRIGGER_ENV, "cli")
        for host, lines in self._lines.items():
            summary = stats.summarize(host)
            # A host whose every task skipped (no CTID assigned) wasn't deployed.
            if summary["ok"] == 0 and summary["failures"] == 0 and summary["unreachable"] == 0:
                continue
            started = self._started[host]
            failed = summary["failures"] > 0 or summary["unreachable"] > 0
            record = {
                "service": host,
                "result": "failed" if failed else "succeeded",
                "failed_task": self._failed.get(host) if failed else None,
                "version": stats.custom.get(host, {}).get("deploy_log", {}).get("version"),
                "zai_ops": target["zai_ops"],
                "trigger": trigger,
                "started_at": _stamp(started),
                "finished_at": _stamp(finished),
                "recap": summary,
            }
            key = f"deploys/{host}/{started.strftime('%Y%m%dT%H%M%SZ')}"
            try:
                self._put(target, key + ".json", json.dumps(record, indent=2), "application/json")
                self._put(target, key + ".log", "\n".join(lines) + "\n", "text/plain; charset=utf-8")
            except Exception as exc:
                self._display.warning(f"deploy_log: {host}'s deploy record was not written: {exc}")

    def _target(self):
        """Garage endpoint, bucket and writer key, templated from the repo's own
        definitions. None when the cluster has no object store."""
        vm = self._play.get_variable_manager()
        loader = self._play.get_loader()
        store = vm._inventory.get_host("object-store")
        if store is None:
            return None
        hv = vm.get_vars(play=self._play, host=store)
        if "ctid" not in hv:
            return None
        defaults = loader.load_from_file(
            os.path.join(loader.get_basedir(), "roles", "manifest", "defaults", "main.yml")
        ) or {}
        variables = dict(defaults)
        variables.update(hv)
        t = Templar(loader=loader, variables=variables)
        return {
            name: str(t.template(variables[var]))
            for name, var in (
                ("endpoint", "manifest_endpoint_url"),
                ("region", "manifest_region"),
                ("bucket", "manifest_bucket"),
                ("access", "manifest_writer_access_key"),
                ("secret", "manifest_writer_secret_key"),
                ("zai_ops", "zai_ops_revision"),
            )
        }

    def _put(self, target, key, body, content_type):
        if self._s3 is None:
            import boto3
            from botocore.config import Config
            self._s3 = boto3.client(
                "s3",
                endpoint_url=target["endpoint"],
                region_name=target["region"],
                aws_access_key_id=target["access"],
                aws_secret_access_key=target["secret"],
                config=Config(s3={"addressing_style": "path"}, retries={"max_attempts": 2}),
            )
        self._s3.put_object(
            Bucket=target["bucket"], Key=key, Body=body.encode("utf-8"), ContentType=content_type
        )
