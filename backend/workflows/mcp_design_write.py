"""MCP de CONCEPTION — outils d'ÉCRITURE (ADR 0025) : un assistant externe PRÉPARE des brouillons, une personne publie.

Ce qui est possible : créer ou modifier le BROUILLON d'un workflow ou d'un formulaire, rattacher un formulaire à un workflow, PROPOSER une
action ou un skill (état « proposée », jamais exécutable ni publiable avant relecture humaine), et vérifier un workflow (analyse
statique). Ce qui ne l'est pas, par construction : publier, promouvoir, lancer, activer un déclencheur, approuver, activer une proposition,
supprimer, toucher aux connexions et aux secrets. Il n'existe aucun outil pour cela.

Mêmes validations que l'API (mêmes sérialiseurs, mêmes contrôles de dossier et de droit), mêmes erreurs structurées pour que l'assistant se
corrige. Tout ce qui est écrit porte l'étiquette « assistant-externe » ; chaque appel est audité (`mcp.design.call`, puis
`mcp.design.write` avec ce qui a été touché) et borné par un quota horaire par personne.
Pas d'« essai à blanc » ici : il exécute les scripts dans un bac à sable, donc il appartient à un job, jamais au process web."""

import json
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.http import Http404
from django.utils import timezone
from rest_framework import serializers

from audit.models import AuditEvent, record
from core.folders import clean_folder
from runs import ssh_node
from tenancy.permissions import EDIT

from . import access
from .mcp_design_common import ASSISTANT_TAG as ASSISTANT_TAG  # noqa: PLC0414 - réexporté : lu ici par les tests
from .mcp_design_common import SLUG, _conflict, _marked, _need_edit, _ok, _spec, _touched
from .mcp_design_hints import design_hints
from .models import Workflow
from .validation import inputs_shape_issues, validate

MAX_GRAPH_BYTES = 512 * 1024


class Throttled(Exception):
    pass


def check_budget(request, ws) -> None:
    """Quota de créations par heure et par personne : l'assistant ne noie pas l'espace de brouillons."""
    limit = getattr(settings, "ORCH_MCP_DESIGN_WRITES_PER_HOUR", 60)
    since = timezone.now() - timedelta(hours=1)
    used = AuditEvent.objects.filter(
        action="mcp.design.call",
        actor=request.user,
        workspace=ws,
        created_at__gte=since,
        data__tool__in=sorted(WRITE_NAMES),
    ).count()
    if used > limit:  # l'appel en cours est déjà compté
        raise Throttled(f"Limite atteinte : {limit} écritures par heure. Réessayez plus tard.")


# --- sérialiseurs d'entrée ---------------------------------------------------------------------------------------------------
class Tags(serializers.ListField):
    def __init__(self, **kw):
        super().__init__(child=serializers.CharField(max_length=60), required=False, default=list, max_length=19, **kw)


class CreateWorkflowIn(serializers.Serializer):
    slug = serializers.SlugField(max_length=50)
    name = serializers.CharField(max_length=160)
    description = serializers.CharField(required=False, allow_blank=True, default="")
    folder = serializers.CharField(required=False, allow_blank=True, default="", max_length=200)
    tags = Tags()
    graph = serializers.JSONField(required=False)


class UpdateWorkflowIn(serializers.Serializer):
    slug = serializers.SlugField(max_length=50)
    revision = serializers.IntegerField(min_value=1)
    graph = serializers.JSONField()


class CreateFormIn(serializers.Serializer):
    slug = serializers.SlugField(max_length=50)
    name = serializers.CharField(max_length=160)
    description = serializers.CharField(required=False, allow_blank=True, max_length=2000, default="")
    folder = serializers.CharField(required=False, allow_blank=True, default="", max_length=200)
    tags = Tags()
    definition = serializers.JSONField(required=False)


class UpdateFormIn(serializers.Serializer):
    slug = serializers.SlugField(max_length=50)
    revision = serializers.IntegerField(min_value=1)
    definition = serializers.JSONField()
    name = serializers.CharField(max_length=160, required=False)
    description = serializers.CharField(required=False, allow_blank=True, max_length=2000)


