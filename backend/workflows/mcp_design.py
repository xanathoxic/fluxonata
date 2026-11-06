"""MCP de CONCEPTION (ADR 0025) : un assistant externe LIT ce que l'espace contient, pour construire dedans.

Même protocole que `mcp.py` (JSON-RPC sur HTTP, jeton Bearer), adresse distincte : `/api/v1/workspaces/<espace>/mcp/design/`.
Cette première série d'outils est en LECTURE SEULE : chercher des actions, skills, formulaires, workflows et connexions, pour que l'assistant
vérifie ce qui existe avant d'en proposer. Aucun outil ne lance, ne publie ni ne modifie.

Règles tenues ici, jamais déléguées au client :
- les droits sont ceux de la personne (rôle, portées du jeton, dossiers privés) : mêmes contrôles que l'API ;
- jamais de secret : une connexion ne sort que par son nom, son libellé et son type (ni paramètres, ni identifiants) ;
- chaque appel est audité avec le nom du client annoncé, pour distinguer un assistant externe de l'interface."""

import json

from django.http import Http404
from rest_framework.exceptions import APIException, PermissionDenied

from audit.models import record
from tenancy.permissions import has_perm

from . import access
from .mcp import McpServerView
from .models import Workflow

DESIGN_SCOPES = {"view", "edit"}  # jamais « run » ni « publish » (ADR 0025)
DEFAULT_LIMIT, MAX_LIMIT = 25, 50
MAX_SOURCE = 20_000  # caractères de code renvoyés pour une action
SLUG = {"type": "string", "description": "Identifiant (slug) de la ressource."}


def _ok(data) -> dict:
    return {
        "content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}],
        "structuredContent": data,
        "isError": False,
    }


def _err(message: str) -> dict:
    return {"isError": True, "content": [{"type": "text", "text": message}]}


def _limit(args) -> int:
    try:
        return max(1, min(MAX_LIMIT, int(args.get("limit", DEFAULT_LIMIT))))
    except (TypeError, ValueError):
        return DEFAULT_LIMIT


def _text(args, key="query") -> str:
    v = args.get(key, "")
    return v.strip().lower()[:100] if isinstance(v, str) else ""


def _match(q: str, *parts) -> bool:
    return not q or q in " ".join(str(p or "") for p in parts).lower()


def _clip(text: str, limit: int) -> tuple[str, bool]:
    return (text, False) if len(text) <= limit else (text[:limit], True)


# --- actions ---------------------------------------------------------------------------------------------------------------
def _action_row(a, with_schemas=True, usage=None) -> dict:
    v = a.latest_version()
    row = {
        "slug": a.slug,
        "name": a.name,
        "description": a.description,
        "domain": a.domain,
        "folder": a.folder,
        "tags": a.tags,
        "status": a.status,  # « proposed » : une proposition d'assistant, pas encore utilisable
        "version": v.version if v else None,
    }
    if usage is not None:
        # l'action est-elle réellement réutilisée ? (brouillons et dernières versions publiées des workflows visibles)
        row["used_by"] = usage.get(a.slug, {"workflows": 0, "steps": 0})
    if v and with_schemas:
        row |= {
            "runtime": v.runtime,
            "side_effects": v.side_effects,
            "idempotent": v.idempotent,
            "input_schema": v.input_schema,
            "output_schema": v.output_schema,
        }
    return row


def _visible_actions(ws, user):
    from catalog.models import Action

    qs = Action.objects.filter(workspace=ws).exclude(slug__startswith="__").prefetch_related("versions")
    if not has_perm(user, ws, "edit"):  # comme l'API : les propositions ne sont vues que par qui peut modifier
        qs = qs.exclude(status="proposed")
    return qs


def search_actions(request, ws, args):
    q, domain = _text(args), _text(args, "domain")
    usage = _usage(request, ws)
    rows = [
        _action_row(a, usage=usage)
        for a in _visible_actions(ws, request.user)
        if _match(q, a.name, a.slug, a.description, a.domain, " ".join(a.tags or []))
        and (not domain or a.domain.lower() == domain)
    ]
    return _ok({"count": len(rows), "results": rows[: _limit(args)]})


