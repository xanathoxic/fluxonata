"""Progression d'une exécution, telle que la voit un consommateur de l'API store.

Calculée à la lecture depuis les étapes du run lui-même (jamais celles de ses sous-exécutions). Elle donne le nombre d'étapes passées, le
total, l'étape en cours (nom et état) et l'état global. Elle ne contient AUCUNE entrée ni sortie d'étape, ni message d'erreur.
"""

from runs.models import StepRun, WorkflowRun

S = StepRun.Status
ACTIVE = (S.RUNNING, S.RETRY_WAIT, S.WAITING, S.UNCERTAIN)
PASSED = (
    S.SUCCEEDED,
    S.SKIPPED,
)  # une branche non prise (ignorée) compte comme passée : la barre atteint le total à la fin


def _name(nodes: dict, node_id: str) -> str:
    node = nodes.get(node_id) or {}
    return str(node.get("name") or node_id)


def _current(run: WorkflowRun, steps: list, nodes: dict):
    """(étape à montrer, nombre d'autres étapes actives). Active la plus ancienne ; à défaut, la dernière échouée d'un run échoué."""
    active = [s for s in steps if s.status in ACTIVE]
    if active:
        return active[0], len(active) - 1
    failed = [s for s in steps if s.status == S.FAILED]
    if run.status == WorkflowRun.Status.FAILED and failed:
        return failed[-1], 0
    return None, 0


def progress(run: WorkflowRun) -> dict:
    nodes = {n["id"]: n for n in run.definition.get("graph", {}).get("nodes", [])}
    steps = list(run.steps.only("node_id", "status", "started_at").order_by("started_at", "id"))
    shown, others = _current(run, steps, nodes)
    return {
        "steps_done": sum(1 for s in steps if s.status in PASSED),
        "steps_total": len(nodes),
        "current_step": (
            {"name": _name(nodes, shown.node_id), "status": shown.status, "status_label": shown.get_status_display()}
            if shown
            else None
        ),
        "also_active": others,
        "workflow_status": run.status,
        "workflow_status_label": run.get_status_display(),
    }