class AttachFormIn(serializers.Serializer):
    workflow = serializers.SlugField(max_length=50)
    revision = serializers.IntegerField(min_value=1)
    form = serializers.SlugField(max_length=50)
    map = serializers.JSONField(required=False)


class ValidateIn(serializers.Serializer):
    slug = serializers.SlugField(max_length=50, required=False)
    graph = serializers.JSONField(required=False)


# --- workflows -------------------------------------------------------------------------------------------------------------
def _check_graph(graph) -> None:
    if not isinstance(graph, dict) or len(json.dumps(graph)) > MAX_GRAPH_BYTES:
        raise serializers.ValidationError({"graph": "Graphe trop volumineux ou invalide."})
    if bad := inputs_shape_issues(
        graph
    ):  # refusé AVANT toute écriture : aucune révision du brouillon n'est enregistrée
        raise serializers.ValidationError({"graph": [f"Étape « {i['node']} » : {i['message']}" for i in bad]})


def _ssh_refusal(message: str):
    return serializers.ValidationError({"graph": message})


ANSIBLE_REVIEWED_KEYS = (
    "playbook",
    "roles",
    "required_tags",
    "connections",
    "auth",
    "inputs",
    "timeout_seconds",
    "idempotent",
)


def _guard_ansible(current: dict | None, incoming: dict) -> dict:
    """Un playbook écrit par un assistant est du code qui agira sur des machines : toute étape `ansible` créée ou modifiée par lui est marquée `ai_review`
    (la publication reste bloquée tant qu'une personne ne l'a pas relue et confirmée dans l'éditeur de playbook). Une étape renvoyée telle qu'elle était
    garde son état (relue ou non) : l'assistant ne peut ni lever la marque ni la reposer sur ce qu'une personne a relu."""
    before = {
        n["id"]: n
        for n in (current or {}).get("nodes", [])
        if isinstance(n, dict) and n.get("type") == "ansible" and isinstance(n.get("id"), str)
    }
    nodes = []
    for n in incoming.get("nodes", []):
        if not (isinstance(n, dict) and n.get("type") == "ansible"):
            nodes.append(n)
            continue
        old = before.get(n.get("id"))
        same = old is not None and all(old.get(k) == n.get(k) for k in ANSIBLE_REVIEWED_KEYS)
        nodes.append({**n, "ai_review": bool(old.get("ai_review")) if same and old is not None else True})
    return {**incoming, "nodes": nodes}


def _guard_ssh(ws, current: dict | None, incoming: dict) -> dict:
    """What an assistant may do with SSH steps, checked on the SERVER at every write (never trusting an earlier list_connections):
    - a step it was shown and sends back unchanged (or as the redacted view it read) is kept exactly as stored;
    - a step it creates or changes may be in TEST or REAL mode (the mode it names, else the one the step had, else test: a stored real step is
      never downgraded silently). The run itself stays a person's decision: publishing and launching are never available to an assistant, and
      its dry run always simulates;
    - such a step may only name SSH connections of this workspace (target and every jump), carries no secret reference and no field the step
      does not define, and no masked placeholder (the hidden text of a sensitive command can be kept by sending the step back unchanged, not rewritten);
    - it may remove an SSH step (the draft keeps its history).
    Errors name the step and the rule, never a parameter of a connection."""
    before = ssh_node.ssh_nodes(current)
    after = ssh_node.ssh_nodes(incoming)
    kept: dict[str, dict] = {}
    for nid, n in after.items():
        old = before.get(nid)
        if old is not None and (n == old or n == ssh_node.redact_node(old)):
            kept[nid] = old  # untouched: the stored step wins over the redacted view
            continue
        if n.get("mode") not in (None, "test", "reel"):
            raise _ssh_refusal(f"Étape SSH « {nid} » : le mode est « test » ou « reel ».")
        if n.get("secrets") or "send_secret" in json.dumps(n):
            raise _ssh_refusal(
                f"Étape SSH « {nid} » : les références à des secrets ne sont pas permises dans ce parcours."
            )
        unknown = ssh_node.unknown_fields(n)
        if unknown:
            raise _ssh_refusal(f"Étape SSH « {nid} » : champ(s) inconnu(s) : {', '.join(unknown[:8])}.")
        if ssh_node.MASKED in json.dumps(n, ensure_ascii=False):
            raise _ssh_refusal(
                f"Étape SSH « {nid} » : une valeur masquée (« {ssh_node.MASKED} ») ne peut pas être réécrite."
            )
        if ssh_node.unauthorized_connections(ws, n):
            raise _ssh_refusal(
                f"Étape SSH « {nid} » : une connexion (cible ou rebond) n'existe pas, n'est pas de type SSH ou n'est pas disponible dans cet espace. "
                "Prenez des noms de connexions de type « ssh » dans list_connections."
            )
        kept[nid] = {**n, "mode": n.get("mode") or (old or {}).get("mode") or "test"}
    if not kept:
        return incoming
    return {
        **incoming,
        "nodes": [
            kept.get(x["id"], x) if isinstance(x, dict) and x.get("id") in kept else x
            for x in incoming.get("nodes", [])
        ],
    }