def get_action(request, ws, args):
    a = _visible_actions(ws, request.user).filter(slug=str(args.get("slug", ""))).first()
    if a is None:
        raise Http404()
    v = a.latest_version()
    out = _action_row(a, usage=_usage(request, ws))
    if v:
        source, cut = _clip(v.source, MAX_SOURCE)
        out |= {
            "entrypoint": v.entrypoint,
            "source": source,
            "source_truncated": cut,
            "network_policy": v.network_policy,
            "limits": v.limits,
            "examples": v.examples,
            "connection_slots": [
                s.get("slot") for s in (v.secrets_required or []) if isinstance(s, dict)
            ],  # noms des emplacements, jamais de valeur
        }
    return _ok(out)


def _usage(request, ws) -> dict:
    from .mcp_design_hints import action_usage

    return action_usage(_workflows(request, ws))


# --- skills IA -------------------------------------------------------------------------------------------------------------
def _visible_skills(ws, user):
    from ai.models import AiSkill

    qs = AiSkill.objects.filter(workspace=ws).prefetch_related("versions")
    if not has_perm(user, ws, "edit"):
        qs = qs.exclude(status="proposed")
    return qs


def _skill_row(s) -> dict:
    v = s.latest_version()
    return {
        "slug": s.slug,
        "name": s.name,
        "description": s.description,
        "folder": s.folder,
        "tags": s.tags,
        "status": s.status,
        "version": v.version if v else None,
        "input_schema": v.input_schema if v else None,
        "output_schema": v.output_schema if v else None,
    }


def list_skills(request, ws, args):
    q = _text(args)
    rows = [
        _skill_row(s)
        for s in _visible_skills(ws, request.user)
        if _match(q, s.name, s.slug, s.description, " ".join(s.tags or []))
    ]
    return _ok({"count": len(rows), "results": rows[: _limit(args)]})


def get_skill(request, ws, args):
    s = _visible_skills(ws, request.user).filter(slug=str(args.get("slug", ""))).first()
    if s is None:
        raise Http404()
    out, v = _skill_row(s), s.latest_version()
    if v:
        out |= {"instructions": v.instructions, "default_provider": v.default_provider}
    return _ok(out)


# --- formulaires (dossiers privés respectés) -------------------------------------------------------------------------------
def _forms(request, ws):
    from formbuilder.models import Form

    return access.visible(Form.objects.filter(workspace=ws).prefetch_related("versions"), request.user, ws)


def _form_row(f) -> dict:
    v = f.latest_version()
    return {
        "slug": f.slug,
        "name": f.name,
        "description": f.description,
        "folder": f.folder,
        "tags": f.tags,
        "is_template": f.is_template,
        "version": v.version if v else None,
        "fields": sum(len(s.get("fields", [])) for s in (f.draft_definition or {}).get("sections", [])),
    }


def list_forms(request, ws, args):
    q = _text(args)
    rows = [
        _form_row(f) for f in _forms(request, ws) if _match(q, f.name, f.slug, f.description, " ".join(f.tags or []))
    ]
    return _ok({"count": len(rows), "results": rows[: _limit(args)]})


def get_form(request, ws, args):
    f = _forms(request, ws).filter(slug=str(args.get("slug", ""))).first()
    if f is None:
        raise Http404()
    return _ok(_form_row(f) | {"draft_revision": f.draft_revision, "definition": f.draft_definition})


# --- workflows (dossiers privés respectés) ---------------------------------------------------------------------------------
def _workflows(request, ws):
    return access.visible(
        Workflow.objects.filter(workspace=ws, deprecated=False).prefetch_related("versions"), request.user, ws
    )


def _workflow_row(w) -> dict:
    v = w.latest_version()
    return {
        "slug": w.slug,
        "name": w.name,
        "description": w.description,
        "folder": w.folder,
        "tags": w.tags,
        "version": v.version if v else None,  # None : jamais publié
        "in_catalog": w.in_catalog,
        "mcp_exposed": w.mcp_exposed,
    }


def list_workflows(request, ws, args):
    q = _text(args)
    rows = [
        _workflow_row(w)
        for w in _workflows(request, ws)
        if _match(q, w.name, w.slug, w.description, " ".join(w.tags or []))
    ]
    return _ok({"count": len(rows), "results": rows[: _limit(args)]})


