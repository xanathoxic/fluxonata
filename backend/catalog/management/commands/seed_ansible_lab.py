"""Laboratoire Ansible (ADR 0037) : installe, dans un espace, tout ce qu'il faut pour valider de bout en bout l'implémentation Ansible contre de VRAIES machines SSH
(scripts/ansible-lab.sh up) : une connexion portant la clé SSH, des inventaires, des rôles, des actions-playbooks, un runner dédié et un workflow qui vérifie chaque point.

Ce que le workflow « lab-ansible-complet » éprouve, dans l'ordre : connexion SSH par clé du coffre et clés d'hôte vérifiées, inventaire géré (groupes, variables, machine désactivée),
machine injoignable sans faire échouer le workflow, mode vérification, accord d'une personne, déploiement par rôles (`roles:`, dépendances, handlers, modèles, `become`),
idempotence, `include_role` et assertions, `limit` et `tags`. Un dernier pas dresse la liste des points validés.

Idempotent ; `--purge` retire tout (y compris le runner et son jeton). Le dossier du laboratoire est celui de scripts/ansible_lab.py (clé, clés d'hôte, lab.json)."""

import base64
import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from catalog import ansible_roles as roles_lib
from catalog import inventory as inventory_lib
from catalog.models import Action, ActionVersion, AnsibleRole, AnsibleRoleVersion, Inventory
from catalog.removal import delete_action
from catalog.seed_helpers import layout, publish_workflow
from runs.models import Runner
from runs.runner_auth import new_token
from tenancy.models import Membership, Workspace
from vault.models import Connection, Secret
from workflows.models import Workflow
from workflows.removal import delete_workflow

FOLDER = "Démonstration/Ansible (laboratoire)"
RUNNER = "lab-ansible"
RUNNER_TAG = "ansible-lab"
CONNECTION = "lab-ssh"
SECRET = "lab-ssh-key"  # noqa: S105 - le nom du secret dans le coffre, pas sa valeur
WORKFLOW = "lab-ansible-complet"
WORKFLOW_INLINE = "lab-playbook-en-ligne"  # le playbook est écrit DANS le workflow (étape « Playbook Ansible »)
INVENTORY = "lab"
INVENTORY_PANNE = "lab-panne"
ACTIONS = ("lab-ping", "lab-nettoyer", "lab-deployer", "lab-controle")
ROLES = ("common", "web", "db", "controle")

# le contrat d'usage de chaque rôle du laboratoire : à quoi il sert, ce qu'il attend, ce qu'il produit, comment l'appeler
CONTRACTS = {
    "common": {
        "summary": "Prépare une machine du laboratoire : répertoire /opt/lab et fiche d'identité.",
        "description": "Crée /opt/lab puis écrit /opt/lab/identite.txt (nom de la machine, groupes, environnement, message). C'est le socle dont dépendent les rôles web et db.",
        "variables": [
            {
                "name": "lab_message",
                "type": "str",
                "required": False,
                "default": "bonjour",
                "description": "Le message écrit dans la fiche d'identité.",
            },
            {
                "name": "lab_env",
                "type": "str",
                "required": False,
                "default": "inconnu",
                "description": "Le nom de l'environnement, écrit dans la fiche.",
            },
        ],
        "outputs": [
            {
                "name": "journaliser",
                "description": "Message de journal (handler) émis quand la fiche est écrite ou modifiée.",
            }
        ],
        "example": "- hosts: all\n  roles:\n    - role: common\n      vars:\n        lab_message: bonjour\n",
    },
    "web": {
        "summary": "Installe la page web du laboratoire sur les serveurs web.",
        "description": "Génère la page d'accueil depuis un modèle et copie un fichier statique. Dépend du rôle common, qui est joué d'abord.",
        "variables": [
            {
                "name": "http_port",
                "type": "int",
                "required": False,
                "default": 80,
                "description": "Le port annoncé dans la page générée.",
            },
        ],
        "outputs": [],
        "example": "- hosts: web\n  roles:\n    - role: web\n      vars:\n        http_port: 8080\n",
    },
    "db": {
        "summary": "Pose la configuration de la base de données du laboratoire.",
        "description": "Crée /opt/lab/db et y génère db.conf depuis un modèle. Dépend du rôle common.",
        "variables": [
            {
                "name": "db_port",
                "type": "int",
                "required": False,
                "default": 5432,
                "description": "Le port d'écoute écrit dans la configuration.",
            },
        ],
        "outputs": [],
        "example": "- hosts: db\n  roles:\n    - role: db\n",
    },
    "controle": {
        "summary": "Vérifie ce que les autres rôles ont écrit sur la machine.",
        "description": "Relit la fiche d'identité, la page web et la configuration de la base, et échoue si l'un d'eux ne correspond pas. À appeler après common, web et db (par exemple avec include_role).",
        "variables": [],
        "outputs": [
            {
                "name": "controles",
                "description": "Les contrôles réalisés (identite, page, base), publiés avec set_stats dans les données du résultat.",
            }
        ],
        "example": "- hosts: all\n  tasks:\n    - ansible.builtin.include_role:\n        name: controle\n",
    },
}

