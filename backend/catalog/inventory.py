"""Inventaires Ansible gérés par la plate-forme (ADR 0037, lot 3) : validation, enregistrement, rendu pour `ansible-playbook`, import YAML/JSON.

Le document d'un inventaire (ce que l'API lit et remplace en bloc) :
    {"slug", "name", "description", "variables": {…},
     "groups": [{"name", "variables": {…}, "children": ["autre_groupe"]}],
     "hosts":  [{"name", "variables": {…}, "enabled": true, "groups": ["groupe"]}]}
Le rendu (`render`) est un inventaire Ansible au format JSON : `{"all": {"hosts", "vars", "children"}}`, sans les machines désactivées.
"""

from __future__ import annotations

import json
import re

import yaml
from django.db import transaction
from rest_framework import serializers

from .models import Inventory, InventoryGroup, InventoryHost

MAX_HOSTS = 2000
MAX_GROUPS = 200
MAX_VARS_BYTES = 16 * 1024
MAX_IMPORT_BYTES = 1024 * 1024
HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,254}$")
GROUP_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")  # Ansible refuse les tirets dans les noms de groupe
VAR_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
RESERVED_GROUPS = {"all", "ungrouped"}
# Une variable d'inventaire ne porte jamais un identifiant (clé, mot de passe, jeton…) ni ne change la politique de clés d'hôte : cela passe par les connexions et les
# entrées de l'action. Les mots ci-dessous, repérés entre soulignés (« ansible_password », « db_pass », « api_token »), sont refusés.
SECRET_WORDS = {"password", "passwd", "pass", "passphrase", "secret", "token", "apikey", "vault"}
FORBIDDEN_VARS = {
    "ansible_ssh_private_key_file",
    "ansible_private_key_file",
    "ansible_ssh_common_args",
    "ansible_ssh_extra_args",
    "ansible_ssh_executable",
    "ansible_shell_executable",
    "ansible_sftp_extra_args",
    "ansible_scp_extra_args",
}
SECRET_VALUE = re.compile(
    r"(?i)(-----BEGIN [A-Z ]*PRIVATE KEY-----|AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{30,}|xox[baprs]-[A-Za-z0-9-]{10,})"
)


def _bad(field: str, message: str) -> serializers.ValidationError:
    return serializers.ValidationError({field: message})