def get_workflow(request, ws, args):
    w = _workflows(request, ws).filter(slug=str(args.get("slug", ""))).first()
    if w is None:
        raise Http404()
    v = w.latest_version()
    return _ok(
        _workflow_row(w)
        | {
            "draft_revision": w.draft_revision,
            "draft_graph": w.draft_graph,
            "published_graph": v.graph if v else None,
            "input_schema": v.input_schema if v else (w.draft_graph or {}).get("inputs"),
        }
    )


# --- guide de conception ---------------------------------------------------------------------------------------------------
def get_authoring_guide(request, ws, args):
    from .mcp_design_guide import guide

    return _ok(guide(str(args.get("topic", "all")), ws))


# --- connexions : noms et types seulement ----------------------------------------------------------------------------------
def list_connections(request, ws, args):
    from vault.models import Connection

    q = _text(args)
    rows = [
        {"name": c.name, "label": c.label, "kind": c.kind}  # ni `config`, ni secret, ni identifiant
        for c in Connection.objects.filter(workspace=ws).order_by("name", "id")
        if _match(q, c.name, c.label, c.kind)
    ]
    return _ok({"count": len(rows), "results": rows[: _limit(args)]})


# --- intégrations : description statique des services (aucune donnée de l'espace, aucun secret) ---------------------------
def list_integrations(request, ws, args):
    from catalog import integrations

    slug = _text(args, "slug")
    q = _text(args)
    if slug:
        integ = integrations.INDEX.get(slug)
        if integ is None:
            raise Http404()
        return _ok(
            {
                "slug": integ["slug"],
                "name": integ["name"],
                "connection_kind": integ["connection"]["kind"],
                "setup": integ["connection"].get("setup", ""),
                "operations": [
                    {
                        "id": o["id"],
                        "name": o["name"],
                        "description": o["description"],
                        "params": [
                            {k: prm[k] for k in ("name", "label", "type", "required", "example", "help") if k in prm}
                            for prm in o["params"]
                        ],
                    }
                    for o in integ["operations"]
                ],
            }
        )
    rows = [
        {
            "slug": i["slug"],
            "name": i["name"],
            "category": i["category"],
            "description": i["description"],
            "connection_kind": i["connection"]["kind"],
            "operations": [o["id"] for o in i["operations"]],
        }
        for i in integrations.INDEX.values()
        if _match(q, i["slug"], i["name"], i["category"], i["description"])
    ]
    return _ok({"count": len(rows), "results": rows[: _limit(args)]})


def _spec(name, title, description, props=None, required=None):
    return {
        "name": name,
        "title": title,
        "description": description,
        "inputSchema": {"type": "object", "properties": props or {}, **({"required": required} if required else {})},
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    }


QUERY = {"query": {"type": "string", "description": "Texte recherché (nom, identifiant, description, étiquettes)."}}
LIMIT = {
    "limit": {
        "type": "integer",
        "minimum": 1,
        "maximum": MAX_LIMIT,
        "description": f"Nombre maximum de résultats (défaut {DEFAULT_LIMIT}).",
    }
}

