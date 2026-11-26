"""Workflow management: metadata, favorites, dependencies, per-workflow audit, script task test."""

from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import Http404
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from audit.models import AuditEvent, record
from catalog.models import Action
from core.folders import clean_folder
from core.tags import clean_tags
from runs import services
from runs.builtin import get_script_version
from tenancy.models import Environment
from tenancy.permissions import EDIT, RUN, VIEW, get_workspace

from . import access, cache_spec
from .models import Favorite, Workflow
from .validation import MAX_SCRIPT, _validate_script, issue


def _wf(user, ws, slug, perm=VIEW) -> Workflow:
    return access.workflow_or_404(user, ws, slug, perm)


def _chosen_runner(ws, value):
    """Le runner imposé choisi dans les réglages : vide = aucun ; sinon il doit exister ET être ouvert à cet espace (dédié à cet espace, ou partagé et
    runners partagés autorisés) : on n'impose jamais un runner qu'une autre équipe réserve."""
    from runs.models import Runner
    from runs.runner_pin import _allowed_in

    if value in (None, ""):
        return None
    try:
        runner = Runner.objects.filter(pk=str(value)).first()
    except (ValueError, TypeError, DjangoValidationError):  # pas un identifiant de runner
        runner = None
    if runner is None or not _allowed_in(runner, ws):
        raise serializers.ValidationError({"runner": "Runner introuvable ou non ouvert à cet espace."})
    return runner


def _set_runner(wf, ws, d) -> None:
    """Réglages de runner d'un workflow : le runner imposé et l'extension à ses sous-workflows."""
    if "runner" in d:
        wf.runner = _chosen_runner(ws, d["runner"])
    if "runner_force_children" in d:
        wf.runner_force_children = bool(d["runner_force_children"])


def _set_run_mode(wf, d) -> None:
    """Mode d'exécution : concurrent (défaut) ou sequential (un seul lancement actif à la fois, les autres attendent)."""
    if "run_mode" not in d:
        return
    if d["run_mode"] not in Workflow.RunMode.values:
        raise serializers.ValidationError({"run_mode": "Mode d'exécution inconnu (concurrent ou sequential)."})
    wf.run_mode = d["run_mode"]


def _set_cache_publish(wf, d) -> None:
    """Publication du résultat dans le cache nommé (ADR 0032) : clé fixe (vide = désactivé) et durée de vie facultative."""
    if "cache_key" in d:
        key = str(d["cache_key"] or "").strip()
        if key and not cache_spec.KEY_RE.match(key):
            raise serializers.ValidationError(
                {"cache_key": "Clé invalide : 1 à 120 caractères (lettres, chiffres, . _ : / -)."}
            )
        wf.cache_key = key
    if "cache_sensitive" in d:
        if not isinstance(d["cache_sensitive"], bool):
            raise serializers.ValidationError({"cache_sensitive": "Vrai ou faux."})
        wf.cache_sensitive = d["cache_sensitive"]
    if "cache_ttl_seconds" in d:
        ttl = d["cache_ttl_seconds"]
        if ttl in (None, ""):
            wf.cache_ttl_seconds = None
        elif isinstance(ttl, bool) or not isinstance(ttl, int) or not 1 <= ttl <= cache_spec.MAX_TTL_SECONDS:
            raise serializers.ValidationError(
                {"cache_ttl_seconds": f"Un nombre de secondes entre 1 et {cache_spec.MAX_TTL_SECONDS}, ou vide."}
            )
        else:
            wf.cache_ttl_seconds = ttl


