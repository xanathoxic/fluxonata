"""Ce qui dépend d'une action ou d'un workflow qu'on veut supprimer, et comment l'emporter avec lui SUR DEMANDE EXPRESSE.

Par défaut, rien de tout cela n'est supprimé : l'historique d'exécutions et les déclencheurs (planifications, webhooks, formulaires publics, interrogations)
protègent l'élément. Deux options le permettent, chacune à confirmer par la personne :
  - `with_history`  : supprime aussi les exécutions (et leurs journaux, tentatives, artefacts, fichiers) — définitif ;
  - `with_triggers` : supprime aussi les déclencheurs du workflow (voir `workflows.removal`).
Une exécution encore en cours bloque toujours : il faut l'annuler d'abord.
"""

import logging
from collections import Counter

from django.db import transaction
from rest_framework import serializers

from runs.artifacts import get_store
from runs.models import Artifact, Execution

log = logging.getLogger(__name__)
TERMINAL = ("succeeded", "failed", "cancelled")


def action_facts(action) -> dict:
    qs = Execution.objects.filter(action_version__action=action)
    return {"executions": qs.count(), "running": qs.exclude(status__in=TERMINAL).count(), "triggers": {}}


def _artifact_keys(executions) -> list[str]:
    return list(Artifact.objects.filter(execution__in=executions).values_list("storage_key", flat=True))


def _drop_files(keys: list[str]) -> None:
    store = get_store()
    for key in keys:
        try:
            store.delete(key)
        except Exception:  # noqa: BLE001 - the rows are gone; an orphan file is a leftover to report, never a reason to fail
            log.warning("fichier d'artefact non supprimé du stockage : %s", key)


def purge_action_history(action) -> tuple[Counter, list[str]]:
    executions = Execution.objects.filter(action_version__action=action)
    counts = Counter(executions=executions.count())
    keys = _artifact_keys(executions)
    executions.delete()
    return counts, keys


def refuse_if_running(facts: dict, noun: str) -> None:
    if facts["running"]:
        n = facts["running"]
        raise serializers.ValidationError(
            {
                "detail": f"Suppression refusée : {n} exécution{'s' if n > 1 else ''} de {noun} "
                f"{'sont' if n > 1 else 'est'} encore en cours. Annulez-{'les' if n > 1 else 'la'} d'abord."
            }
        )


def delete_action(action, *, with_history: bool) -> dict:
    facts = action_facts(action)
    if facts["executions"] and not with_history:
        raise serializers.ValidationError(
            {
                "action": "Suppression refusée : cette action a déjà été exécutée (l'historique en dépend). Dépréciez-la plutôt, ou confirmez la suppression de l'historique."
            }
        )
    if with_history:
        refuse_if_running(facts, "cette action")
    keys: list[str] = []
    removed = 0
    with transaction.atomic():
        if with_history:
            counts, keys = purge_action_history(action)
            removed = counts["executions"]
        action.delete()
    _drop_files(keys)
    return {"history": removed}
