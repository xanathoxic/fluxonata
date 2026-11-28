"""« Débridage total » du MCP de conception (ADR 0025, amendement du 2026-11-28).

Tant qu'un administrateur n'a pas activé le débridage de l'espace, ces outils N'EXISTENT PAS pour l'assistant : ils ne figurent pas dans `tools/list` et
l'appeler répond « outil inconnu ». Activés, ils permettent à l'assistant de mener seul conception, publication et essais en dev et recette : publier un
workflow, un formulaire, un contrôle ou une version d'action, activer une action ou un skill proposés.

Chaque outil fait EXACTEMENT ce que ferait la personne propriétaire du jeton depuis l'interface : la vue de l'application est appelée en son nom (rôle,
dossiers privés, validations et audit de l'interface), jamais avec les portées du jeton. Ce qui ne change pas : jamais la production (aucun outil de
promotion), aucune décision humaine d'approbation, ni suppression, connexion, secret ou utilisateur ; le jeton garde ses portées « voir » et « modifier »."""

from django.contrib.auth import get_user_model
from django.db import transaction
from django.http import Http404
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.test import APIRequestFactory, force_authenticate

from audit.models import record
from tenancy.models import Workspace
from tenancy.permissions import PUBLISH
from workflows import access

from .mcp_design_common import SLUG, _ok, _spec, _touched
from .models import Workflow

BUMP = {"type": "string", "enum": ["patch", "minor", "major"], "description": "Quel numéro augmenter (défaut : minor)."}
CHANGELOG = {"type": "string", "maxLength": 300, "description": "Ce qui change, en une phrase."}
NOT_ENABLED = (
    "Le débridage total n'est pas activé dans cet espace : un administrateur peut l'activer dans Approbations › "
    "Lancements demandés par un assistant."
)


def enabled(ws) -> bool:
    """L'état lu maintenant (jamais celui du début de la requête) : couper le débridage coupe aussitôt les outils."""
    row = Workspace.objects.filter(pk=ws.pk).values("assistant_auto_run", "assistant_unrestricted").first()
    return bool(row and row["assistant_auto_run"] and row["assistant_unrestricted"])


def _owner(request):
    """La personne propriétaire du jeton, SANS le jeton : ses droits sont ceux de son rôle dans l'espace."""
    return get_user_model().objects.get(pk=request.user.pk)


def _call(view, owner, ws, data=None, **kwargs):
    """Fait, au nom de `owner`, ce que fait l'interface : appelle la vue (mêmes droits, mêmes validations, même audit)."""
    req = APIRequestFactory().post("/", data or {}, format="json")
    force_authenticate(req, user=owner)
    resp = view.as_view()(req, workspace=ws.slug, **kwargs)
    if resp.status_code == 404:
        raise Http404()
    if resp.status_code == 403:
        raise PermissionDenied(resp.data.get("detail", "Refusé."))
    if resp.status_code >= 400:
        raise serializers.ValidationError(resp.data)
    return resp.data


def _clear_ai_review(owner, ws, slug) -> list[str]:
    """Les étapes écrites par un assistant sont marquées « à relire » : avec le débridage, c'est lui qui relit. La levée est tracée à l'audit."""
    with transaction.atomic():
        wf = Workflow.objects.select_for_update().filter(workspace=ws, slug=slug).first()
        if wf is None:
            raise Http404()
        access.require(owner, ws, wf.folder, PUBLISH)
        graph = wf.draft_graph or {}
        nodes = (graph.get("nodes") if isinstance(graph, dict) else None) or []
        flagged = [n.get("id") for n in nodes if isinstance(n, dict) and n.get("ai_review")]
        if flagged:
            wf.draft_graph = {**graph, "nodes": [{**n, "ai_review": False} if n.get("ai_review") else n for n in nodes]}
            wf.draft_revision += 1
            wf.save(update_fields=["draft_graph", "draft_revision"])
            record(
                "workflow.ai_review_cleared",
                actor=owner,
                workspace=ws,
                target=wf,
                steps=[str(i) for i in flagged],
                automatic=True,
                unrestricted=True,
            )
    return [str(i) for i in flagged]


def _done(request, ws, tool, target, **data):
    _touched(request, ws, tool, target, unrestricted=True, **data)


def publish_workflow(request, ws, args):
    from .views import PublishView

    slug = str(args.get("slug", ""))
    owner = _owner(request)
    cleared = _clear_ai_review(owner, ws, slug)
    out = _call(
        PublishView,
        owner,
        ws,
        {"bump": args.get("bump") or "minor", "changelog": args.get("changelog") or ""},
        slug=slug,
    )
    _done(request, ws, "publish_workflow", Workflow.objects.get(workspace=ws, slug=slug), version=out.get("version"))
    return _ok(
        {
            "slug": slug,
            "version": out.get("version"),
            "status": "publié",
            **({"steps_marked_reviewed": cleared} if cleared else {}),
        }
    )


