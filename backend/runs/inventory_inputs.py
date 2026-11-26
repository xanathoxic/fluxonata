"""Entrées d'une action Ansible : `inventory_ref` (l'identifiant d'un inventaire géré par la plate-forme, ADR 0037 lot 3) devient `inventory`, l'inventaire
Ansible rendu, AU MOMENT où l'exécution est prise en charge (jamais avant : c'est l'état courant de l'inventaire qui compte, et la liste des machines
n'est pas recopiée dans l'exécution enregistrée). L'inventaire est cherché dans l'espace de l'exécution, jamais ailleurs."""

from rest_framework import serializers

from catalog import ansible_roles as roles_lib
from catalog import inventory as inventory_lib
from catalog.models import Inventory


class InventoryError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def effective_inputs(execution) -> dict:
    inputs = execution.inputs
    if execution.action_version.runtime != "ansible" or "inventory_ref" not in inputs:
        return inputs
    if "inventory" in inputs:
        raise InventoryError("INVENTORY_CONFLICT", "Donnez « inventory_ref » ou « inventory », pas les deux.")
    ref = inputs["inventory_ref"]
    inv = Inventory.objects.filter(workspace=execution.workspace, slug=ref).first() if isinstance(ref, str) else None
    if inv is None:
        raise InventoryError("INVENTORY_NOT_FOUND", f"Inventaire introuvable dans cet espace : « {str(ref)[:60]} ».")
    rendered = inventory_lib.render(inv)
    if not (rendered["all"].get("hosts") or rendered["all"].get("children")):
        raise InventoryError("INVENTORY_EMPTY", f"L'inventaire « {inv.name} » ne contient aucune machine active.")
    return {**{k: v for k, v in inputs.items() if k != "inventory_ref"}, "inventory": rendered}


def effective_support_files(execution) -> dict[str, str]:
    """Les fichiers des rôles Ansible figés de la version d'action (`roles/<nom>/<chemin>`), à déposer dans le répertoire de travail ; vide pour tout autre langage."""
    if execution.action_version.runtime != "ansible" or not execution.action_version.ansible_roles:
        return {}
    try:
        return roles_lib.support_files(execution.action_version)
    except serializers.ValidationError as exc:
        raise InventoryError("ANSIBLE_ROLES", f"Rôles Ansible indisponibles : {exc.detail}"[:300]) from None
