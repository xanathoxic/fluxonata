"""ADR 0025 — une action ou une skill « proposée » (par un assistant externe) n'est JAMAIS exécutée avant relecture humaine.

Deux barrages : `refuse_proposed_in_graph` à la création d'un run (refus net, avant tout effet) et `refuse_proposed_version` à la création
d'une exécution (dernier recours : toute exécution passe par `services.enqueue`). Le statut ne revient jamais à « proposée » après activation.
"""

from rest_framework.exceptions import PermissionDenied

PROPOSED = "proposed"


class ProposedResource(PermissionDenied):
    default_code = "proposed_resource"


def refuse_proposed_version(action_version) -> None:
    action = action_version.action
    if action.status == PROPOSED:
        raise ProposedResource(
            f"L'action « {action.name} » est une proposition d'assistant : elle doit être relue et activée par une personne avant toute exécution."
        )


def refuse_proposed_in_graph(workspace, graph: dict) -> None:
    """Refuse de démarrer un run dont le graphe utilise une action ou une skill proposée (étapes, compensations, outils d'agent)."""
    from ai.models import AiSkill
    from catalog.models import Action

    from . import agent

    actions, skills = set(), set()
    for n in graph.get("nodes", []):
        if n.get("type") == "action" and n.get("action"):
            actions.add(n["action"])
            if (n.get("compensation") or {}).get("action"):
                actions.add(n["compensation"]["action"])
        elif n.get("type") == "agent":
            actions.update(agent.tool_slugs(n))
        elif n.get("type") == "ai" and n.get("skill"):
            skills.add(n["skill"])
    bad = list(
        Action.objects.filter(workspace=workspace, slug__in=actions, status=PROPOSED).values_list("name", flat=True)
    )
    bad += list(
        AiSkill.objects.filter(workspace=workspace, slug__in=skills, status=PROPOSED).values_list("name", flat=True)
    )
    if bad:
        raise ProposedResource(
            "Ce workflow utilise des propositions d'assistant non relues : "
            + ", ".join(sorted(bad))
            + ". Relisez-les et activez-les avant de lancer."
        )