class WorkflowMetaView(APIView):
    """PATCH name / description / folder / error workflow of a workflow (draft metadata; versions are untouched)."""

    def patch(self, request, workspace, slug):
        ws = get_workspace(request.user, workspace, EDIT)
        wf = _wf(request.user, ws, slug, EDIT)
        d = request.data
        if "name" in d:
            if not str(d["name"]).strip():
                raise serializers.ValidationError({"name": "Le nom ne peut pas être vide."})
            wf.name = str(d["name"]).strip()[:160]
        if "description" in d:
            wf.description = str(d["description"])[:2000]
        if "folder" in d:
            wf.folder = clean_folder(d["folder"])
            access.require(
                request.user, ws, wf.folder, EDIT
            )  # one cannot move a workflow into a folder one cannot edit
        if "tags" in d:
            wf.tags = clean_tags(d["tags"])
        if "mcp_exposed" in d:
            wf.mcp_exposed = bool(d["mcp_exposed"])
        _set_run_mode(wf, d)
        _set_cache_publish(wf, d)
        _set_runner(wf, ws, d)
        for field, label in (("error_workflow", "d'erreur"), ("success_workflow", "de succès")):
            if field in d:
                target = str(d[field] or "").strip()
                if target:
                    if target == wf.slug:
                        raise serializers.ValidationError(
                            {field: f"Un workflow ne peut pas être son propre workflow {label}."}
                        )
                    if not Workflow.objects.filter(workspace=ws, slug=target).exists():
                        raise serializers.ValidationError({field: f"Workflow {label} introuvable dans l'espace."})
                setattr(wf, field, target)
        wf.save()
        record(
            "workflow.update",
            actor=request.user,
            workspace=ws,
            target=wf,
            fields=sorted(
                set(d)
                & {
                    "name",
                    "description",
                    "folder",
                    "tags",
                    "error_workflow",
                    "success_workflow",
                    "mcp_exposed",
                    "run_mode",
                    "cache_key",
                    "cache_ttl_seconds",
                    "cache_sensitive",
                    "runner",
                    "runner_force_children",
                }
            ),
            runner=wf.runner.name if wf.runner_id else None,
        )
        return Response(
            {
                "slug": wf.slug,
                "name": wf.name,
                "description": wf.description,
                "folder": wf.folder,
                "tags": wf.tags,
                "error_workflow": wf.error_workflow,
                "success_workflow": wf.success_workflow,
                "mcp_exposed": wf.mcp_exposed,
                "run_mode": wf.run_mode,
                "cache_key": wf.cache_key,
                "cache_ttl_seconds": wf.cache_ttl_seconds,
                "cache_sensitive": wf.cache_sensitive,
                "runner": str(wf.runner_id) if wf.runner_id else None,
                "runner_name": wf.runner.name if wf.runner_id else None,
                "runner_force_children": wf.runner_force_children,
            }
        )


class FavoritesView(APIView):
    def get(self, request, workspace):
        ws = get_workspace(request.user, workspace, VIEW)
        out = []
        rules = access.rules(request.user, ws)
        for f in Favorite.objects.filter(user=request.user, workspace=ws).order_by("-created_at"):
            obj = (Workflow if f.kind == "workflow" else Action).objects.filter(workspace=ws, slug=f.slug).first()
            if obj is not None and f.kind == "workflow" and not rules.allows(obj.folder, VIEW):
                obj = None
            if obj is not None:  # the target may have been removed
                out.append({"kind": f.kind, "slug": f.slug, "name": obj.name})
        return Response({"results": out})


class FavoriteDetailView(APIView):
    def _target(self, ws, kind, slug):
        if kind not in ("workflow", "action"):
            raise Http404()
        if kind == "workflow":
            access.workflow_or_404(self.request.user, ws, slug)
        elif not Action.objects.filter(workspace=ws, slug=slug).exists():
            raise Http404()

    def put(self, request, workspace, kind, slug):
        ws = get_workspace(request.user, workspace, VIEW)
        self._target(ws, kind, slug)
        Favorite.objects.get_or_create(user=request.user, workspace=ws, kind=kind, slug=slug)
        return Response(status=204)

    def delete(self, request, workspace, kind, slug):
        ws = get_workspace(request.user, workspace, VIEW)
        Favorite.objects.filter(user=request.user, workspace=ws, kind=kind, slug=slug).delete()
        return Response(status=204)


