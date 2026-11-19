"""Point d'accès MCP d'un produit : les workflows du produit deviennent des outils pour l'application qui détient la clé.

Même porte que l'appel REST : clé `fxs_…` (jamais de session ni de jeton), puis `service.invoke` — plan, quotas, environnement,
identité d'exécution et gouvernance sont donc appliqués à l'identique et chaque appel est consigné sans valeur d'entrée.
L'environnement est le premier du plan (l'administrateur le choisit) : un outil MCP n'élargit jamais ce que le plan permet."""

import json
import re
import time

from django.conf import settings
from django.http import HttpResponse
from drf_spectacular.utils import extend_schema
from rest_framework.response import Response

from runs.models import WorkflowRun
from workflows.mcp import PROTOCOLS, AnyAccept, _err

from . import service
from .consume_views import ConsumerView, _key_from, _refusal

TOOL_NAME = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
WAIT_SECONDS = 25


def _text(message: str, *, error: bool = True) -> dict:
    return {"isError": error, "content": [{"type": "text", "text": message}]}


def _tools(sub) -> list:
    out = []
    for link in sub.product.links.select_related("workflow").order_by("workflow__slug"):
        version = link.workflow.latest_version()
        if version is None or not TOOL_NAME.match(link.workflow.slug):
            continue
        schema = version.input_schema or {}
        out.append(
            {
                "name": link.workflow.slug,
                "title": link.workflow.name,
                "description": (link.workflow.description or link.workflow.name)[:1000],
                "inputSchema": {
                    "type": "object",
                    **{k: schema[k] for k in ("properties", "required") if schema.get(k)},
                },
            }
        )
    return out


def _call(key, params: dict) -> dict:
    args = params.get("arguments") or {}
    if not isinstance(args, dict):
        return _text("arguments doit être un objet.")
    sub = key.subscription
    try:
        run, _ = service.invoke(
            service.Call(
                key=key,
                workflow_slug=str(params.get("name", ""))[:80],
                environment=sub.plan.environments[0],
                inputs=args,
            )
        )
    except service.Refused as exc:
        return _text(f"Refusé ({exc.outcome}) : {exc.message}")
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
        return _text(
            f"Exécution {run.status}."
        )  # le message d'erreur interne n'est pas montré à un consommateur externe
    return _text(json.dumps({"status": "running", "run_id": str(run.id)}), error=False)


@extend_schema(exclude=True)
class ProductMcpView(ConsumerView):
    content_negotiation_class = AnyAccept

    def get(self, request, workspace, product):
        return HttpResponse(status=405)

    def post(self, request, workspace, product):
        try:
            key = _key_from(request)
        except service.Refused as exc:
            return _refusal(exc)
        sub = key.subscription
        if sub.product.slug != product or sub.product.workspace.slug != workspace:
            return _refusal(service.Refused(404, "forbidden", "Produit inconnu."))
        msg = request.data
        if isinstance(msg, list):
            replies = [r for r in (self._one(key, m) for m in msg) if r is not None]
            return Response(replies) if replies else HttpResponse(status=202)
        reply = self._one(key, msg)
        return Response(reply) if reply is not None else HttpResponse(status=202)

    def _one(self, key, msg):
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
            return _err(msg.get("id") if isinstance(msg, dict) else None, -32600, "Requête JSON-RPC invalide.")
        rid, method = msg.get("id"), msg["method"]
        params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
        if rid is None:
            return None
        if method == "initialize":
            asked = params.get("protocolVersion")
            result = {
                "protocolVersion": asked if asked in PROTOCOLS else PROTOCOLS[0],
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "fluxonata-apistore", "version": "0.1"},
                "instructions": "Chaque outil lance un workflow du produit selon votre plan et attend son résultat (25 s au plus).",
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": _tools(key.subscription)}
        elif method == "tools/call":
            result = _call(key, params)
        else:
            return _err(rid, -32601, f"Méthode inconnue : {method}")
        return {"jsonrpc": "2.0", "id": rid, "result": result}
