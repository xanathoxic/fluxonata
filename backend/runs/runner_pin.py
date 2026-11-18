"""Runner imposé à un workflow (et, si demandé, à ses sous-workflows).

Un workflow peut désigner UN runner (`Workflow.runner`) : ses actions et ses scripts ne s'exécutent alors que sur lui — jamais sur un worker intégré ni sur un autre
runner. Avec `runner_force_children`, ses sous-workflows (étape Workflow, boucle sur liste, boucle conditionnelle) s'exécutent AUSSI sur ce runner, même si un autre
leur est assigné ; l'obligation se propage à toute la descendance. Sans cette option, un sous-workflow utilise son propre runner imposé, sinon le choix habituel.

Le runner effectif est RÉSOLU AU LANCEMENT et figé dans le run (`definition["runner"]`) : changer le réglage du workflow n'affecte pas un run en cours.
Au moment d'envoyer une étape, si le runner imposé ne peut pas l'exécuter (supprimé, hors de l'espace, langage non pris en charge, étiquette requise absente),
l'étape ÉCHOUE avec un message clair : jamais de bascule silencieuse sur un autre runner. Hors ligne ou en pause, il est attendu (l'étape reste en file et la santé
de l'espace le dit). Ne sont concernées que les exécutions confiées à un worker d'exécution : les étapes IA, HTTP, SQL, LDAP, SMTP, SFTP et SSH restent sur la plateforme.
"""

from .models import Runner

RUNNER_KEY = "runner"


class PinRefused(Exception):
    """Le runner imposé ne peut pas exécuter cette étape : `code` et `message` (français) vont dans l'erreur de l'étape."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def info_of(runner: Runner, *, force_children: bool, inherited: bool) -> dict:
    return {"id": str(runner.pk), "name": runner.name, "force_children": force_children, "inherited": inherited}


def resolve_for_start(workflow, parent_run=None) -> dict | None:
    """Le runner effectif d'un run qui démarre, ou None (choix habituel). `parent_run` : le run qui lance celui-ci comme sous-workflow."""
    parent = ((parent_run.definition or {}).get(RUNNER_KEY) if parent_run is not None else None) or None
    if parent and parent.get("force_children"):
        return {
            **parent,
            "inherited": True,
        }  # imposé d'en haut : même si ce workflow en a un autre, et jusqu'au bout de la descendance
    if workflow is None:
        return None
    from workflows.models import Workflow

    fresh = (
        Workflow.objects.select_related("runner").filter(pk=workflow.pk).first()
    )  # le réglage d'AUJOURD'HUI, pas celui d'un objet gardé en mémoire
    if fresh is None or fresh.runner is None:
        return None
    return info_of(fresh.runner, force_children=bool(fresh.runner_force_children), inherited=False)


def _allowed_in(runner: Runner, workspace) -> bool:
    if runner.scope == Runner.Scope.DEDICATED:
        return runner.workspaces.filter(pk=workspace.pk).exists()
    return bool(getattr(workspace, "use_shared_runners", True))


def refusal(runner: Runner | None, name: str, workspace, action_version) -> PinRefused | None:
    """Pourquoi `runner` ne peut JAMAIS exécuter cette version d'action (None : il le peut, même s'il est hors ligne ou en pause pour l'instant)."""
    if runner is None:
        return PinRefused(
            "RUNNER_UNAVAILABLE",
            f"Le runner « {name} » imposé à ce workflow n'existe plus : choisissez-en un autre dans les réglages du workflow.",
        )
    if not _allowed_in(runner, workspace):
        return PinRefused(
            "RUNNER_UNAVAILABLE",
            f"Le runner « {runner.name} » imposé n'est pas ouvert à cet espace (runner dédié à d'autres espaces, ou runners partagés désactivés).",
        )
    if action_version.runtime not in (runner.runtimes or []):
        return PinRefused(
            "RUNNER_INCOMPATIBLE",
            f"Le runner « {runner.name} » imposé n'exécute pas le langage « {action_version.runtime} » de cette étape (il prend en charge : {', '.join(runner.runtimes or []) or 'aucun'}).",
        )
    missing = sorted(set(action_version.required_tags or []) - set(runner.tags or []))
    if missing:
        return PinRefused(
            "RUNNER_INCOMPATIBLE",
            f"Le runner « {runner.name} » imposé ne porte pas l'étiquette requise par cette étape : {', '.join(missing)}.",
        )
    return None


def for_dispatch(run, action_version) -> Runner | None:
    """Le runner à imposer à l'exécution de `action_version` dans `run` (None : aucun runner imposé). Lève `PinRefused` s'il ne pourra jamais l'exécuter."""
    info = (run.definition or {}).get(RUNNER_KEY)
    if not info:
        return None
    runner = Runner.objects.filter(pk=info["id"]).first()
    problem = refusal(runner, info.get("name", "?"), run.workspace, action_version)
    if problem is not None:
        raise problem
    return runner