def check_vars(value: object, field: str) -> dict:
    """Variables d'une machine, d'un groupe ou de l'inventaire : un objet de noms simples, borné, sans identifiant."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise _bad(field, "Les variables doivent être un objet.")
    try:
        raw = json.dumps(value)
    except (TypeError, ValueError):
        raise _bad(field, "Variables non sérialisables.") from None
    if len(raw) > MAX_VARS_BYTES:
        raise _bad(field, "Variables trop volumineuses (16 Ko au plus).")
    for key in value:
        if not isinstance(key, str) or not VAR_RE.match(key):
            raise _bad(field, f"Nom de variable invalide : « {str(key)[:40]} » (lettres, chiffres et soulignés).")
        low = key.lower()
        if low in FORBIDDEN_VARS or SECRET_WORDS & set(low.split("_")):
            raise _bad(
                field,
                f"La variable « {key} » ressemble à un identifiant ou change la vérification des clés d'hôte : "
                "les identifiants passent par les connexions de l'action, la politique de clés par ses entrées.",
            )
    if SECRET_VALUE.search(raw):
        raise _bad(field, "Une valeur semble contenir un secret : utilisez une connexion.")
    return value


def _names(items: object, field: str, pattern: re.Pattern, what: str) -> list[dict]:
    if not isinstance(items, list):
        raise _bad(field, "Une liste est attendue.")
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not pattern.match(item["name"]):
            raise _bad(
                field, f"Nom de {what} invalide : « {str(item.get('name') if isinstance(item, dict) else item)[:60]} »."
            )
        if item["name"] in seen:
            raise _bad(field, f"{what.capitalize()} en double : « {item['name']} ».")
        seen.add(item["name"])
    return items


def _acyclic(children: dict[str, list[str]]) -> None:
    state: dict[str, int] = {}

    def visit(node: str) -> None:
        if state.get(node) == 1:
            raise _bad("groups", f"Les groupes forment une boucle (autour de « {node} »).")
        if state.get(node) == 2:
            return
        state[node] = 1
        for child in children.get(node, []):
            visit(child)
        state[node] = 2

    for name in children:
        visit(name)


def clean_document(doc: object) -> dict:
    """Valide un document d'inventaire (sans son identifiant) et le renvoie normalisé ; lève une erreur DRF lisible sinon."""
    if not isinstance(doc, dict):
        raise serializers.ValidationError("Un objet est attendu.")
    groups = _names(doc.get("groups", []), "groups", GROUP_RE, "groupe")
    hosts = _names(doc.get("hosts", []), "hosts", HOST_RE, "machine")
    if len(groups) > MAX_GROUPS:
        raise _bad("groups", f"{MAX_GROUPS} groupes au plus.")
    if len(hosts) > MAX_HOSTS:
        raise _bad("hosts", f"{MAX_HOSTS} machines au plus.")
    names = {g["name"] for g in groups}
    if names & RESERVED_GROUPS:
        raise _bad("groups", "Les noms « all » et « ungrouped » sont réservés par Ansible.")
    out_groups, children = [], {}
    for g in groups:
        kids = g.get("children", [])
        if not isinstance(kids, list) or not all(isinstance(k, str) and k in names and k != g["name"] for k in kids):
            raise _bad(
                "groups", f"Groupe « {g['name']} » : ses sous-groupes doivent être d'autres groupes de l'inventaire."
            )
        children[g["name"]] = sorted(set(kids))
        out_groups.append(
            {"name": g["name"], "variables": check_vars(g.get("variables"), "groups"), "children": children[g["name"]]}
        )
    _acyclic(children)
    out_hosts = []
    for h in hosts:
        member = h.get("groups", [])
        if not isinstance(member, list) or not all(isinstance(m, str) and m in names for m in member):
            raise _bad("hosts", f"Machine « {h['name']} » : ses groupes doivent exister dans l'inventaire.")
        enabled = h.get("enabled", True)
        if not isinstance(enabled, bool):
            raise _bad("hosts", f"Machine « {h['name']} » : « enabled » doit être vrai ou faux.")
        out_hosts.append(
            {
                "name": h["name"],
                "variables": check_vars(h.get("variables"), "hosts"),
                "enabled": enabled,
                "groups": sorted(set(member)),
            }
        )
    return {"variables": check_vars(doc.get("variables"), "variables"), "groups": out_groups, "hosts": out_hosts}


@transaction.atomic
def save_document(inv: Inventory, doc: dict) -> None:
    """Remplace en bloc le contenu (machines, groupes, variables) d'un inventaire par un document déjà validé (`clean_document`)."""
    inv.groups.all().delete()
    inv.hosts.all().delete()
    inv.variables = doc["variables"]
    inv.save()
    by_name = {
        g["name"]: InventoryGroup.objects.create(inventory=inv, name=g["name"], variables=g["variables"])
        for g in doc["groups"]
    }
    for g in doc["groups"]:
        by_name[g["name"]].children.set([by_name[c] for c in g["children"]])
    for h in doc["hosts"]:
        host = InventoryHost.objects.create(
            inventory=inv, name=h["name"], variables=h["variables"], enabled=h["enabled"]
        )
        host.groups.set([by_name[n] for n in h["groups"]])