def create_workflow(request, ws, args):
    _need_edit(request, ws)
    ser = CreateWorkflowIn(data=args)
    ser.is_valid(raise_exception=True)
    d = ser.validated_data
    folder = clean_folder(d["folder"])
    access.require(request.user, ws, folder, EDIT)
    graph = d.get("graph")
    if graph is not None:
        _check_graph(graph)
        graph = _guard_ansible(None, _guard_ssh(ws, None, graph))
    if Workflow.objects.filter(workspace=ws, slug=d["slug"]).exists():
        raise serializers.ValidationError({"slug": "Cet identifiant existe déjà dans l'espace."})
    try:
        with transaction.atomic():
            wf = Workflow.objects.create(
                workspace=ws,
                owner=request.user,
                slug=d["slug"],
                name=d["name"],
                folder=folder,
                description=d["description"],
                tags=_marked(d["tags"]),
                **({"draft_graph": graph, "draft_revision": 2} if graph is not None else {}),
            )
    except IntegrityError:
        raise serializers.ValidationError({"slug": "Cet identifiant existe déjà dans l'espace."}) from None
    record("workflow.create", actor=request.user, workspace=ws, target=wf)
    _touched(request, ws, "create_workflow", wf, slug=wf.slug)
    return _ok(
        {
            "slug": wf.slug,
            "revision": wf.draft_revision,
            "issues": validate(wf.draft_graph, ws, wf.slug),
            "hints": design_hints(wf.draft_graph),
            "status": "brouillon",
        }
    )


def update_workflow(request, ws, args):
    _need_edit(request, ws)
    ser = UpdateWorkflowIn(data=args)
    ser.is_valid(raise_exception=True)
    d = ser.validated_data
    _check_graph(d["graph"])
    with transaction.atomic():
        wf = Workflow.objects.select_for_update().filter(workspace=ws, slug=d["slug"]).first()
        if wf is None:
            raise Http404()
        access.require(request.user, ws, wf.folder, EDIT)
        if wf.draft_revision != d["revision"]:
            raise _conflict(wf.draft_revision)
        wf.draft_graph = _guard_ansible(wf.draft_graph, _guard_ssh(ws, wf.draft_graph, d["graph"]))
        wf.draft_revision += 1
        wf.tags = _marked(wf.tags)
        wf.save()
    _touched(request, ws, "update_workflow", wf, slug=wf.slug, revision=wf.draft_revision)
    return _ok(
        {
            "slug": wf.slug,
            "revision": wf.draft_revision,
            "issues": validate(wf.draft_graph, ws, wf.slug),
            "hints": design_hints(wf.draft_graph),
            "status": "brouillon",
        }
    )


def validate_workflow(request, ws, args):
    ser = ValidateIn(data=args)
    ser.is_valid(raise_exception=True)
    d = ser.validated_data
    if "graph" in d:
        _check_graph(d["graph"])
        return _ok({"issues": validate(d["graph"], ws, d.get("slug")), "hints": design_hints(d["graph"])})
    if "slug" not in d:
        raise serializers.ValidationError({"slug": "Indiquez le workflow (slug) ou un graphe."})
    wf = access.workflow_or_404(request.user, ws, d["slug"])
    return _ok({"issues": validate(wf.draft_graph, ws, wf.slug), "hints": design_hints(wf.draft_graph)})


