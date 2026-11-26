"""Workflow de test du runtime Ansible (ADR 0037) : un inventaire local, une action « playbook » et un workflow qui l'essaie en mode vérification, demande
l'accord d'une personne, puis l'applique. Tout se joue dans le bac à sable (connexion `local`) : aucune machine à joindre, aucun identifiant.

Prérequis pour l'exécuter : l'image `fluxonata-runner-ansible:local` (scripts/build-ansible-runner.sh) et un worker qui porte l'étiquette `ansible` (ORCH_WORKER_TAGS=ansible).
Idempotent ; `--purge` retire ce que la commande a installé."""

from django.core.management.base import BaseCommand, CommandError

from catalog import inventory as inventory_lib
from catalog.models import Action, ActionVersion, Inventory
from catalog.seed_helpers import layout, publish_workflow
from tenancy.models import Membership, Workspace
from workflows.models import Workflow

FOLDER = "Démonstration/Ansible"
INVENTORY = "labo-local"
ACTION = "ansible-etat-machine"
WORKFLOW = "test-ansible"

PLAYBOOK = """\
# Playbook de démonstration : ne touche qu'au bac à sable du conteneur (connexion locale).
- name: Etat de la machine
  hosts: all
  gather_facts: false
  tasks:
    - name: Qui suis-je ?
      ansible.builtin.command: id -un
      register: qui
      changed_when: false

    - name: Poser le fichier témoin
      ansible.builtin.copy:
        dest: /tmp/fluxonata-test-ansible.txt
        content: "{{ message }}\\n"
        mode: "0600"

    - name: Annoncer le message
      ansible.builtin.debug:
        msg: "{{ message }} (compte {{ qui.stdout }}, machine {{ inventory_hostname }})"

    - name: Publier le résultat
      ansible.builtin.set_stats:
        data:
          fichier: /tmp/fluxonata-test-ansible.txt
          compte: "{{ qui.stdout }}"
"""

INPUT_SCHEMA = {
    "type": "object",
    "required": ["inventory_ref"],
    "properties": {
        "inventory_ref": {
            "type": "string",
            "title": "Inventaire",
            "description": "Identifiant de l'inventaire à cibler (Bibliothèque → Inventaires).",
        },
        "extra_vars": {
            "type": "object",
            "title": "Variables",
            "description": "Variables du playbook : « message » est le texte à écrire.",
        },
        "check": {
            "type": "boolean",
            "title": "Mode vérification",
            "description": "Vrai : Ansible dit ce qui changerait sans rien modifier.",
        },
        "limit": {
            "type": "string",
            "title": "Limiter aux machines",
            "description": "Motif d'hôtes Ansible (ex. serveur-test) ; vide = toutes.",
        },
    },
}
OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["ok", "hosts", "stats"],
    "properties": {
        "ok": {
            "type": "boolean",
            "title": "Réussi",
            "description": "Vrai si aucune machine n'est en échec ou injoignable.",
        },
        "check": {
            "type": "boolean",
            "title": "Vérification",
            "description": "Vrai si le playbook a tourné en mode vérification.",
        },
        "hosts": {"type": "integer", "title": "Machines", "description": "Nombre de machines jouées."},
        "stats": {
            "type": "object",
            "title": "Bilan par machine",
            "description": "Ok, changé, échec, injoignable, ignoré par machine.",
        },
        "tasks": {"type": "array", "title": "Tâches", "description": "Une ligne par tâche et par machine."},
        "data": {
            "type": "object",
            "title": "Valeurs publiées",
            "description": "Ce que le playbook publie avec set_stats.",
        },
    },
}

INVENTORY_DOC = {
    "variables": {"env": "labo"},
    "groups": [{"name": "labo", "variables": {}, "children": []}],
    "hosts": [
        {"name": "serveur-test", "variables": {"ansible_connection": "local"}, "enabled": True, "groups": ["labo"]},
        {"name": "serveur-eteint", "variables": {"ansible_connection": "local"}, "enabled": False, "groups": ["labo"]},
    ],
}


