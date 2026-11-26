"""MCP de CONCEPTION — Ansible : rôles (avec leur contrat d'usage) et inventaires, en LECTURE (ADR 0037, mise à jour du 2026-11-26).

Un assistant externe peut LIRE les rôles Ansible de l'espace (fichiers et contrat d'usage d'une version), les inventaires (machines, groupes, variables ; jamais
d'identifiant : la plateforme les refuse dans un inventaire) et demander une ÉBAUCHE de contrat tirée de `defaults/main.yml`. Il peut proposer une action Ansible
(`propose_action`, runtime « ansible ») qui appelle ces rôles et désigne un inventaire par son identifiant.

Ce qui reste interdit, par construction : créer ou publier un rôle (c'est du code joué sur des machines et une version publiée ne change plus), créer ou
modifier un inventaire (il décide QUELLES machines un playbook atteint), lancer un playbook. Les personnes le font dans l'interface. Ranger un rôle ou un
inventaire dans un dossier passe par `update_item_info` (voir `mcp_design_organize`)."""

from django.http import Http404

from catalog import ansible_roles as roles_lib
from catalog import inventory as inventory_lib
from catalog.models import AnsibleRole, AnsibleRoleVersion, Inventory

from .mcp_design_common import LIMIT, QUERY, SLUG, _limit, _match, _ok, _spec, _text

MAX_FILES_CHARS = 120_000  # code d'un rôle renvoyé en une fois


def _latest(role: AnsibleRole) -> AnsibleRoleVersion | None:
    return max(role.versions.all(), key=lambda v: roles_lib.semver_key(v.version), default=None)


def _role_row(role: AnsibleRole) -> dict:
    latest = _latest(role)
    contract = latest.contract if latest else {}
    return {
        "name": role.name,
        "summary": contract.get("summary", ""),
        "description": role.description,
        "folder": role.folder,
        "latest_version": latest.version if latest else None,
        "versions": sorted((v.version for v in role.versions.all()), key=roles_lib.semver_key, reverse=True),
        "variables": [v["name"] for v in contract.get("variables", [])],
        "contract_ok": not roles_lib.contract_problems(contract),
        "used_by": roles_lib.roles_in_use(role),
    }


def list_ansible_roles(request, ws, args):
    q = _text(args)
    rows = [
        _role_row(r)
        for r in AnsibleRole.objects.filter(workspace=ws).prefetch_related("versions")
        if _match(q, r.name, r.description, r.folder, _latest_summary(r))
    ]
    return _ok({"count": len(rows), "results": rows[: _limit(args)]})


def _latest_summary(role: AnsibleRole) -> str:
    latest = _latest(role)
    return (latest.contract or {}).get("summary", "") if latest else ""


def get_ansible_role(request, ws, args):
    name = args.get("name")
    role = (
        AnsibleRole.objects.filter(workspace=ws, name=name).prefetch_related("versions").first()
        if isinstance(name, str)
        else None
    )
    if role is None:
        raise Http404()
    wanted = args.get("version")
    version = (
        next((v for v in role.versions.all() if v.version == wanted), None)
        if isinstance(wanted, str) and wanted
        else _latest(role)
    )
    if version is None:
        raise Http404()
    files, shown, truncated = {}, 0, False
    for path in sorted(version.files):
        text = version.files[path]
        if shown + len(text) > MAX_FILES_CHARS:
            truncated = True
            break
        files[path] = text
        shown += len(text)
    return _ok(
        {
            **_role_row(role),
            "version": version.version,
            "digest": version.digest,
            "changelog": version.changelog,
            "contract": version.contract,
            "contract_problems": roles_lib.contract_problems(version.contract),
            "files": files,
            "files_truncated": truncated,
            "dependencies": roles_lib.dependencies(version.files),
            "usage": f'Dans un playbook : roles: [{role.name}] ou include_role. Dans une action Ansible, déclarez-le : ansible_roles: [{{"role": "{role.name}", "version": "{version.version}"}}].',
        }
    )


def draft_role_contract(request, ws, args):
    files = roles_lib.check_files(args.get("files"))
    return _ok(
        {
            "variables": roles_lib.draft_contract(files).get("variables", []),
            "note": "Ébauche à compléter : descriptions, types et caractère obligatoire.",
        }
    )


def _inventory_row(inv: Inventory) -> dict:
    return {
        "slug": inv.slug,
        "name": inv.name,
        "description": inv.description,
        "folder": inv.folder,
        "hosts": inv.hosts.count(),
        "groups": inv.groups.count(),
    }


def list_inventories(request, ws, args):
    q = _text(args)
    rows = [
        _inventory_row(i)
        for i in Inventory.objects.filter(workspace=ws)
        if _match(q, i.slug, i.name, i.description, i.folder)
    ]
    return _ok({"count": len(rows), "results": rows[: _limit(args)]})


def get_inventory(request, ws, args):
    slug = args.get("slug")
    inv = Inventory.objects.filter(workspace=ws, slug=slug).first() if isinstance(slug, str) else None
    if inv is None:
        raise Http404()
    return _ok(
        {
            **_inventory_row(inv),
            "document": inventory_lib.load_document(inv),
            "usage": f"Dans une action Ansible, passez inventory_ref: \"{inv.slug}\" (entrée de l'action) : la plateforme le lit au démarrage de l'exécution.",
        }
    )


def read_tools() -> dict:
    return {
        "list_ansible_roles": (
            _spec(
                "list_ansible_roles",
                "Lister les rôles Ansible",
                "Rôles Ansible de la bibliothèque : nom, phrase d'usage, versions, variables, contrat complet ou non, nombre d'usages. À lire AVANT d'écrire un playbook : réutilisez un rôle existant plutôt que de réécrire ses tâches.",
                QUERY | LIMIT,
            ),
            list_ansible_roles,
        ),
        "get_ansible_role": (
            _spec(
                "get_ansible_role",
                "Lire un rôle Ansible",
                "Une version d'un rôle : son contrat d'usage (à quoi il sert, variables avec type et défaut, résultats, exemple d'appel), ses dépendances et ses fichiers. Sans `version` : la dernière.",
                {
                    "name": {"type": "string", "description": "Nom du rôle."},
                    "version": {"type": "string", "description": "Version (ex. 1.0.1) ; défaut : la dernière."},
                },
                ["name"],
            ),
            get_ansible_role,
        ),
        "draft_role_contract": (
            _spec(
                "draft_role_contract",
                "Ébaucher le contrat d'un rôle",
                "Tire une ébauche de contrat (variables, types, valeurs par défaut) de `defaults/main.yml` d'un ensemble de fichiers de rôle {chemin: texte}. Ne crée rien : sert à rédiger le contrat que vous proposerez à une personne.",
                {
                    "files": {
                        "type": "object",
                        "description": 'Fichiers du rôle : {"tasks/main.yml": "…", "defaults/main.yml": "…"}.',
                    }
                },
                ["files"],
            ),
            draft_role_contract,
        ),
        "list_inventories": (
            _spec(
                "list_inventories",
                "Lister les inventaires Ansible",
                "Inventaires Ansible de l'espace : identifiant, nom, dossier, nombre de machines et de groupes.",
                QUERY | LIMIT,
            ),
            list_inventories,
        ),
        "get_inventory": (
            _spec(
                "get_inventory",
                "Lire un inventaire Ansible",
                "Machines, groupes (imbriqués) et variables d'un inventaire, tels qu'enregistrés. Aucun identifiant n'y figure : les clés et mots de passe passent par les connexions de l'action.",
                {"slug": SLUG},
                ["slug"],
            ),
            get_inventory,
        ),
    }
