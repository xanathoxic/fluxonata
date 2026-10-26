"""Workflow management: metadata, favorites, dependencies, per-workflow audit, script task test."""

from django.http import Http404
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from audit.models import AuditEvent, record
from catalog.models import Action
from core.folders import clean_folder
from runs import services
from runs.builtin import get_script_version
from tenancy.models import Environment
from tenancy.permissions import EDIT, RUN, VIEW, get_workspace

from .models import Favorite, Workflow
from .validation import MAX_SCRIPT, _validate_script, issue


def _wf(ws, slug) -> Workflow:
    wf = Workflow.objects.filter(workspace=ws, slug=slug).first()
    if wf is None:
        raise Http404()
    return wf


class WorkflowMetaView(APIView):
    """PATCH name / description / folder of a workflow (draft metadata; versions are untouched)."""

    def patch(self, request, workspace, slug):
        ws = get_workspace(request.user, workspace, EDIT)
        wf = _wf(ws, slug)
        d = request.data
        if "name" in d:
            if not str(d["name"]).strip():
                raise serializers.ValidationError({"name": "Le nom ne peut pas être vide."})
            wf.name = str(d["name"]).strip()[:160]
        if "description" in d:
            wf.description = str(d["description"])[:2000]
        if "folder" in d:
            wf.folder = clean_folder(d["folder"])
        wf.save()
        record(
            "workflow.update",
            actor=request.user,
            workspace=ws,
            target=wf,
            fields=sorted(set(d) & {"name", "description", "folder"}),
        )
        return Response({"slug": wf.slug, "name": wf.name, "description": wf.description, "folder": wf.folder})


class FavoritesView(APIView):
    def get(self, request, workspace):
        ws = get_workspace(request.user, workspace, VIEW)
        out = []
        for f in Favorite.objects.filter(user=request.user, workspace=ws).order_by("-created_at"):
            obj = (Workflow if f.kind == "workflow" else Action).objects.filter(workspace=ws, slug=f.slug).first()
            if obj is not None:  # the target may have been removed
                out.append({"kind": f.kind, "slug": f.slug, "name": obj.name})
        return Response({"results": out})


class FavoriteDetailView(APIView):
    def _target(self, ws, kind, slug):
        if kind not in ("workflow", "action"):
            raise Http404()
        if not (Workflow if kind == "workflow" else Action).objects.filter(workspace=ws, slug=slug).exists():
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
        wf = _wf(ws, slug)
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
        wf = _wf(ws, slug)
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
        _wf(ws, slug)
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


class WorkflowDeleteView(APIView):
    """Delete a workflow, only if that cannot hurt: never run, not used by another workflow, no schedule/webhook.
    Otherwise deprecate it instead (history, runs and audit must stay consistent)."""

    def delete(self, request, workspace, slug):
        from runs.models import WorkflowRun
        from triggers.models import Schedule, Webhook

        ws = get_workspace(request.user, workspace, EDIT)
        wf = _wf(ws, slug)
        reasons = []
        if WorkflowRun.objects.filter(workflow_version__workflow=wf).exists():
            reasons.append("il a déjà été exécuté")
        if Schedule.objects.filter(workflow=wf).exists() or Webhook.objects.filter(workflow=wf).exists():
            reasons.append("une planification ou un webhook l'utilise")
        from .models import WorkflowVersion

        for wv in WorkflowVersion.objects.filter(workflow__workspace=ws).exclude(workflow=wf):
            if any(n.get("workflow") == slug for n in wv.graph.get("nodes", [])):
                reasons.append(f"{wv.workflow.slug} l'appelle")
                break
        if reasons:
            raise serializers.ValidationError(
                {"workflow": "Suppression refusée : " + ", ".join(reasons) + ". Dépréciez-le plutôt."}
            )
        record("workflow.delete", actor=request.user, workspace=ws, slug=slug, name=wf.name)
        wf.delete()
        return Response(status=204)