# --- formulaires -----------------------------------------------------------------------------------------------------------
def create_form(request, ws, args):
    from formbuilder.models import Form, empty_definition
    from formbuilder.views import _check_definition

    _need_edit(request, ws)
    ser = CreateFormIn(data=args)
    ser.is_valid(raise_exception=True)
    d = ser.validated_data
    folder = clean_folder(d["folder"])
    access.require(request.user, ws, folder, EDIT)
    definition = _check_definition(d["definition"]) if "definition" in d else empty_definition()
    try:
        with transaction.atomic():
            form = Form.objects.create(
                workspace=ws,
                slug=d["slug"],
                name=d["name"],
                description=d["description"],
                folder=folder,
                tags=_marked(d["tags"]),
                owner=request.user,
                draft_definition=definition,
            )
    except IntegrityError:
        raise serializers.ValidationError({"slug": "Cet identifiant existe déjà."}) from None
    record("form.create", actor=request.user, workspace=ws, target=form, template=False)
    _touched(request, ws, "create_form", form, slug=form.slug)
    return _ok({"slug": form.slug, "revision": form.draft_revision, "status": "brouillon (non publié)"})


def update_form(request, ws, args):
    from formbuilder.models import Form
    from formbuilder.views import _check_definition

    _need_edit(request, ws)
    ser = UpdateFormIn(data=args)
    ser.is_valid(raise_exception=True)
    d = ser.validated_data
    with transaction.atomic():
        form = Form.objects.select_for_update().filter(workspace=ws, slug=d["slug"]).first()
        if form is None:
            raise Http404()
        access.require(request.user, ws, form.folder, EDIT)
        if form.is_template:
            raise serializers.ValidationError({"slug": "Un modèle ne se modifie pas ici : copiez-le en formulaire."})
        if d["revision"] != form.draft_revision:
            raise _conflict(form.draft_revision)
        form.draft_definition = _check_definition(d["definition"])
        form.draft_revision += 1
        for key in ("name", "description"):
            if key in d:
                setattr(form, key, d[key])
        form.tags = _marked(form.tags)
        form.save()
    record("form.update", actor=request.user, workspace=ws, target=form, fields=sorted(d))
    _touched(request, ws, "update_form", form, slug=form.slug, revision=form.draft_revision)
    return _ok({"slug": form.slug, "revision": form.draft_revision, "status": "brouillon (non publié)"})


def attach_form(request, ws, args):
    """Rattache un formulaire au brouillon d'un workflow. Toujours une référence à la bibliothèque : à sa dernière version publiée, ou, si le formulaire n'est pas encore publié
    (par ex. créé par l'assistant), au formulaire lui-même (avertissement jusqu'à sa publication par une personne)."""
    from formbuilder.models import Form
    from triggers import formengine

    _need_edit(request, ws)
    ser = AttachFormIn(data=args)
    ser.is_valid(raise_exception=True)
    d = ser.validated_data
    mapping = d.get("map")
    if mapping is not None and not formengine._valid_map(mapping):
        raise serializers.ValidationError(
            {"map": "Correspondance invalide : {entrée du workflow : clé d'un champ du formulaire}."}
        )
    form = access.visible(
        Form.objects.filter(workspace=ws, slug=d["form"]).prefetch_related("versions"), request.user, ws
    ).first()
    if form is None:
        raise Http404()
    if form.is_template:
        raise serializers.ValidationError({"form": "C'est un modèle : copiez-le en formulaire avant de le rattacher."})
    with transaction.atomic():
        wf = Workflow.objects.select_for_update().filter(workspace=ws, slug=d["workflow"]).first()
        if wf is None:
            raise Http404()
        access.require(request.user, ws, wf.folder, EDIT)
        if wf.draft_revision != d["revision"]:
            raise _conflict(wf.draft_revision)
        latest = form.latest_version()
        # toujours une RÉFÉRENCE à la bibliothèque (jamais une copie « ancien format ») : sans version publiée, elle suit la dernière version à venir
        input_form = {"ref": {"form": form.slug, "version": latest.version if latest else None}}
        mode = (
            f"référence à la version {latest.version}"
            if latest
            else "référence au formulaire (non publié : une personne doit le publier avant de publier le workflow)"
        )
        if mapping:
            input_form["map"] = mapping
        wf.draft_graph = {**wf.draft_graph, "input_form": input_form}
        wf.draft_revision += 1
        wf.tags = _marked(wf.tags)
        wf.save()
    _touched(request, ws, "attach_form", wf, slug=wf.slug, form=form.slug, revision=wf.draft_revision)
    return _ok(
        {
            "slug": wf.slug,
            "revision": wf.draft_revision,
            "attached": mode,
            "issues": validate(wf.draft_graph, ws, wf.slug),
            "hints": design_hints(wf.draft_graph),
        }
    )


