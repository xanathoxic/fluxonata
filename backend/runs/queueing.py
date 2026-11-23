"""Mode d'exécution « séquentiel » d'un workflow : un seul lancement actif à la fois par (workflow, environnement).

Un lancement qui trouve le précédent encore actif naît « En attente » (`queued`) : l'orchestrateur ne le fait pas avancer. À chaque tour,
`promote_waiting` démarre le plus ancien de chaque file dont plus aucun lancement n'est actif. L'état vit dans PostgreSQL : un redémarrage
ne perd aucun lancement, et deux orchestrateurs ne promeuvent jamais deux lancements d'une même file (verrou sur le workflow).
Les sous-workflows et les lancements sur brouillon ne sont jamais mis en file (ils s'exécutent dans un lancement parent ou à titre d'essai)."""

from django.db import transaction
from django.utils import timezone

from audit.models import record
from workflows.models import Workflow

from .models import RunEvent, WorkflowRun

R = WorkflowRun.Status
ACTIVE = (R.RUNNING, R.COMPENSATING)


def _event(run, type_: str, message: str) -> None:
    """Événement du journal du lancement (même numérotation que `engine.add_event`, qui ne peut pas être importé ici sans cycle)."""
    last = run.events.order_by("-seq").values_list("seq", flat=True).first() or 0
    RunEvent.objects.create(run=run, seq=last + 1, type=type_, message=message[:300])


def lock_if_sequential(workflow: Workflow) -> bool:
    """Verrouille le workflow (sérialise les lancements concurrents) ; vrai s'il est en mode séquentiel."""
    mode = Workflow.objects.select_for_update().values_list("run_mode", flat=True).get(pk=workflow.pk)
    return mode == Workflow.RunMode.SEQUENTIAL


def must_wait(workflow: Workflow, environment) -> bool:
    """Vrai si un lancement de ce workflow est déjà actif ou en attente dans l'environnement (à appeler sous `lock_if_sequential`)."""
    return WorkflowRun.objects.filter(
        workflow_version__workflow=workflow,
        environment=environment,
        parent_step__isnull=True,
        status__in=[*ACTIVE, R.QUEUED],
    ).exists()


def waits_for_turn(workflow: Workflow, environment, parent_step, graph_override) -> bool:
    """Le lancement qui se crée doit-il attendre ? Seuls les lancements de premier niveau, hors brouillon, d'un workflow séquentiel."""
    if parent_step is not None or graph_override or not lock_if_sequential(workflow):
        return False
    return must_wait(workflow, environment)


def start_event(workflow_version, waits: bool) -> tuple[str, str]:
    """Type et message de l'événement de création d'un lancement."""
    wf = workflow_version.workflow
    if waits:
        return "queued", f"Workflow {wf.name} : un lancement précédent est en cours, celui-ci attend son tour."
    return "started", f"Workflow {wf.name} v{workflow_version.version} démarré."


def promote_waiting() -> int:
    """Démarre le plus ancien lancement en attente de chaque file libre. Renvoie le nombre de lancements promus."""
    promoted = 0
    heads = (
        WorkflowRun.objects.filter(status=R.QUEUED, parent_step__isnull=True)
        .order_by("created_at")
        .values_list("workflow_version__workflow_id", "environment_id")
        .distinct()
    )
    for workflow_id, environment_id in list(heads):
        with transaction.atomic():
            Workflow.objects.select_for_update().filter(pk=workflow_id).first()
            busy = WorkflowRun.objects.filter(
                workflow_version__workflow_id=workflow_id,
                environment_id=environment_id,
                parent_step__isnull=True,
                status__in=ACTIVE,
            ).exists()
            nxt = (
                None
                if busy
                else WorkflowRun.objects.select_for_update()
                .filter(
                    workflow_version__workflow_id=workflow_id,
                    environment_id=environment_id,
                    parent_step__isnull=True,
                    status=R.QUEUED,
                )
                .order_by("-priority", "created_at", "id")
                .first()
            )
            if nxt is None:
                continue
            nxt.status = R.RUNNING
            nxt.next_check_at = timezone.now()
            nxt.save(update_fields=["status", "next_check_at"])
            _event(nxt, "dequeued", "Le lancement précédent est terminé : ce lancement démarre.")
            promoted += 1
    return promoted


def cancel_waiting(run, user) -> None:
    """Annule un lancement encore en attente : il n'a rien exécuté, il passe directement à « Annulée »."""
    run.status, run.finished_at = R.CANCELLED, timezone.now()
    run.save(update_fields=["status", "finished_at"])
    _event(run, "cancelled", f"Lancement en attente annulé par {user.username}.")
    record("workflow_run.cancel", actor=user, workspace=run.workspace, target=run)