# --- les rôles ----------------------------------------------------------------------------------------------------------------------------

COMMON = {
    "defaults/main.yml": "lab_message: bonjour\nlab_env: inconnu\n",
    "tasks/main.yml": """\
- name: Creer le repertoire du laboratoire
  ansible.builtin.file:
    path: /opt/lab
    state: directory
    mode: "0755"
  become: true

- name: Ecrire la fiche d'identite de la machine
  ansible.builtin.template:
    src: identite.j2
    dest: /opt/lab/identite.txt
    mode: "0644"
  become: true
  notify: journaliser
""",
    "handlers/main.yml": """\
- name: journaliser
  ansible.builtin.debug:
    msg: "fiche d'identite ecrite sur {{ inventory_hostname }}"
""",
    "templates/identite.j2": "machine: {{ inventory_hostname }}\ngroupes: {{ group_names | sort | join(',') }}\nenvironnement: {{ lab_env }}\nmessage: {{ lab_message }}\n",
}
WEB = {
    "meta/main.yml": "dependencies:\n  - role: common\n",
    "defaults/main.yml": "http_port: 80\n",
    "tasks/main.yml": """\
- name: Creer le repertoire web
  ansible.builtin.file:
    path: /opt/lab/web
    state: directory
    mode: "0755"
  become: true

- name: Generer la page depuis le modele
  ansible.builtin.template:
    src: index.html.j2
    dest: /opt/lab/web/index.html
    mode: "0644"
  become: true
  notify: annoncer_web

- name: Copier robots.txt
  ansible.builtin.copy:
    src: robots.txt
    dest: /opt/lab/web/robots.txt
    mode: "0644"
  become: true
""",
    "handlers/main.yml": """\
- name: annoncer_web
  ansible.builtin.debug:
    msg: "page web ecrite sur {{ inventory_hostname }} (port {{ http_port }})"
""",
    "templates/index.html.j2": "<h1>{{ inventory_hostname }}</h1>\n<p>port {{ http_port }} - {{ lab_message }}</p>\n",
    "files/robots.txt": "User-agent: *\nDisallow: /\n",
}
DB = {
    "meta/main.yml": "dependencies:\n  - role: common\n",
    "defaults/main.yml": "db_port: 5432\n",
    "tasks/main.yml": """\
- name: Creer le repertoire de la base
  ansible.builtin.file:
    path: /opt/lab/db
    state: directory
    mode: "0755"
  become: true

- name: Generer la configuration de la base
  ansible.builtin.template:
    src: db.conf.j2
    dest: /opt/lab/db/db.conf
    mode: "0644"
  become: true
""",
    "templates/db.conf.j2": "port = {{ db_port }}\nenvironnement = {{ lab_env }}\n",
}
CONTROLE = {
    "tasks/main.yml": """\
- name: Lire la fiche d'identite
  ansible.builtin.slurp:
    src: /opt/lab/identite.txt
  register: identite
  become: true

- name: La fiche nomme la machine
  ansible.builtin.assert:
    that:
      - "('machine: ' ~ inventory_hostname) in (identite.content | b64decode)"
    quiet: true

- name: Lire la page web
  ansible.builtin.slurp:
    src: /opt/lab/web/index.html
  register: page
  become: true
  when: "'web' in group_names"

- name: La page annonce le port du groupe
  ansible.builtin.assert:
    that:
      - "('port ' ~ http_port) in (page.content | b64decode)"
    quiet: true
  when: "'web' in group_names"

- name: Lire la configuration de la base
  ansible.builtin.slurp:
    src: /opt/lab/db/db.conf
  register: base
  become: true
  when: "'db' in group_names"

- name: La base ecoute sur le port du groupe
  ansible.builtin.assert:
    that:
      - "('port = ' ~ db_port) in (base.content | b64decode)"
    quiet: true
  when: "'db' in group_names"

- name: Publier les controles faits
  ansible.builtin.set_stats:
    data:
      controles: identite,page,base
  run_once: true
""",
}