# --- propositions d'actions et de skills ------------------------------------------------------------------------------------
def propose_action(request, ws, args):
    from catalog.library import ActionCreateSerializer, _create_version, require_contract
    from catalog.models import Action

    _need_edit(request, ws)
    ser = ActionCreateSerializer(
        data={**args, "ai_generated": True, "reviewed": True}
    )  # la relecture est celle de l'ACTIVATION, par une personne
    ser.is_valid(raise_exception=True)
    d = ser.validated_data
    require_contract(d["description"], d["version"])
    with transaction.atomic():
        action = Action.objects.select_for_update().filter(workspace=ws, slug=d["slug"]).first()
        if action is None:
            action = Action.objects.create(
                workspace=ws,
                owner=request.user,
                slug=d["slug"],
                name=d["name"],
                description=d["description"],
                domain=d["domain"],
                folder=clean_folder(d["folder"]),
                tags=_marked(d["tags"])[:10],
                status=Action.Status.PROPOSED,
            )
            created = True
        elif action.status == Action.Status.PROPOSED:  # une proposition encore à relire se révise : nouvelle version
            created = False
        else:
            raise serializers.ValidationError(
                {"slug": "Cette action existe déjà et n'est pas une proposition : choisissez un autre identifiant."}
            )
        v = _create_version(action, {**d["version"], "version": d["version"].get("version")}, request.user)
    record("action.propose", actor=request.user, workspace=ws, target=action, version=v.version, created=created)
    _touched(request, ws, "propose_action", action, slug=action.slug, version=v.version)
    from .mcp_design_hints import NETWORK_MESSAGE, uses_network

    out = {
        "slug": action.slug,
        "version": v.version,
        "status": "proposed",
        "note": "À relire et activer par une personne avant tout usage.",
    }
    if uses_network(str(d["version"].get("source", ""))):
        out["hints"] = [{"code": "network_in_action", "message": NETWORK_MESSAGE}]
    return _ok(out)


def propose_skill(request, ws, args):
    from ai.models import AiSkill
    from ai.skills import SkillCreateSerializer, _create_version

    _need_edit(request, ws)
    ser = SkillCreateSerializer(data={**args, "ai_generated": True, "reviewed": True})
    ser.is_valid(raise_exception=True)
    d = ser.validated_data
    with transaction.atomic():
        skill = AiSkill.objects.select_for_update().filter(workspace=ws, slug=d["slug"]).first()
        if skill is None:
            skill = AiSkill.objects.create(
                workspace=ws,
                owner=request.user,
                slug=d["slug"],
                name=d["name"],
                description=d["description"],
                folder=clean_folder(d["folder"]),
                tags=_marked(d["tags"]),
                status=AiSkill.Status.PROPOSED,
            )
            created = True
        elif skill.status == AiSkill.Status.PROPOSED:
            created = False
        else:
            raise serializers.ValidationError(
                {"slug": "Ce skill existe déjà et n'est pas une proposition : choisissez un autre identifiant."}
            )
        v = _create_version(skill, d["version"], request.user)
    record("ai_skill.propose", actor=request.user, workspace=ws, target=skill, version=v.version, created=created)
    _touched(request, ws, "propose_skill", skill, slug=skill.slug, version=v.version)
    return _ok(
        {
            "slug": skill.slug,
            "version": v.version,
            "status": "proposed",
            "note": "À relire et activer par une personne avant tout usage.",
        }
    )


