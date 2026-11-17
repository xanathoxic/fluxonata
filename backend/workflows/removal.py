"""Suppression d'un workflow avec son historique et ses déclencheurs, sur demande expresse (voir `catalog.removal` pour le principe)."""

from collections import Counter

from django.db import transaction
from rest_framework import serializers

from catalog.removal import TERMINAL, _artifact_keys, _drop_files, refuse_if_running
from runs.models import Execution, StepRun, WorkflowRun
from triggers.models import PollTrigger, PublicForm, Schedule, Webhook

TRIGGERS = (("schedules", Schedule), ("webhooks", Webhook), ("forms", PublicForm), ("polls", PollTrigger))


def _run_tree(root_ids: list) -> list:
    """Ids of the runs and of every sub-run they started (a sub-run is deleted with the step that started it)."""
    seen, level = set(root_ids), list(root_ids)
    while level:
        level = [
            pk
            for pk in WorkflowRun.objects.filter(parent_step__run__in=level).values_list("pk", flat=True)
            if pk not in seen
        ]
        seen.update(level)
    return list(seen)


def workflow_facts(wf) -> dict:
    """Runs (with their sub-runs), the ones still active, and the triggers that would go with the workflow."""
    own = list(WorkflowRun.objects.filter(workflow_version__workflow=wf).values_list("pk", flat=True))
    tree = _run_tree(own)
    runs = WorkflowRun.objects.filter(pk__in=tree)
    return {
        "executions": len(tree),
        "running": runs.exclude(status__in=TERMINAL).count(),
        "triggers": {name: model.objects.filter(workflow=wf).count() for name, model in TRIGGERS},
    }


def purge_workflow_history(wf) -> tuple[Counter, list[str]]:
    """Deletes every run of `wf` (and its sub-runs, executions, events, files). Returns (counts, artifact keys to remove from storage AFTER commit)."""
    own = list(WorkflowRun.objects.filter(workflow_version__workflow=wf).values_list("pk", flat=True))
    tree = _run_tree(own)
    executions = Execution.objects.filter(step_run__run__in=tree)
    counts = Counter(runs=len(tree), executions=executions.count())
    keys = _artifact_keys(executions)
    executions.delete()
    StepRun.objects.filter(run__in=tree).delete()
    WorkflowRun.objects.filter(pk__in=tree).delete()
    return counts, keys


def delete_workflow(wf, *, with_history: bool, with_triggers: bool) -> dict:
    """Deletes `wf`. The caller has already refused when other workflows use it. Returns what was removed along with it."""
    facts = workflow_facts(wf)
    reasons = []
    if facts["executions"] and not with_history:
        reasons.append("il a déjà été exécuté")
    if any(facts["triggers"].values()) and not with_triggers:
        reasons.append("une planification, un webhook, un formulaire public ou une interrogation l'utilise")
    if reasons:
        raise serializers.ValidationError(
            {
                "workflow": "Suppression refusée : "
                + ", ".join(reasons)
                + ". Dépréciez-le plutôt, ou confirmez leur suppression."
            }
        )
    if with_history:
        refuse_if_running(facts, "ce workflow")
    removed: dict = {"history": 0, "triggers": {}}
    keys: list[str] = []
    with transaction.atomic():
        if with_history:
            counts, keys = purge_workflow_history(wf)
            removed["history"] = counts["runs"]
        if with_triggers:
            removed["triggers"] = {name: n for name, n in facts["triggers"].items() if n}
        wf.delete()  # the triggers go with it (cascade): they were counted above and the person confirmed
    _drop_files(keys)
    return removed