# --- les playbooks --------------------------------------------------------------------------------------------------------------------------

PLAY_PING = """\
- name: Joindre les machines
  hosts: all
  gather_facts: false
  tasks:
    - name: Collecter l'essentiel
      ansible.builtin.setup:
        gather_subset:
          - min

    - name: Ping
      ansible.builtin.ping:

    - name: Publier le systeme
      ansible.builtin.set_stats:
        data:
          systeme: "{{ ansible_system }}"
      run_once: true
"""
PLAY_RESET = """\
- name: Remise a zero du laboratoire
  hosts: all
  gather_facts: false
  tasks:
    - name: Supprimer le repertoire du laboratoire
      ansible.builtin.file:
        path: /opt/lab
        state: absent
      become: true
"""
PLAY_DEPLOYER = """\
- name: Socle commun
  hosts: all
  gather_facts: false
  roles:
    - role: common
      vars:
        lab_message: "{{ message | default('bonjour') }}"

- name: Serveurs web
  hosts: web
  gather_facts: false
  roles:
    - role: web
      tags: [web]
      vars:
        lab_message: "{{ message | default('bonjour') }}"

- name: Bases de donnees
  hosts: db
  gather_facts: false
  roles:
    - role: db
      tags: [db]
      vars:
        lab_message: "{{ message | default('bonjour') }}"

- name: Bilan du deploiement
  hosts: all
  gather_facts: false
  tasks:
    - name: Publier les roles joues
      ansible.builtin.set_stats:
        data:
          roles_joues: common,web,db
      run_once: true
      tags: [always]
"""
PLAY_CONTROLE = """\
- name: Controles apres deploiement
  hosts: all
  gather_facts: false
  tasks:
    - name: Appeler le role de controle
      ansible.builtin.include_role:
        name: controle
"""

# --- le contrat des actions -------------------------------------------------------------------------------------------------------------------