# --- catalogue des outils ---------------------------------------------------------------------------------------------------
def _w(name, title, description, props, required):
    spec = _spec(name, title, description, props, required)
    spec["annotations"] = {
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    }
    return spec


REV = {
    "type": "integer",
    "minimum": 1,
    "description": "Révision du brouillon lue juste avant (get_*) : protège contre l'écrasement d'un travail plus récent.",
}
OBJ = {"type": "object"}
TXT = {"type": "string"}
TAGS = {"type": "array", "items": TXT, "description": "Étiquettes (la marque « assistant-externe » est ajoutée)."}

WRITE_TOOLS = {
    "create_workflow": (
        _w(
            "create_workflow",
            "Créer un workflow (brouillon)",
            "Format exact : lisez d'abord get_authoring_guide (topic=workflow). Crée le BROUILLON d'un workflow, éventuellement avec son graphe. Renvoie les problèmes à corriger (issues). Jamais publié ni lancé : une personne le fait.",
            {"slug": SLUG, "name": TXT, "description": TXT, "folder": TXT, "tags": TAGS, "graph": OBJ},
            ["slug", "name"],
        ),
        create_workflow,
    ),
    "update_workflow": (
        _w(
            "update_workflow",
            "Modifier le brouillon d'un workflow",
            "Format exact : lisez d'abord get_authoring_guide (topic=workflow). Remplace le graphe du BROUILLON (révision exigée). Renvoie les problèmes restants.",
            {"slug": SLUG, "revision": REV, "graph": OBJ},
            ["slug", "revision", "graph"],
        ),
        update_workflow,
    ),
    "validate_workflow": (
        _w(
            "validate_workflow",
            "Vérifier un workflow",
            "Analyse statique du brouillon d'un workflow (ou d'un graphe fourni) : renvoie les problèmes, sans rien écrire ni exécuter.",
            {"slug": SLUG, "graph": OBJ},
            None,
        )
        | {
            "annotations": {
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": False,
            }
        },
        validate_workflow,
    ),
    "create_form": (
        _w(
            "create_form",
            "Créer un formulaire (brouillon)",
            "Format exact : lisez d'abord get_authoring_guide (topic=form). Crée le BROUILLON d'un formulaire de la bibliothèque. Non publié.",
            {"slug": SLUG, "name": TXT, "description": TXT, "folder": TXT, "tags": TAGS, "definition": OBJ},
            ["slug", "name"],
        ),
        create_form,
    ),
    "update_form": (
        _w(
            "update_form",
            "Modifier le brouillon d'un formulaire",
            "Format exact : lisez d'abord get_authoring_guide (topic=form). Remplace la définition du BROUILLON (révision exigée).",
            {"slug": SLUG, "revision": REV, "definition": OBJ, "name": TXT, "description": TXT},
            ["slug", "revision", "definition"],
        ),
        update_form,
    ),
    "attach_form": (
        _w(
            "attach_form",
            "Rattacher un formulaire à un workflow",
            "Fait du formulaire le formulaire d'entrée du BROUILLON d'un workflow (référence s'il est publié, copie sinon). `map` : {entrée du workflow : clé du champ}.",
            {"workflow": SLUG, "revision": REV, "form": SLUG, "map": OBJ},
            ["workflow", "revision", "form"],
        ),
        attach_form,
    ),
    "propose_action": (
        _w(
            "propose_action",
            "Proposer une action",
            "Format exact : lisez d'abord get_authoring_guide (topic=action). Crée (ou révise) une PROPOSITION d'action : code et contrat complets. Elle ne s'exécute pas et ne se publie pas tant qu'une personne ne l'a pas relue et activée. Vérifiez d'abord search_actions.",
            {
                "slug": SLUG,
                "name": TXT,
                "description": TXT,
                "domain": TXT,
                "folder": TXT,
                "tags": TAGS,
                "version": {
                    "type": "object",
                    "description": "runtime, source, input_schema, output_schema, side_effects, network_policy, secrets_required…",
                },
            },
            ["slug", "name", "description", "version"],
        ),
        propose_action,
    ),
    "propose_skill": (
        _w(
            "propose_skill",
            "Proposer un skill IA",
            "Format exact : lisez d'abord get_authoring_guide (topic=skill). Crée (ou révise) une PROPOSITION de skill : instructions, entrées, réponse attendue. Inutilisable tant qu'une personne ne l'a pas relue et activée.",
            {
                "slug": SLUG,
                "name": TXT,
                "description": TXT,
                "folder": TXT,
                "tags": TAGS,
                "version": {
                    "type": "object",
                    "description": "instructions, input_schema, output_schema, default_provider",
                },
            },
            ["slug", "name", "version"],
        ),
        propose_skill,
    ),
}


