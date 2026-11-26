"""Greffon de rappel Ansible de Fluxonata : écrit, à la fin du playbook, un fichier JSON compact (résumé par hôte et une ligne par tâche).

Il ne remplace pas la sortie standard d'Ansible (type « aggregate ») et n'a besoin d'aucune collection. Une tâche `no_log` n'y laisse aucun message.
Le fichier est écrit à l'endroit donné par la variable d'environnement FLX_RESULT_FILE ; sans elle, le greffon ne fait rien.
"""

from __future__ import annotations

import json
import os

from ansible.plugins.callback import CallbackBase

DOCUMENTATION = """
    name: fluxonata_callback
    type: aggregate
    short_description: résumé JSON pour Fluxonata
    description: écrit un résumé JSON compact du playbook.
"""

MAX_TASKS = 2000
MAX_MSG = 500


class CallbackModule(CallbackBase):
    CALLBACK_VERSION = 2.0
    CALLBACK_TYPE = "aggregate"
    CALLBACK_NAME = "fluxonata_callback"
    CALLBACK_NEEDS_ENABLED = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.tasks: list[dict] = []
        self.dropped = 0

    def _add(self, status: str, result, ignored: bool = False) -> None:
        if len(self.tasks) >= MAX_TASKS:
            self.dropped += 1
            return
        data = result._result or {}
        if data.get("_ansible_no_log") or data.get("censored") or getattr(result._task, "no_log", False):
            msg = "(masqué : no_log)"
        else:
            msg = str(data.get("msg") or data.get("stderr") or "")[:MAX_MSG]
        entry = {
            "host": result._host.get_name(),
            "task": result._task.get_name(),
            "status": status,
            "changed": bool(data.get("changed", False)),
            "msg": msg,
        }
        if ignored:
            entry["ignored"] = True
        self.tasks.append(entry)

    def v2_runner_on_ok(self, result):
        self._add("ok", result)

    def v2_runner_on_failed(self, result, ignore_errors=False):
        self._add("failed", result, ignored=bool(ignore_errors))

    def v2_runner_on_unreachable(self, result):
        self._add("unreachable", result)

    def v2_runner_on_skipped(self, result):
        self._add("skipped", result)

    def v2_playbook_on_stats(self, stats):
        path = os.environ.get("FLX_RESULT_FILE")
        if not path:
            return
        hosts = {h: stats.summarize(h) for h in sorted(stats.processed)}
        summary = {
            "stats": hosts,
            "tasks": self.tasks,
            "dropped": self.dropped,
            "custom": stats.custom,
        }
        with open(path, "w") as fh:
            json.dump(summary, fh, default=str)