def publish_form(request, ws, args):
    from formbuilder.models import Form
    from formbuilder.views import FormPublishView

    slug = str(args.get("slug", ""))
    out = _call(
        FormPublishView,
        _owner(request),
        ws,
        {"bump": args.get("bump") or "minor", "changelog": args.get("changelog") or ""},
        slug=slug,
    )
    _done(request, ws, "publish_form", Form.objects.get(workspace=ws, slug=slug), version=out.get("version"))
    return _ok({"slug": slug, "version": out.get("version"), "status": "publié"})


def publish_control(request, ws, args):
    from formbuilder.control_views import ControlPublishView
    from formbuilder.models import Control

    slug = str(args.get("slug", ""))
    out = _call(
        ControlPublishView,
        _owner(request),
        ws,
        {"bump": args.get("bump") or "minor", "changelog": args.get("changelog") or ""},
        slug=slug,
    )
    _done(request, ws, "publish_control", Control.objects.get(workspace=ws, slug=slug), version=out.get("version"))
    return _ok({"slug": slug, "version": out.get("version"), "status": "publié"})


def activate_action(request, ws, args):
    from catalog.library import ActionActivateView
    from catalog.models import Action

    slug = str(args.get("slug", ""))
    _call(ActionActivateView, _owner(request), ws, {}, slug=slug)
    _done(request, ws, "activate_action", Action.objects.get(workspace=ws, slug=slug))
    return _ok({"slug": slug, "status": "active", "note": "Utilisable par les workflows."})


def new_action_version(request, ws, args):
    from catalog.library import ActionVersionsView
    from catalog.models import Action

    slug = str(args.get("slug", ""))
    version = args.get("version")
    if not isinstance(version, dict):
        raise serializers.ValidationError({"version": "Un objet est attendu (runtime, source, schémas, version…)."})
    out = _call(ActionVersionsView, _owner(request), ws, version, slug=slug)
    _done(request, ws, "new_action_version", Action.objects.get(workspace=ws, slug=slug), version=out.get("version"))
    return _ok(
        {"slug": slug, "version": out.get("version"), "status": "publiée", "note": "Utilisable dès sa création."}
    )


def activate_skill(request, ws, args):
    from ai.models import AiSkill
    from ai.skills import SkillActivateView

    slug = str(args.get("slug", ""))
    _call(SkillActivateView, _owner(request), ws, {}, slug=slug)
    _done(request, ws, "activate_skill", AiSkill.objects.get(workspace=ws, slug=slug))
    return _ok({"slug": slug, "status": "active"})


def _w(name, title, description, props, required):
    spec = _spec(name, title, "[Débridage total] " + description, props, required)
    spec["annotations"] = {
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    }
    return spec


def tools() -> dict:
    pub = {"slug": SLUG, "bump": BUMP, "changelog": CHANGELOG}
    return {
        "publish_workflow": (
            _w(
                "publish_workflow",
                "Publier un workflow",
                "Publie le brouillon d'un workflow en nouvelle version (validée comme depuis l'application). Les étapes que vous avez écrites et marquées « à relire » sont "
                "considérées relues. Une action ou un skill encore proposé doit d'abord être activé (activate_action, activate_skill).",
                pub,
                ["slug"],
            ),
            publish_workflow,
        ),
        "publish_form": (
            _w(
                "publish_form",
                "Publier un formulaire",
                "Publie le brouillon d'un formulaire en nouvelle version.",
                pub,
                ["slug"],
            ),
            publish_form,
        ),
        "publish_control": (
            _w(
                "publish_control",
                "Publier un contrôle",
                "Publie le brouillon d'un contrôle personnalisé en nouvelle version.",
                pub,
                ["slug"],
            ),
            publish_control,
        ),
        "activate_action": (
            _w(
                "activate_action",
                "Activer une action proposée",
                "Une action que vous avez proposée devient utilisable par les workflows.",
                {"slug": SLUG},
                ["slug"],
            ),
            activate_action,
        ),
        "new_action_version": (
            _w(
                "new_action_version",
                "Publier une nouvelle version d'une action",
                "Crée une nouvelle version d'une action EXISTANTE, même active (utilisable dès sa création). Même objet `version` que propose_action.",
                {
                    "slug": SLUG,
                    "version": {
                        "type": "object",
                        "description": "version, runtime, source, input_schema, output_schema, side_effects, network_policy…",
                    },
                },
                ["slug", "version"],
            ),
            new_action_version,
        ),
        "activate_skill": (
            _w(
                "activate_skill",
                "Activer un skill proposé",
                "Un skill IA que vous avez proposé devient utilisable.",
                {"slug": SLUG},
                ["slug"],
            ),
            activate_skill,
        ),
    }


NAMES = frozenset(tools())