def load_document(inv: Inventory) -> dict:
    groups = list(inv.groups.prefetch_related("children"))
    hosts = list(inv.hosts.prefetch_related("groups"))
    return {
        "slug": inv.slug,
        "name": inv.name,
        "description": inv.description,
        "variables": inv.variables,
        "groups": [
            {"name": g.name, "variables": g.variables, "children": sorted(c.name for c in g.children.all())}
            for g in groups
        ],
        "hosts": [
            {
                "name": h.name,
                "variables": h.variables,
                "enabled": h.enabled,
                "groups": sorted(g.name for g in h.groups.all()),
            }
            for h in hosts
        ],
    }


def render(inv: Inventory) -> dict:
    """L'inventaire au format JSON d'Ansible. Les machines désactivées en sont absentes ; une machine sans groupe est dans `all.hosts`."""
    doc = load_document(inv)
    enabled = [h for h in doc["hosts"] if h["enabled"]]
    ungrouped = {h["name"]: h["variables"] for h in enabled if not h["groups"]}
    children = {}
    for g in doc["groups"]:
        entry: dict = {}
        members = {h["name"]: h["variables"] for h in enabled if g["name"] in h["groups"]}
        if members:
            entry["hosts"] = members
        if g["variables"]:
            entry["vars"] = g["variables"]
        if g["children"]:
            entry["children"] = {c: {} for c in g["children"]}
        children[g["name"]] = entry
    top: dict = {}
    if ungrouped:
        top["hosts"] = ungrouped
    if doc["variables"]:
        top["vars"] = doc["variables"]
    if children:
        top["children"] = children
    return {"all": top or {"hosts": {}}}


class _Collector:
    """Parcourt un inventaire Ansible imbriqué et en tire des groupes et des machines à plat."""

    def __init__(self) -> None:
        self.groups: dict[str, dict] = {}
        self.hosts: dict[str, dict] = {}

    def walk(self, name: str | None, body: object) -> None:
        body = body or {}
        if not isinstance(body, dict):
            raise serializers.ValidationError({"text": f"« {name} » : un objet est attendu."})
        if name is not None:
            self.groups.setdefault(name, {"name": name, "variables": {}, "children": []})["variables"].update(
                body.get("vars") or {}
            )
        for host, hvars in (body.get("hosts") or {}).items():
            entry = self.hosts.setdefault(
                str(host), {"name": str(host), "variables": {}, "enabled": True, "groups": []}
            )
            entry["variables"].update(hvars or {})
            if name is not None and name not in entry["groups"]:
                entry["groups"].append(name)
        for child, cbody in (body.get("children") or {}).items():
            if name is not None and str(child) not in self.groups[name]["children"]:
                self.groups[name]["children"].append(str(child))
            self.walk(str(child), cbody)


def parse_import(text: str) -> dict:
    """Lit un inventaire Ansible au format YAML ou JSON (`all:` avec groupes imbriqués, ou groupes à la racine) et le convertit en document. Les formats INI
    et les scripts d'inventaire ne sont pas pris en charge."""
    if not isinstance(text, str) or not text.strip():
        raise serializers.ValidationError({"text": "Collez un inventaire YAML ou JSON."})
    if len(text.encode()) > MAX_IMPORT_BYTES:
        raise serializers.ValidationError({"text": "Inventaire trop volumineux (1 Mo au plus)."})
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise serializers.ValidationError({"text": f"YAML/JSON illisible : {str(exc)[:150]}"}) from None
    if not isinstance(data, dict) or not data:
        raise serializers.ValidationError(
            {"text": "Un inventaire est un objet : « all: … » ou des groupes à la racine."}
        )
    # au format YAML d'Ansible, « all » est un groupe comme les autres : les groupes écrits à côté de lui sont ses enfants
    top = dict(data["all"]) if isinstance(data.get("all"), dict) else {}
    extra = {k: v for k, v in data.items() if k != "all"}
    if extra:
        top["children"] = {**(top.get("children") or {}), **extra}
    found = _Collector()
    found.walk(None, top)
    return {
        "variables": top.get("vars") or {},
        "groups": list(found.groups.values()),
        "hosts": list(found.hosts.values()),
    }