TOOLS = {
    "search_actions": (
        _spec(
            "search_actions",
            "Chercher des actions",
            "Cherche dans le catalogue d'actions (briques exécutables). Renvoie pour chacune son contrat d'entrées et de sorties. À faire AVANT d'en proposer une nouvelle.",
            QUERY | LIMIT | {"domain": {"type": "string", "description": "Filtrer par domaine."}},
        ),
        search_actions,
    ),
    "get_action": (
        _spec(
            "get_action",
            "Lire une action",
            "Détail d'une action : contrat, code de la dernière version, limites, emplacements de connexion.",
            {"slug": SLUG},
            ["slug"],
        ),
        get_action,
    ),
    "list_skills": (
        _spec(
            "list_skills",
            "Chercher des skills IA",
            "Skills IA réutilisables (instructions, entrées, format de réponse).",
            QUERY | LIMIT,
        ),
        list_skills,
    ),
    "get_skill": (
        _spec(
            "get_skill",
            "Lire un skill IA",
            "Détail d'un skill : instructions, entrées et réponse attendue.",
            {"slug": SLUG},
            ["slug"],
        ),
        get_skill,
    ),
    "list_forms": (
        _spec(
            "list_forms",
            "Chercher des formulaires",
            "Formulaires de la bibliothèque, modèles compris (dossiers privés respectés).",
            QUERY | LIMIT,
        ),
        list_forms,
    ),
    "get_form": (
        _spec(
            "get_form",
            "Lire un formulaire",
            "Définition complète d'un formulaire (sections, champs, règles).",
            {"slug": SLUG},
            ["slug"],
        ),
        get_form,
    ),
    "list_workflows": (
        _spec(
            "list_workflows",
            "Chercher des workflows",
            "Workflows de l'espace (dossiers privés respectés).",
            QUERY | LIMIT,
        ),
        list_workflows,
    ),
    "get_workflow": (
        _spec(
            "get_workflow",
            "Lire un workflow",
            "Brouillon et dernière version publiée d'un workflow (graphe, entrées).",
            {"slug": SLUG},
            ["slug"],
        ),
        get_workflow,
    ),
    "get_authoring_guide": (
        _spec(
            "get_authoring_guide",
            "Lire le guide de conception",
            "COMMENCEZ ICI. Format exact d'un graphe de workflow, d'un formulaire, d'une action et d'un skill, avec des exemples validés par le serveur, et la méthode à suivre. Évite de deviner d'après les erreurs.",
            {
                "topic": {
                    "type": "string",
                    "enum": ["all", "method", "essai", "decision", "workflow", "form", "action", "skill"],
                    "description": "Rubrique (défaut : all).",
                }
            },
        ),
        get_authoring_guide,
    ),
    "list_integrations": (
        _spec(
            "list_integrations",
            "Lister les intégrations",
            "Services décrits par la plateforme (Slack, Jira, GitHub, Microsoft 365…) : sans `slug`, la liste avec le type de connexion attendu ; avec `slug`, les opérations et leurs paramètres. "
            "S'utilise dans une étape `http` {integration, operation, params, connection} (voir get_authoring_guide topic=integrations). Données statiques : aucun secret ni contenu d'espace.",
            QUERY
            | LIMIT
            | {"slug": {"type": "string", "description": "Détail d'une intégration (opérations et paramètres)."}},
        ),
        list_integrations,
    ),
    "list_connections": (
        _spec(
            "list_connections",
            "Lister les connexions",
            "Noms, libellés et types des connexions disponibles. Aucun paramètre ni secret n'est jamais renvoyé : un nœud désigne une connexion par son nom.",
            QUERY | LIMIT,
        ),
        list_connections,
    ),
}


def all_tools() -> dict:
    """Lecture + écriture ; les outils d'écriture sont dans `mcp_design_write.py`."""
    from .mcp_design_write import WRITE_TOOLS

    return {**TOOLS, **WRITE_TOOLS}