INPUT_SCHEMA = {
    "type": "object",
    "required": ["inventory_ref", "known_hosts"],
    "properties": {
        "inventory_ref": {
            "type": "string",
            "title": "Inventaire",
            "description": "Identifiant de l'inventaire à cibler (Bibliothèque, Inventaires).",
        },
        "known_hosts": {
            "type": "string",
            "title": "Clés d'hôte",
            "description": "Les lignes known_hosts des machines : la connexion SSH refuse toute machine qui n'y figure pas.",
        },
        "user": {
            "type": "string",
            "title": "Compte SSH",
            "description": "Le compte avec lequel Ansible se connecte aux machines.",
        },
        "become": {
            "type": "boolean",
            "title": "Élévation de privilèges",
            "description": "Vrai : les tâches passent par sudo.",
        },
        "check": {
            "type": "boolean",
            "title": "Mode vérification",
            "description": "Vrai : Ansible dit ce qui changerait, sans rien modifier.",
        },
        "limit": {
            "type": "string",
            "title": "Limiter aux machines",
            "description": "Motif d'hôtes Ansible (ex. web) ; vide pour toutes les machines.",
        },
        "tags": {
            "type": "array",
            "items": {"type": "string"},
            "title": "Étiquettes",
            "description": "Ne joue que les tâches portant l'une de ces étiquettes.",
        },
        "extra_vars": {"type": "object", "title": "Variables", "description": "Variables du playbook (ex. message)."},
        "fail_on_error": {
            "type": "boolean",
            "title": "Échouer sur erreur",
            "description": "Faux : le résultat est rendu même si une machine est en échec ou injoignable.",
        },
        "timeout": {
            "type": "integer",
            "title": "Délai de connexion",
            "description": "Secondes avant de déclarer une machine injoignable (5 à 120).",
        },
    },
}
OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["ok", "hosts", "stats", "totals"],
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
            "description": "Ok, changé, échec, injoignable pour chaque machine.",
        },
        "totals": {
            "type": "object",
            "title": "Bilan total",
            "description": "Les mêmes compteurs, toutes machines réunies.",
        },
        "tasks": {"type": "array", "title": "Tâches", "description": "Une ligne par tâche et par machine."},
        "data": {
            "type": "object",
            "title": "Valeurs publiées",
            "description": "Ce que le playbook publie avec set_stats.",
        },
    },
}
ACTION_DEFS = {
    "lab-ping": (
        "Joindre les machines (Ansible)",
        "Se connecte en SSH aux machines de l'inventaire, collecte l'essentiel et répond « pong ».",
        PLAY_PING,
        [],
    ),
    "lab-nettoyer": (
        "Remettre le laboratoire à zéro (Ansible)",
        "Supprime le répertoire /opt/lab des machines : le workflow repart toujours d'un état propre.",
        PLAY_RESET,
        [],
    ),
    "lab-deployer": (
        "Déployer le laboratoire (Ansible)",
        "Joue les rôles common, web et db : fiche d'identité, page web et configuration de base, selon les groupes de l'inventaire.",
        PLAY_DEPLOYER,
        ["common", "web", "db"],
    ),
    "lab-controle": (
        "Contrôler le laboratoire (Ansible)",
        "Appelle le rôle controle avec include_role et vérifie, machine par machine, ce que le déploiement a écrit.",
        PLAY_CONTROLE,
        ["controle"],
    ),
}

VERDICT = """\
points = []


def point(nom, ok, detail):
    points.append({"nom": nom, "ok": bool(ok), "detail": detail})


def tot(r, cle):
    return int((r.get("totals") or {}).get(cle, 0))


point("Connexion SSH par clé du coffre, clés d'hôte vérifiées", ssh.get("ok") and tot(ssh, "unreachable") == 0 and tot(ssh, "failures") == 0,
      f"{ssh.get('hosts')} machine(s) jouées, système : {(ssh.get('data') or {}).get('systeme')}")
point("Inventaire géré : groupes, variables et machine désactivée exclue", ssh.get("hosts") == 3,
      f"{ssh.get('hosts')} machine(s) (3 attendues : web1, web2, db1 ; la machine désactivée n'est pas jouée)")
point("Machine injoignable sans faire échouer le workflow", panne.get("ok") is False and tot(panne, "unreachable") == 1,
      f"ok={panne.get('ok')}, injoignables={tot(panne, 'unreachable')}")
point("Remise à zéro (become) : le workflow repart d'un état propre", reset.get("ok") and tot(reset, "failures") == 0 and reset.get("hosts") == 3,
      f"{tot(reset, 'changed')} machine(s) nettoyée(s) sur {reset.get('hosts')}")
point("Mode vérification : annonce des changements sans rien modifier", essai.get("check") is True and essai.get("ok") and tot(essai, "changed") > 0,
      f"{tot(essai, 'changed')} changement(s) annoncé(s)")
point("Déploiement par rôles (roles:, dépendance, modèles, become)", deploiement.get("ok") and tot(deploiement, "changed") > 0 and tot(deploiement, "failures") == 0
      and (deploiement.get("data") or {}).get("roles_joues") == "common,web,db",
      f"{tot(deploiement, 'changed')} changement(s), rôles : {(deploiement.get('data') or {}).get('roles_joues')}")
point("Idempotence : un second passage ne change rien", idempotence.get("ok") and tot(idempotence, "changed") == 0 and tot(idempotence, "ok") > 0,
      f"{tot(idempotence, 'changed')} changement(s) au second passage")
point("include_role et assertions sur l'état réel des machines", controle.get("ok") and tot(controle, "failures") == 0
      and (controle.get("data") or {}).get("controles") == "identite,page,base",
      f"contrôles : {(controle.get('data') or {}).get('controles')}")
noms = {t.get("host") for t in (cible.get("tasks") or [])}
point("limit et tags : seules les machines web, seules les tâches web", cible.get("ok") and cible.get("hosts") == 2 and all("web" in n for n in noms) and len(noms) == 2,
      f"{cible.get('hosts')} machine(s) : {sorted(noms)}")
ok = all(p["ok"] for p in points)
valides = sum(1 for p in points if p["ok"])
resume = f"{valides} point(s) validé(s) sur {len(points)}"
"""