def _dry(request, ws, args):
    from .mcp_dryrun import start_dry_run

    return start_dry_run(request, ws, args)


def _dry_get(request, ws, args):
    from .mcp_dryrun import get_dry_run

    return get_dry_run(request, ws, args)


SAMPLE = {
    "type": "object",
    "description": "Jeu d'essai : {label, inputs (valeurs réalistes respectant le schéma d'entrées), expected?: [{step, field, value}] (ce que vous attendez de la SORTIE d'une étape), mocks?: {étape: {champ: valeur}} (sorties imposées à une étape SIMULÉE : action, IA)}.",
}
WRITE_TOOLS["start_dry_run"] = (
    _w(
        "start_dry_run",
        "Essayer un workflow (essai à blanc)",
        "ESSAYEZ avant de rendre la main : exécute les scripts et les décisions du BROUILLON dans un bac à sable isolé (ni réseau, ni secret) ; actions, IA, sous-workflows et approbations sont simulés d'après leur contrat. "
        "Fournissez 1 à 3 jeux d'essai réalistes (un par cas métier important, avec les mêmes codes que le formulaire), sinon un cas typique et un cas limite sont générés. Asynchrone : renvoie un job_id, puis appelez get_dry_run.",
        {"slug": SLUG, "samples": {"type": "array", "items": SAMPLE, "maxItems": 3}},
        ["slug"],
    ),
    _dry,
)


def _action_test(request, ws, args):
    from .mcp_dryrun import start_action_test

    return start_action_test(request, ws, args)


WRITE_TOOLS["start_action_test"] = (
    _w(
        "start_action_test",
        "Tester le code d'une action",
        "TESTEZ le code de chaque action que vous proposez avant de rendre la main : il s'exécute dans le bac à sable isolé (réseau coupé, aucun secret ni connexion, limites plafonnées) sur 1 à 5 cas {label, inputs, expected?}. "
        "La sortie est contrôlée contre le contrat. L'action reste « proposée » : ce test ne l'active pas. Asynchrone : renvoie un job_id, puis appelez get_dry_run.",
        {
            "slug": SLUG,
            "version": {"type": "string"},
            "cases": {
                "type": "array",
                "maxItems": 5,
                "items": {
                    "type": "object",
                    "description": "Cas : {label, inputs (selon input_schema), expected?: {champ: valeur attendue}}.",
                },
            },
        },
        ["slug", "cases"],
    ),
    _action_test,
)
WRITE_TOOLS["get_dry_run"] = (
    _w(
        "get_dry_run",
        "Lire le résultat d'un essai",
        "Avancement puis résultat d'un essai lancé par start_dry_run ou start_action_test : problèmes, couverture, étapes exécutées avec leurs sorties. Rappelez jusqu'à status = succeeded.",
        {"job_id": {"type": "string"}},
        ["job_id"],
    )
    | {"annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}},
    _dry_get,
)
# `validate_workflow` et `get_dry_run` ne modifient rien : ils n'entrent pas dans le quota d'écritures
from . import mcp_design_controls as _controls  # noqa: E402 - après les définitions dont il se sert

WRITE_TOOLS.update(_controls.write_tools(_w, OBJ, TXT, TAGS, REV))
from . import mcp_design_organize as _organize  # noqa: E402

WRITE_TOOLS.update(_organize.write_tools(_w, TXT, TAGS))
from . import mcp_design_runs as _runs  # noqa: E402

WRITE_TOOLS.update(_runs.write_tools())
WRITE_NAMES = set(WRITE_TOOLS) - {"validate_workflow", "get_dry_run", "validate_control", "validate_form"}
