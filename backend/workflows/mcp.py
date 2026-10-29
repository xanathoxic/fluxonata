"""MCP server (Model Context Protocol, « Streamable HTTP », JSON responses): lets an assistant or agent call the workspace's
workflows as tools.

Only workflows marked « exposé comme outil MCP » are listed. A call starts a run exactly like any launch (same
authorization, governance and audit; the caller is the token's user) in the environment `test` (or the only one the
token allows) and waits for the result for a few seconds. Authentication: the usual bearer service token."""

import json
import re
import time

from django.http import HttpResponse
from rest_framework.response import Response
from rest_framework.views import APIView

from runs import engine
from runs.models import WorkflowRun
from runs.serializers import validate_inputs_against, with_defaults
from tenancy.models import Environment
from tenancy.permissions import RUN, VIEW, get_workspace, has_perm

from . import access
from .models import Workflow

PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
WAIT_SECONDS = 25  # settings.ORCH_MCP_WAIT_SECONDS overrides
TOOL_NAME = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def _err(rid, code, message):
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def _tools(ws, user):
    out = []
    for wf in access.visible(Workflow.objects.filter(workspace=ws, mcp_exposed=True, deprecated=False), user, ws):
        v = wf.latest_version()
        if v is None or not TOOL_NAME.match(wf.slug):
            continue
        out.append(
            {
                "name": wf.slug,
                "title": wf.name,
                "description": (wf.description or wf.name)[:1000],
                "inputSchema": {
                    "type": "object",
                    **{k: v.input_schema.get(k) for k in ("properties", "required") if v.input_schema.get(k)},
                },
            }
        )
    return out


def _call(request, ws, params) -> dict:
    name, args = params.get("name"), params.get("arguments") or {}
    wf = access.visible(
        Workflow.objects.filter(workspace=ws, slug=name, mcp_exposed=True, deprecated=False), request.user, ws
    ).first()
    if wf is not None and not access.rules(request.user, ws).allows(wf.folder, RUN):
        wf = None
    version = wf.latest_version() if wf else None
    if version is None:
        return {"isError": True, "content": [{"type": "text", "text": f"Outil inconnu : {name}"}]}
    if not has_perm(request.user, ws, RUN):
        return {"isError": True, "content": [{"type": "text", "text": "Droit de lancer requis."}]}
    if not isinstance(args, dict):
        return {"isError": True, "content": [{"type": "text", "text": "arguments doit être un objet."}]}
    args = with_defaults(version.input_schema, args)
    problems = validate_inputs_against(version.input_schema, args)
    if problems:
        return {
            "isError": True,
            "content": [
                {"type": "text", "text": "Arguments invalides : " + json.dumps(problems, ensure_ascii=False)[:500]}
            ],
        }
    allowed = getattr(getattr(request.user, "_token", None), "environments", None) or []
    env = Environment.objects.filter(workspace=ws, kind=allowed[0] if len(allowed) == 1 else "test").first()
    if env is None:
        return {"isError": True, "content": [{"type": "text", "text": "Environnement indisponible."}]}
    try:
        run, _ = engine.start_run(
            workspace=ws, environment=env, workflow_version=version, user=request.user, inputs=args, trigger="mcp"
        )
    except Exception as exc:  # noqa: BLE001  (governance refusals are answered, not raised)
        return {
            "isError": True,
            "content": [{"type": "text", "text": f"Lancement refusé : {str(getattr(exc, 'detail', exc))[:300]}"}],
        }
    from django.conf import settings

    deadline = time.monotonic() + getattr(settings, "ORCH_MCP_WAIT_SECONDS", WAIT_SECONDS)
    while time.monotonic() < deadline:
        run = WorkflowRun.objects.get(pk=run.pk)
        if run.status in ("succeeded", "failed", "cancelled"):
            break
        time.sleep(0.5)
    if run.status == "succeeded":
        return {
            "content": [{"type": "text", "text": json.dumps(run.outputs or {}, ensure_ascii=False)}],
            "structuredContent": run.outputs or {},
            "isError": False,
        }
    if run.status in ("failed", "cancelled"):
        return {
            "isError": True,
            "content": [
                {"type": "text", "text": f"Exécution {run.status} : {(run.error or {}).get('message', '')}"[:600]}
            ],
        }
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(
                    {
                        "status": "running",
                        "run_id": str(run.id),
                        "note": "Toujours en cours : consultez l'exécution dans la plateforme.",
                    }
                ),
            }
        ],
        "isError": False,
    }


class McpServerView(APIView):
    """POST: one JSON-RPC message (or a batch). GET/DELETE: no server-initiated stream, no session."""

    def get(self, request, workspace):
        return HttpResponse(status=405)

    def post(self, request, workspace):
        ws = get_workspace(request.user, workspace, VIEW)
        msg = request.data
        if isinstance(msg, list):
            replies = [r for r in (self._one(request, ws, m) for m in msg) if r is not None]
            return Response(replies) if replies else HttpResponse(status=202)
        reply = self._one(request, ws, msg)
        return Response(reply) if reply is not None else HttpResponse(status=202)

    def _one(self, request, ws, msg):
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
            return _err(msg.get("id") if isinstance(msg, dict) else None, -32600, "Requête JSON-RPC invalide.")
        rid, method, params = msg.get("id"), msg["method"], msg.get("params") or {}
        if rid is None:  # a notification: no answer
            return None
        if method == "initialize":
            asked = params.get("protocolVersion")
            return {
                "jsonrpc": "2.0",
                "id": rid,
                "result": {
                    "protocolVersion": asked if asked in PROTOCOLS else PROTOCOLS[0],
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "fluxonata", "version": "0.3"},
                    "instructions": "Chaque outil lance un workflow publié et attend son résultat (25 s au plus).",
                },
            }
        if method == "ping":
            return {"jsonrpc": "2.0", "id": rid, "result": {}}
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": rid, "result": {"tools": _tools(ws, request.user)}}
        if method == "tools/call":
            return {
                "jsonrpc": "2.0",
                "id": rid,
                "result": _call(request, ws, params if isinstance(params, dict) else {}),
            }
        return _err(rid, -32601, f"Méthode inconnue : {method}")