def graph() -> dict:
    nodes: list[dict] = [
        {
            "id": "essai",
            "type": "action",
            "name": "Essai à blanc",
            "action": ACTION,
            "inputs": {"inventory_ref": INVENTORY, "check": True, "extra_vars": {"message": "{{ inputs.message }}"}},
        },
        {
            "id": "accord",
            "type": "approval",
            "name": "Accord avant application",
            "title": "Appliquer le playbook sur {{ steps.essai.output.hosts }} machine(s) ?",
            "instructions": "L'essai à blanc est terminé : consultez ses tâches dans le détail de l'exécution (« changé » = ce qui serait modifié).",
        },
        {
            "id": "application",
            "type": "action",
            "name": "Application réelle",
            "action": ACTION,
            "inputs": {"inventory_ref": INVENTORY, "check": False, "extra_vars": {"message": "{{ inputs.message }}"}},
        },
    ]
    edges = [{"from": "essai", "to": "accord"}, {"from": "accord", "to": "application"}]
    layout(nodes, edges)
    return {
        "inputs": {
            "type": "object",
            "required": ["message"],
            "properties": {
                "message": {
                    "type": "string",
                    "title": "Message",
                    "description": "Texte écrit par le playbook dans /tmp du conteneur.",
                }
            },
        },
        "nodes": nodes,
        "edges": edges,
        "outputs": {
            "machines": "{{ steps.application.output.hosts }}",
            "compte": "{{ steps.application.output.data.compte }}",
            "fichier": "{{ steps.application.output.data.fichier }}",
        },
    }


class Command(BaseCommand):
    help = "Installe le workflow de test Ansible (inventaire local, action, workflow essai → accord → application)."

    def add_arguments(self, parser):
        parser.add_argument("--workspace", default="demo")
        parser.add_argument("--user", default="alice", help="Concepteur propriétaire des objets créés.")
        parser.add_argument("--purge", action="store_true")

    def handle(self, *args, workspace, user, purge, **opts):
        ws = Workspace.objects.filter(slug=workspace).first()
        if ws is None:
            raise CommandError(f"Espace inconnu : {workspace}")
        if purge:
            Workflow.objects.filter(workspace=ws, slug=WORKFLOW).delete()
            Inventory.objects.filter(workspace=ws, slug=INVENTORY).delete()
            Action.objects.filter(workspace=ws, slug=ACTION).delete()  # ses versions suivent (cascade)
            self.stdout.write("Workflow de test Ansible retiré.")
            return
        member = Membership.objects.filter(workspace=ws, user__username=user).select_related("user").first()
        if member is None:
            raise CommandError(f"« {user} » n'est pas membre de l'espace {workspace}.")
        owner = member.user
        inv, _ = Inventory.objects.get_or_create(
            workspace=ws,
            slug=INVENTORY,
            defaults={
                "name": "Laboratoire local",
                "description": "Une machine locale pour essayer Ansible.",
                "created_by": owner,
            },
        )
        if not inv.hosts.exists():
            inventory_lib.save_document(inv, inventory_lib.clean_document(INVENTORY_DOC))
        action, _ = Action.objects.get_or_create(
            workspace=ws,
            slug=ACTION,
            defaults={
                "name": "Etat d'une machine (Ansible)",
                "description": "Joue un playbook de démonstration sur les machines d'un inventaire : pose un fichier témoin et publie le compte utilisé.",
                "domain": "Ansible",
                "tags": ["ansible", "test"],
                "owner": owner,
                "folder": FOLDER,
            },
        )
        ActionVersion.objects.get_or_create(
            action=action,
            version="1.0.0",
            defaults={
                "runtime": "ansible",
                "source": PLAYBOOK,
                "input_schema": INPUT_SCHEMA,
                "output_schema": OUTPUT_SCHEMA,
                "limits": {"timeout_seconds": 120, "memory_mb": 256},
                "side_effects": "external",
                "idempotent": True,
                "network_policy": "none",
                "required_tags": ["ansible"],
                "created_by": owner,
            },
        )
        publish_workflow(
            ws,
            owner,
            WORKFLOW,
            "Test Ansible : essai, accord, application",
            FOLDER,
            "Joue un playbook sur l'inventaire local : essai à blanc, accord d'une personne, puis application réelle.",
            graph(),
            changelog="Workflow de test du runtime Ansible",
        )
        self.stdout.write(
            f"Installé dans « {workspace} » : inventaire « {INVENTORY} », action « {ACTION} », workflow « {WORKFLOW} ». "
            "Il faut l'image fluxonata-runner-ansible:local et un worker avec ORCH_WORKER_TAGS=ansible."
        )