class Command(BaseCommand):
    help = "Installe le laboratoire Ansible (connexion SSH, inventaires, rôles, actions, runner dédié, workflow de validation)."

    def add_arguments(self, parser):
        parser.add_argument("--workspace", default="demo")
        parser.add_argument("--user", default="alice")
        parser.add_argument(
            "--lab-dir",
            default="",
            help="dossier du laboratoire (scripts/ansible-lab.sh up) ; par défaut var/ansible-lab",
        )
        parser.add_argument("--purge", action="store_true")

    # ------------------------------------------------------------------------------------------------------------------------------------------
    def handle(self, *args, workspace, user, lab_dir, purge, **opts):
        ws = Workspace.objects.filter(slug=workspace).first()
        if ws is None:
            raise CommandError(f"Espace inconnu : {workspace}")
        directory = Path(lab_dir) if lab_dir else Path(__file__).resolve().parents[4] / "var" / "ansible-lab"
        if purge:
            self._purge(ws, directory)
            return
        info_path = directory / "lab.json"
        if not info_path.exists():
            raise CommandError(f"{info_path} absent : lancez d'abord scripts/ansible-lab.sh up.")
        info = json.loads(info_path.read_text())
        member = Membership.objects.filter(workspace=ws, user__username=user).select_related("user").first()
        if member is None:
            raise CommandError(f"« {user} » n'est pas membre de l'espace {workspace}.")
        owner = member.user
        self._connection(ws, info)
        runner = self._runner(ws, directory)
        self._inventories(ws, owner, info)
        self._roles(ws, owner)
        self._actions(ws, owner)
        publish_workflow(
            ws,
            owner,
            WORKFLOW,
            "Laboratoire Ansible : validation complète",
            FOLDER,
            "Joue des playbooks et des rôles sur de vraies machines SSH (laboratoire) et vérifie chaque point : SSH, inventaire, rôles, idempotence, limit/tags, machine injoignable.",
            self.graph(info),
            changelog="Workflow de validation du laboratoire Ansible",
        )
        publish_workflow(
            ws,
            owner,
            WORKFLOW_INLINE,
            "Laboratoire Ansible : playbook écrit dans le workflow",
            FOLDER,
            "Un playbook écrit directement dans le workflow (étape « Playbook Ansible »), qui appelle les rôles common et web de la bibliothèque et se joue en mode vérification sur les machines du laboratoire.",
            self.graph_inline(info),
            changelog="Étape Playbook Ansible du laboratoire",
        )
        self.stdout.write(
            f"Installé dans « {workspace} » : runner « {runner.name} » (étiquette {RUNNER_TAG}), connexion « {CONNECTION} », inventaires « {INVENTORY} » et « {INVENTORY_PANNE} », "
            f"rôles {', '.join(ROLES)}, actions {', '.join(ACTIONS)}, workflows « {WORKFLOW} » et « {WORKFLOW_INLINE} ».\n"
            "Démarrez le runner : scripts/ansible-lab.sh agent-up"
        )

    # ------------------------------------------------------------------------------------------------------------------------------------------
    def _connection(self, ws, info) -> None:
        key = base64.b64encode(
            Path(info["key"]).read_bytes()
        ).decode()  # le fichier d'environnement refuse le multi-lignes : base64 sur une ligne
        secret, _ = Secret.objects.get_or_create(workspace=ws, name=SECRET)
        secret.set_value(key)
        secret.save()
        Connection.objects.get_or_create(
            workspace=ws,
            name=CONNECTION,
            defaults={"label": "Laboratoire SSH (clé du compte deploy)", "kind": "generic", "secret": secret},
        )

    def _runner(self, ws, directory: Path) -> Runner:
        token_file = directory / "runner.token"
        runner = Runner.objects.filter(name=RUNNER).first()
        if runner is None or not token_file.exists():
            raw, digest, prefix = new_token()
            if runner is None:
                runner = Runner.objects.create(
                    name=RUNNER,
                    scope="dedicated",
                    runtimes=["ansible"],
                    tags=[RUNNER_TAG, "ansible"],
                    token_hash=digest,
                    token_prefix=prefix,
                    max_concurrent=2,
                )
            else:
                runner.token_hash, runner.token_prefix = digest, prefix
                runner.save(update_fields=["token_hash", "token_prefix"])
            token_file.write_text(raw)
            token_file.chmod(0o600)
        runner.workspaces.add(ws)
        return runner

    def _inventories(self, ws, owner, info) -> None:
        common = {"ansible_user": info["user"], "ansible_port": 22, "ansible_python_interpreter": "/usr/bin/python3"}
        names = {m["short"]: m["name"] for m in info["machines"]}
        hosts = [
            {"name": m["name"], "variables": {"lab_nom": m["short"]}, "enabled": True, "groups": [m["group"]]}
            for m in info["machines"]
        ]
        hosts.append(
            {"name": "flx-lab-eteinte", "variables": {}, "enabled": False, "groups": ["web"]}
        )  # désactivée : jamais jouée
        doc = {
            "variables": {**common, "lab_env": "laboratoire"},
            "groups": [
                {"name": "web", "variables": {"http_port": 8080}, "children": []},
                {"name": "db", "variables": {"db_port": 5433}, "children": []},
                {"name": "labo", "variables": {}, "children": ["web", "db"]},
            ],
            "hosts": hosts,
        }
        panne = {
            "variables": {**common, "lab_env": "laboratoire"},
            "groups": [{"name": "web", "variables": {"http_port": 8080}, "children": []}],
            "hosts": [
                {"name": names["web1"], "variables": {}, "enabled": True, "groups": ["web"]},
                {
                    "name": "flx-lab-absente",
                    "variables": {},
                    "enabled": True,
                    "groups": ["web"],
                },  # n'existe pas : injoignable
            ],
        }
        for slug, name, description, body in (
            (INVENTORY, "Laboratoire", "Trois machines SSH jetables (web1, web2, db1), une machine désactivée.", doc),
            (
                INVENTORY_PANNE,
                "Laboratoire avec une panne",
                "Une machine du laboratoire et une machine qui n'existe pas.",
                panne,
            ),
        ):
            inv, _ = Inventory.objects.get_or_create(
                workspace=ws, slug=slug, defaults={"name": name, "description": description, "created_by": owner}
            )
            inventory_lib.save_document(
                inv, inventory_lib.clean_document(body)
            )  # rejoué : l'inventaire retrouve toujours l'état attendu

    def _roles(self, ws, owner) -> None:
        for name, files, description in (
            ("common", COMMON, "Socle commun : répertoire /opt/lab et fiche d'identité de la machine."),
            ("web", WEB, "Serveur web : page générée depuis un modèle et fichier copié ; dépend de common."),
            ("db", DB, "Base de données : configuration générée depuis un modèle ; dépend de common."),
            ("controle", CONTROLE, "Contrôles : relit les fichiers écrits par les autres rôles et les vérifie."),
        ):
            role, _ = AnsibleRole.objects.get_or_create(
                workspace=ws, name=name, defaults={"description": description, "owner": owner}
            )
            contract = roles_lib.check_contract(CONTRACTS[name])
            if not role.versions.exists():
                AnsibleRoleVersion.objects.create(
                    role=role,
                    version="1.0.0",
                    files=roles_lib.check_files(files),
                    changelog="Rôle du laboratoire",
                    contract=contract,
                    created_by=owner,
                )
            else:  # une démo plus ancienne n'a pas de contrat : il arrive avec une version de plus (les actions gardent la version qu'elles ont figée)
                latest = max(role.versions.all(), key=lambda v: roles_lib.semver_key(v.version))
                if not latest.contract:
                    major, minor, patch = roles_lib.semver_key(latest.version)
                    AnsibleRoleVersion.objects.create(
                        role=role,
                        version=f"{major}.{minor}.{patch + 1}",
                        files=latest.files,
                        changelog="Contrat d'usage",
                        contract=contract,
                        created_by=owner,
                    )

    def _actions(self, ws, owner) -> None:
        for slug, (name, description, playbook, roles) in ACTION_DEFS.items():
            action, _ = Action.objects.get_or_create(
                workspace=ws,
                slug=slug,
                defaults={
                    "name": name,
                    "description": description,
                    "domain": "Ansible",
                    "tags": ["ansible", "laboratoire"],
                    "owner": owner,
                    "folder": FOLDER,
                },
            )
            if action.versions.exists():
                continue
            ActionVersion.objects.create(
                action=action,
                version="1.0.0",
                runtime="ansible",
                source=playbook,
                input_schema=INPUT_SCHEMA,
                output_schema=OUTPUT_SCHEMA,
                limits={"timeout_seconds": 300, "memory_mb": 256},
                side_effects="external",
                idempotent=True,
                network_policy="targets",
                required_tags=[RUNNER_TAG],
                secrets_required=[{"slot": "ssh", "env": "FLX_SSH_KEY_B64"}],
                ansible_roles=roles_lib.resolve_declared(ws, [{"role": r, "version": "latest"} for r in roles]),
                created_by=owner,
                changelog="Action du laboratoire Ansible",
            )

    # ------------------------------------------------------------------------------------------------------------------------------------------
    def graph(self, info) -> dict:
        known = info["known_hosts"]

        def step(nid, name, action, inventory=INVENTORY, **inputs):
            return {
                "id": nid,
                "type": "action",
                "name": name,
                "action": action,
                "connections": {"ssh": CONNECTION},
                "inputs": {"inventory_ref": inventory, "known_hosts": known, "user": info["user"], **inputs},
            }

        message = {"message": "{{ inputs.message }}"}
        nodes: list[dict] = [
            step("ssh", "1. Joindre les machines (SSH, clé du coffre)", "lab-ping"),
            step(
                "panne",
                "2. Une machine injoignable (le workflow continue)",
                "lab-ping",
                inventory=INVENTORY_PANNE,
                fail_on_error=False,
                timeout=5,
            ),
            step("reset", "3. Remettre les machines à zéro (become)", "lab-nettoyer", become=True),
            step(
                "essai", "4. Essai à blanc du déploiement", "lab-deployer", check=True, become=True, extra_vars=message
            ),
            {
                "id": "accord",
                "type": "approval",
                "name": "5. Accord avant déploiement",
                "title": "Déployer sur {{ steps.essai.output.hosts }} machine(s) ?",
                "instructions": "L'essai à blanc a annoncé {{ steps.essai.output.totals.changed }} changement(s). Aucune machine n'a été modifiée.",
            },
            step("deploiement", "6. Déploiement réel (rôles, become)", "lab-deployer", become=True, extra_vars=message),
            step("idempotence", "7. Second passage (idempotence)", "lab-deployer", become=True, extra_vars=message),
            step("controle", "8. Contrôles (include_role)", "lab-controle", become=True),
            step(
                "cible",
                "9. Limiter aux machines web (limit, tags)",
                "lab-deployer",
                check=True,
                become=True,
                limit="web",
                tags=["web"],
                extra_vars=message,
            ),
            {
                "id": "bilan",
                "type": "script",
                "name": "10. Bilan : chaque point validé ?",
                "language": "python",
                "script": VERDICT,
                "bindings": {
                    "ssh": "{{ steps.ssh.output }}",
                    "panne": "{{ steps.panne.output }}",
                    "reset": "{{ steps.reset.output }}",
                    "essai": "{{ steps.essai.output }}",
                    "deploiement": "{{ steps.deploiement.output }}",
                    "idempotence": "{{ steps.idempotence.output }}",
                    "controle": "{{ steps.controle.output }}",
                    "cible": "{{ steps.cible.output }}",
                },
                "outputs": [
                    {"name": "ok", "type": "boolean"},
                    {"name": "resume", "type": "string"},
                    {"name": "points", "type": "array"},
                ],
                "timeout_seconds": 30,
            },
        ]
        ids = [n["id"] for n in nodes]
        edges = [{"from": a, "to": b} for a, b in zip(ids, ids[1:], strict=False)]
        layout(nodes, edges)
        return {
            "inputs": {
                "type": "object",
                "required": ["message"],
                "properties": {
                    "message": {
                        "type": "string",
                        "title": "Message",
                        "description": "Texte écrit sur les machines par les rôles.",
                    }
                },
            },
            "nodes": nodes,
            "edges": edges,
            "outputs": {
                "valide": "{{ steps.bilan.output.ok }}",
                "resume": "{{ steps.bilan.output.resume }}",
                "points": "{{ steps.bilan.output.points }}",
            },
        }

    def graph_inline(self, info) -> dict:
        """Un seul pas : le playbook est dans l'étape, appelle deux rôles de la bibliothèque (figés à la publication) et se joue en mode vérification."""
        nodes: list[dict] = [
            {
                "id": "deployer",
                "type": "ansible",
                "name": "Déployer le socle et la page web (vérification)",
                "playbook": "- name: Socle commun et page web\n  hosts: all\n  gather_facts: false\n  tasks:\n    - ansible.builtin.include_role:\n        name: common\n    - ansible.builtin.include_role:\n        name: web\n      when: \"'web' in group_names\"\n",
                "roles": [{"role": "common", "version": "latest"}, {"role": "web", "version": "latest"}],
                "required_tags": [RUNNER_TAG],
                "connections": {"ssh": CONNECTION},
                "inputs": {
                    "inventory_ref": INVENTORY,
                    "known_hosts": info["known_hosts"],
                    "user": info["user"],
                    "become": True,
                    "check": True,
                    "extra_vars": {"lab_message": "{{ inputs.message }}"},
                },
                "timeout_seconds": 300,
            }
        ]
        layout(nodes, [])
        return {
            "inputs": {
                "type": "object",
                "required": ["message"],
                "properties": {
                    "message": {
                        "type": "string",
                        "title": "Message",
                        "description": "Texte écrit sur les machines par les rôles.",
                    }
                },
            },
            "nodes": nodes,
            "edges": [],
            "outputs": {"ok": "{{ steps.deployer.output.ok }}", "machines": "{{ steps.deployer.output.hosts }}"},
        }

    # ------------------------------------------------------------------------------------------------------------------------------------------
    def _purge(self, ws, directory: Path) -> None:
        for w in Workflow.objects.filter(workspace=ws, slug__in=(WORKFLOW, WORKFLOW_INLINE)):
            delete_workflow(w, with_history=True, with_triggers=True)
        for a in Action.objects.filter(workspace=ws, slug__in=ACTIONS):
            delete_action(a, with_history=True)
        AnsibleRole.objects.filter(workspace=ws, name__in=ROLES).delete()
        Inventory.objects.filter(workspace=ws, slug__in=(INVENTORY, INVENTORY_PANNE)).delete()
        Connection.objects.filter(workspace=ws, name=CONNECTION).delete()
        Secret.objects.filter(workspace=ws, name=SECRET).delete()
        Runner.objects.filter(name=RUNNER).delete()
        (directory / "runner.token").unlink(missing_ok=True)
        self.stdout.write("Laboratoire Ansible retiré de l'espace.")