class McpDesignView(McpServerView):
    """Profil de conception : lire l'espace, préparer des brouillons et proposer des actions / skills. Jamais lancer, publier ni activer."""

    capabilities = {"tools": {"listChanged": False}, "prompts": {"listChanged": False}}
    instructions = (
        "AVANT TOUTE CRÉATION, appelez get_authoring_guide (formats exacts, méthode, décision « action ou script »), puis cherchez ce qui existe. "
        "Serveur de conception : consultez les actions, skills, formulaires, workflows et connexions existants avant d'en créer. "
        "Vous préparez des BROUILLONS et des PROPOSITIONS : rien n'est lancé, publié ni activé par vous, une personne le fait après relecture. "
        "Cherchez toujours ce qui existe avant de créer, lisez la révision d'un brouillon avant de le modifier, et corrigez les erreurs renvoyées."
    )

    def _failed(self, request, ws, tool, message) -> None:
        """Trace un appel en erreur (pour relire où l'assistant a tâtonné) : le message est tronqué, il décrit des champs, pas des valeurs."""
        record(
            "mcp.design.error",
            actor=request.user,
            workspace=ws,
            tool=tool,
            message=str(message)[:200],
            external_assistant=True,
        )

    def _fail(self, request, ws, tool, result) -> dict:
        self._failed(request, ws, tool, result["content"][0]["text"])
        return result

    @staticmethod
    def _client(request) -> str:
        return str(request.META.get("HTTP_USER_AGENT", ""))[:120]

    def on_initialize(self, request, ws, params):
        info = params.get("clientInfo") if isinstance(params.get("clientInfo"), dict) else {}
        record(
            "mcp.design.initialize",
            actor=request.user,
            workspace=ws,
            client=str(info.get("name", ""))[:80],
            client_version=str(info.get("version", ""))[:40],
            user_agent=self._client(request),
            external_assistant=True,
        )

    def post(self, request, workspace):
        # ADR 0025 : le profil de conception exige le moindre privilège. Un jeton qui porte aussi « run » ou « publish » est refusé ici.
        token = getattr(request.user, "_token", None)
        if token is not None and set(token.scopes) - DESIGN_SCOPES:
            record(
                "mcp.design.refused",
                actor=request.user,
                workspace=token.workspace,
                reason="token_too_powerful",
                scopes=sorted(token.scopes),
                user_agent=self._client(request),
                external_assistant=True,
            )
            raise PermissionDenied("Ce profil exige un jeton de conception : portées « view » et « edit » seulement.")
        return super().post(request, workspace)

    def extra_method(self, request, ws, method, params):
        from . import mcp_prompts
        from .mcp import McpParamsError

        if method == "prompts/list":
            return {"prompts": mcp_prompts.list_prompts()}
        if method == "prompts/get":
            name = params.get("name")
            if name not in mcp_prompts.PROMPTS:
                raise McpParamsError(f"Invite inconnue : {name}")
            try:
                result = mcp_prompts.get_prompt(name, params.get("arguments") or {})
            except ValueError as exc:
                raise McpParamsError(str(exc)) from None
            record(
                "mcp.design.prompt",
                actor=request.user,
                workspace=ws,
                prompt=name,
                user_agent=self._client(request),
                external_assistant=True,
            )
            return result
        return None

    def list_tools(self, request, ws) -> list:
        return [spec for spec, _ in all_tools().values()]

    def call_tool(self, request, ws, params) -> dict:
        name, args = params.get("name"), params.get("arguments") or {}
        entry = all_tools().get(name) if isinstance(name, str) else None
        if entry is None:
            # un assistant qui demande « publish_workflow » ou « run_workflow » est un signal à garder : l'outil n'existe pas, la tentative est tracée
            record(
                "mcp.design.refused",
                actor=request.user,
                workspace=ws,
                reason="unknown_tool",
                tool=str(name)[:60],
                user_agent=self._client(request),
                external_assistant=True,
            )
            return _err(f"Outil inconnu : {name}")
        if not isinstance(args, dict):
            return _err("arguments doit être un objet.")
        record(
            "mcp.design.call",
            actor=request.user,
            workspace=ws,
            tool=name,
            user_agent=self._client(request),
            external_assistant=True,
        )
        from rest_framework import serializers

        from . import mcp_design_write as w

        try:
            if name in w.WRITE_NAMES:
                w.check_budget(request, ws)
            result = entry[1](request, ws, args)
            if result.get("isError"):
                self._failed(request, ws, name, result["content"][0]["text"])
            return result
        except Http404:  # inexistant ou caché : même réponse (aucune fuite d'existence)
            return self._fail(request, ws, name, _err("Introuvable."))
        except PermissionDenied as exc:
            return self._fail(request, ws, name, _err(str(exc.detail) if name in w.WRITE_TOOLS else "Introuvable."))
        except w.Throttled as exc:
            return self._fail(request, ws, name, _err(str(exc)))
        except serializers.ValidationError as exc:  # erreurs structurées : l'assistant se corrige seul
            return self._fail(
                request,
                ws,
                name,
                _err(
                    "Refusé : "
                    + json.dumps(exc.detail, ensure_ascii=False, default=str)[:1400]
                    + " — format exact : get_authoring_guide."
                ),
            )
        except APIException as exc:  # conflit de révision, etc.
            return self._fail(
                request, ws, name, _err("Refusé : " + json.dumps(exc.detail, ensure_ascii=False, default=str)[:600])
            )