class DependenciesView(APIView):
    """What this workflow (draft) needs: actions (+ version in use), sub-workflows, connections, AI providers."""

    def get(self, request, workspace, slug):
        ws = get_workspace(request.user, workspace, VIEW)
        wf = _wf(request.user, ws, slug)
        rules = access.rules(request.user, ws)
        latest = wf.latest_version()
        graph = (
            wf.draft_graph if wf.draft_graph.get("nodes") or latest is None else latest.graph
        )  # the draft, else what is published
        actions, subs, conns, provs, skills = {}, {}, set(), set(), {}
        for n in graph.get("nodes", []):
            t = n.get("type")
            if t == "action":
                a = Action.objects.filter(workspace=ws, slug=n.get("action")).first()
                v = a.latest_version() if a else None
                actions[n.get("action")] = {
                    "slug": n.get("action"),
                    "name": a.name if a else None,
                    "pinned": n.get("version"),
                    "latest": v.version if v else None,
                    "deprecated": bool(a and a.status == "deprecated"),
                    "exists": a is not None,
                }
                conns |= set((n.get("connections") or {}).values())
                if n.get("compensation"):
                    actions.setdefault(
                        n["compensation"].get("action"),
                        {
                            "slug": n["compensation"].get("action"),
                            "name": None,
                            "pinned": None,
                            "latest": None,
                            "deprecated": False,
                            "exists": Action.objects.filter(
                                workspace=ws, slug=n["compensation"].get("action")
                            ).exists(),
                        },
                    )
            elif t in ("subworkflow", "foreach"):
                w = Workflow.objects.filter(workspace=ws, slug=n.get("workflow")).first()
                if w is not None and not rules.allows(w.folder, VIEW):
                    w = None  # a workflow in a private folder is not even named to someone without access
                subs[n.get("workflow")] = {
                    "slug": n.get("workflow"),
                    "name": w.name if w else None,
                    "exists": w is not None,
                }
            elif t == "ai":
                provs.add(n.get("provider"))
                if n.get("skill"):
                    from ai.models import AiSkill

                    sk = AiSkill.objects.filter(workspace=ws, slug=n["skill"]).first()
                    sv = sk.latest_version() if sk else None
                    skills[n["skill"]] = {
                        "slug": n["skill"],
                        "name": sk.name if sk else None,
                        "pinned": n.get("skill_version"),
                        "latest": sv.version if sv else None,
                        "deprecated": bool(sk and sk.status == "deprecated"),
                        "exists": sk is not None,
                    }
                    provs.add(n.get("provider") or (sv.default_provider if sv else ""))
        return Response(
            {
                "actions": list(actions.values()),
                "workflows": list(subs.values()),
                "skills": list(skills.values()),
                "connections": sorted(c for c in conns if c),
                "ai_providers": sorted(p for p in provs if p),
            }
        )


class WorkflowAuditView(APIView):
    """Audit trail of this workflow (readable by anyone who can view it; the workspace-wide audit stays admin-only)."""

    def get(self, request, workspace, slug):
        ws = get_workspace(request.user, workspace, VIEW)
        wf = _wf(request.user, ws, slug)
        qs = AuditEvent.objects.filter(workspace=ws).filter(
            target_type="Workflow", target_id=str(wf.pk)
        ) | AuditEvent.objects.filter(workspace=ws, data__workflow=slug)
        rows = qs.order_by("-id")[:200]
        return Response(
            {
                "results": [
                    {
                        "id": e.id,
                        "created_at": e.created_at,
                        "actor": e.actor_label,
                        "action": e.action,
                        "details": {
                            k: v
                            for k, v in e.data.items()
                            if k in ("version", "environment", "to", "new", "fields", "rules")
                        },
                    }
                    for e in rows
                ]
            }
        )


class ScriptTestView(APIView):
    """Run a script task in isolation (same sandbox as a real run) with sample values; follow it via /executions/{id}/."""

    def post(self, request, workspace, slug):
        ws = get_workspace(request.user, workspace, EDIT)
        get_workspace(request.user, workspace, RUN)
        _wf(request.user, ws, slug, EDIT)
        node = request.data.get("node")
        if not isinstance(node, dict):
            raise serializers.ValidationError({"node": "Étape script attendue."})
        node = {
            "id": "test",
            **{k: node.get(k) for k in ("language", "script", "outputs", "timeout_seconds") if k in node},
            "bindings": node.get("bindings") or {},
        }
        node.setdefault("timeout_seconds", 60)
        probs = []
        out_types = _validate_script(node, probs)
        sample_inputs, sample_vars, bindings = (
            request.data.get("inputs") or {},
            request.data.get("vars") or {},
            node["bindings"],
        )
        if (
            not all(isinstance(x, dict) for x in (sample_inputs, sample_vars, bindings))
            or len(str(request.data)) > MAX_SCRIPT * 2
        ):
            probs.append(issue("test", "bad_sample", "Valeurs d'essai invalides ou trop volumineuses."))
        errors = [p for p in probs if p["level"] == "error"]
        if errors:
            raise serializers.ValidationError({"issues": errors})
        env = (
            Environment.objects.filter(workspace=ws, kind="test").first()
            or Environment.objects.filter(workspace=ws).exclude(kind="prod").first()
        )
        if env is None:
            raise serializers.ValidationError({"environment": "Aucun environnement hors production pour tester."})
        ex = services.enqueue(
            workspace=ws,
            environment=env,
            action_version=get_script_version(ws, node["language"]),
            user=request.user,
            inputs={
                "script": node["script"],
                "inputs": sample_inputs,
                "vars": sample_vars,
                "bindings": bindings,
                "outputs": list(out_types),
            },
            limits={"timeout_seconds": node["timeout_seconds"]},
        )
        record("workflow.script_test", actor=request.user, workspace=ws, workflow=slug, language=node["language"])
        return Response(
            {"execution_id": str(ex.id), "url": f"/api/v1/workspaces/{ws.slug}/executions/{ex.id}/"}, status=202
        )


class AnsibleStepSchemaView(APIView):
    """Les paramètres et les sorties de l'étape « Playbook Ansible » : une seule source, que l'éditeur lit pour construire ses champs."""

    def get(self, request, workspace):
        from catalog import ansible_inline

        get_workspace(request.user, workspace, VIEW)
        return Response({"input_schema": ansible_inline.INPUT_SCHEMA, "output_schema": ansible_inline.OUTPUT_SCHEMA})


class AnsibleTestView(APIView):
    """Essaie le playbook d'une étape avec des valeurs d'essai, sur les vraies machines désignées (même chemin qu'une exécution : runner portant les
    étiquettes, inventaire, connexions, rôles) ; suivi par /executions/{id}/. Mettre `check: true` dans les valeurs d'essai pour ne rien modifier."""

    def post(self, request, workspace, slug):
        from catalog import ansible_inline

        ws = get_workspace(request.user, workspace, EDIT)
        get_workspace(request.user, workspace, RUN)
        _wf(request.user, ws, slug, EDIT)
        node = request.data.get("node")
        inputs = request.data.get("inputs") or {}
        connections = request.data.get("connections") or {}
        if not isinstance(node, dict):
            raise serializers.ValidationError({"node": "Étape Playbook attendue."})
        if not isinstance(inputs, dict) or not isinstance(connections, dict) or len(str(request.data)) > MAX_SCRIPT * 2:
            raise serializers.ValidationError({"inputs": "Valeurs d'essai invalides ou trop volumineuses."})
        errors = [issue("test", code, message) for code, message in ansible_inline.problems(ws, node)]
        if ("inventory_ref" in inputs) == ("inventory" in inputs):
            errors.append(
                issue("test", "bad_inventory", "Indiquez « inventory_ref » OU « inventory » dans les valeurs d'essai.")
            )
        if errors:
            raise serializers.ValidationError({"issues": errors})
        from runs.serializers import validate_inputs_against

        if problems := validate_inputs_against(ansible_inline.INPUT_SCHEMA, inputs):
            raise serializers.ValidationError(problems)
        env = (
            Environment.objects.filter(workspace=ws, kind="test").first()
            or Environment.objects.filter(workspace=ws).exclude(kind="prod").first()
        )
        if env is None:
            raise serializers.ValidationError({"environment": "Aucun environnement hors production pour tester."})
        ex = services.enqueue(
            workspace=ws,
            environment=env,
            action_version=ansible_inline.inline_version(ws, request.user, node),
            user=request.user,
            inputs=inputs,
            connections={k: str(v) for k, v in connections.items()},
        )
        record("workflow.ansible_test", actor=request.user, workspace=ws, workflow=slug)
        return Response(
            {"execution_id": str(ex.id), "url": f"/api/v1/workspaces/{ws.slug}/executions/{ex.id}/"}, status=202
        )


class WorkflowDeleteView(APIView):
    """Supprime un workflow, seulement si aucun AUTRE workflow ne l'appelle (409 avec la liste). Son historique d'exécutions et ses déclencheurs
    (planifications, webhooks, formulaires publics, interrogations) le protègent, sauf demande expresse : `?with_history=1` supprime aussi
    les exécutions, `?with_triggers=1` aussi les déclencheurs (définitif ; une exécution encore en cours bloque toujours)."""

    def delete(self, request, workspace, slug):
        from catalog.usage import refuse_if_used

        from .removal import delete_workflow

        ws = get_workspace(request.user, workspace, EDIT)
        wf = _wf(request.user, ws, slug, EDIT)
        refuse_if_used(
            ws, request.user, "workflow", slug, "ce workflow"
        )  # 409 + la liste des workflows qui l'appellent
        name = wf.name
        removed = delete_workflow(
            wf,
            with_history=request.query_params.get("with_history") in ("1", "true"),
            with_triggers=request.query_params.get("with_triggers") in ("1", "true"),
        )
        record(
            "workflow.delete",
            actor=request.user,
            workspace=ws,
            slug=slug,
            name=name,
            history_deleted=removed["history"],
            triggers_deleted=removed["triggers"],
        )
        return Response(status=204)
