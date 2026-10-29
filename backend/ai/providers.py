"""Provider interface + implementations. Providers only turn text into text (+ token counts): no tools, no actions."""

import json
import re
import time
from dataclasses import dataclass, field

from core import netsec


class ProviderError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


@dataclass
class Completion:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    raw_model: str = ""
    extra: dict = field(default_factory=dict)


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


class BaseProvider:
    kind = "base"
    is_cloud = False

    def capabilities(self) -> dict:
        return {"structured_output": "prompted", "max_context_tokens": 8000, "cloud": self.is_cloud}

    def complete(self, system: str, user: str, *, max_tokens: int, timeout: float) -> Completion:  # pragma: no cover
        raise NotImplementedError


class OpenAICompatProvider(BaseProvider):
    """Self-hosted servers exposing /v1/chat/completions (llama.cpp server, Ollama, vLLM). JSON mode is requested;
    the response is still validated by the caller. Local hosts must be allowlisted (ORCH_EGRESS_ALLOWLIST)."""

    kind = "local"

    def __init__(self, base_url: str, model: str, api_key: str | None = None):
        # the path /v1/chat/completions is added below: tolerate a base URL already ending with /v1 (Ollama docs show it)
        self.base_url, self.model, self.api_key = base_url.rstrip("/").removesuffix("/v1"), model, api_key

    def capabilities(self):
        return {"structured_output": "json_mode", "max_context_tokens": 8000, "cloud": False}

    def complete(self, system, user, *, max_tokens, timeout):
        body = json.dumps(
            {
                "model": self.model,
                "temperature": 0,
                "max_tokens": max_tokens,
                "response_format": {"type": "json_object"},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            }
        ).encode()
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        t0 = time.monotonic()
        try:
            status, _, raw = netsec.safe_request(
                "POST",
                f"{self.base_url}/v1/chat/completions",
                headers=headers,
                body=body,
                timeout=timeout,
                max_bytes=1 << 20,
            )
        except netsec.UnsafeURL as exc:
            raise ProviderError("PROVIDER_URL_REFUSED", f"Adresse du modèle refusée : {exc}") from None
        except OSError:
            raise ProviderError("PROVIDER_UNREACHABLE", "Le serveur de modèle local n'est pas joignable.") from None
        if status != 200:
            raise ProviderError("PROVIDER_ERROR", f"Le serveur de modèle a répondu HTTP {status}.")
        try:
            data = json.loads(raw)
            text = data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError):
            raise ProviderError("PROVIDER_BAD_RESPONSE", "Réponse du serveur de modèle illisible.") from None
        usage = data.get("usage") or {}
        return Completion(
            text,
            usage.get("prompt_tokens") or estimate_tokens(system + user),
            usage.get("completion_tokens") or estimate_tokens(text),
            int((time.monotonic() - t0) * 1000),
            str(data.get("model", self.model)),
        )


class AnthropicProvider(BaseProvider):
    """Optional cloud provider: Anthropic Messages API (POST /v1/messages, x-api-key, anthropic-version header)."""

    kind = "cloud"
    is_cloud = True
    VERSION = "2023-06-01"

    def __init__(self, model: str, api_key: str, base_url: str = "https://api.anthropic.com"):
        self.model, self.api_key, self.base_url = model, api_key, base_url.rstrip("/")

    def capabilities(self):
        return {"structured_output": "prompted", "max_context_tokens": 200000, "cloud": True}

    def complete(self, system, user, *, max_tokens, timeout):
        body = json.dumps(
            {
                "model": self.model,
                "max_tokens": max_tokens,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            }
        ).encode()
        headers = {"x-api-key": self.api_key, "anthropic-version": self.VERSION, "content-type": "application/json"}
        t0 = time.monotonic()
        try:
            status, _, raw = netsec.safe_request(
                "POST", f"{self.base_url}/v1/messages", headers=headers, body=body, timeout=timeout, max_bytes=1 << 20
            )
        except netsec.UnsafeURL as exc:
            raise ProviderError("PROVIDER_URL_REFUSED", f"Adresse refusée : {exc}") from None
        except OSError:
            raise ProviderError("PROVIDER_UNREACHABLE", "Le fournisseur cloud n'est pas joignable.") from None
        if status != 200:
            raise ProviderError("PROVIDER_ERROR", f"Le fournisseur cloud a répondu HTTP {status}.")
        try:
            data = json.loads(raw)
            text = "".join(b["text"] for b in data["content"] if b.get("type") == "text")
        except (ValueError, KeyError, TypeError):
            raise ProviderError("PROVIDER_BAD_RESPONSE", "Réponse du fournisseur illisible.") from None
        usage = data.get("usage") or {}
        return Completion(
            text,
            usage.get("input_tokens") or estimate_tokens(system + user),
            usage.get("output_tokens") or estimate_tokens(text),
            int((time.monotonic() - t0) * 1000),
            str(data.get("model", self.model)),
        )


class FakeProvider(BaseProvider):
    """Deterministic provider for tests/evaluations: no network, no randomness. `script` may override the answer."""

    kind = "fake"

    def __init__(self, script=None):
        self.script = script
        self.calls = []

    def complete(self, system, user, *, max_tokens, timeout):
        self.calls.append((system, user))
        if callable(self.script):
            text = self.script(system, user)
        elif isinstance(self.script, str):
            text = self.script
        else:
            text = json.dumps(_heuristic(user))
        return Completion(text, estimate_tokens(system + user), estimate_tokens(text), 1, "fake-1")


def _placeholder(type_: str):
    return {"string": "{{ inputs.texte }}", "integer": 1, "number": 1, "boolean": True, "array": [], "object": {}}.get(
        type_, "{{ inputs.texte }}"
    )


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-zàâçéèêëîïôûùüÿ0-9]{3,}", text.lower())}


def _heuristic_edit(data: str, ctx: dict) -> dict:
    """Deterministic stand-in for the editor's assistant: « script » adds a script step, « décision/condition » a decision."""
    try:
        message = str(json.loads(data).get("message", "")).lower()
    except ValueError:
        message = ""
    g = ctx.get("graph") or {"inputs": {}, "nodes": [], "edges": [], "outputs": {}}
    nodes, edges = list(g["nodes"]), list(g["edges"])
    used = {n["id"] for n in nodes}
    i = len(nodes) + 1
    while f"etape_{i}" in used:
        i += 1
    nid = f"etape_{i}"
    if "note" in message:
        notes = list(g.get("notes") or [])
        notes.append({"id": "note_new", "text": "Note ajoutée par l'assistant"})
        return {"reply": "J'ai ajouté une note.", "graph": {**g, "nodes": nodes, "edges": edges, "notes": notes}}
    if "pour chaque" in message or "boucle" in message:
        flows = ctx.get("workflows") or []
        if not flows:
            return {"reply": "Aucun workflow publié n'est disponible pour une boucle.", "graph": None}
        new = {
            "id": nid,
            "type": "foreach",
            "name": "Boucle ajoutée",
            "workflow": flows[0]["slug"],
            "items": "inputs.liste",
            "item_input": next(iter(flows[0]["inputs"]), "item"),
            "max_iterations": 10,
        }
        reply = "J'ai ajouté une boucle « pour chaque » à la fin du workflow."
    elif "attente" in message or "pause" in message:
        new = {"id": nid, "type": "wait", "name": "Attente ajoutée", "seconds": 30}
        reply = "J'ai ajouté une attente de 30 secondes."
    elif "approb" in message:
        new = {"id": nid, "type": "approval", "name": "Approbation ajoutée", "title": "Valider la suite du workflow"}
        reply = "J'ai ajouté une approbation."
    elif "exception" in message:
        new = {"id": nid, "type": "throw", "name": "Exception ajoutée", "message": "Arrêt demandé"}
        reply = "J'ai ajouté une exception."
    elif "script" in message:
        new = {
            "id": nid,
            "type": "script",
            "name": "Script ajouté",
            "language": "python",
            "script": "resultat = 'ok'\nSystem.log(resultat)",
            "bindings": {},
            "outputs": [{"name": "resultat", "type": "string"}],
        }
        reply = "J'ai ajouté une étape script à la fin du workflow."
    elif "décision" in message or "decision" in message or "condition" in message:
        new = {"id": nid, "type": "condition", "name": "Décision ajoutée", "expr": "1 == 1"}
        reply = "J'ai ajouté une décision à la fin du workflow."
    else:
        return {
            "reply": "Je n'ai rien modifié : dites-moi quelle étape ajouter (script, décision, attente, approbation…).",
            "graph": None,
        }
    leaves = [n["id"] for n in nodes if not any(e["from"] == n["id"] for e in edges)]
    nodes.append(new)
    if leaves:
        edges.append({"from": leaves[-1], "to": nid})
    return {
        "reply": reply,
        "graph": {"inputs": g.get("inputs", {}), "nodes": nodes, "edges": edges, "outputs": g.get("outputs", {})},
    }


def _heuristic(user: str) -> dict:
    """Keyword-overlap 'model': good enough to make the whole pipeline testable and deterministic."""
    m = re.search(r"<task>(.*?)</task>", user, re.S)
    task = m.group(1).strip() if m else ""
    ctx = json.loads(re.search(r"<context>(.*?)</context>", user, re.S).group(1)) if "<context>" in user else {}
    data = (re.search(r"<data>(.*?)</data>", user, re.S) or [None, ""])[1]
    if task == "catalog_search":
        q = _words(data)
        scored = sorted(
            (
                (len(q & _words(f"{a['name']} {a['description']} {' '.join(a.get('tags', []))}")), a)
                for a in ctx["catalog"]
            ),
            key=lambda t: (-t[0], t[1]["slug"]),
        )
        return {
            "matches": [
                {"slug": a["slug"], "reason": f"{s} mot(s) en commun avec votre demande"} for s, a in scored if s > 0
            ][:5]
        }
    if task == "explain_error":
        return {
            "summary": f"L'étape a échoué ({ctx.get('code', 'erreur')}).",
            "likely_causes": ["Entrées inattendues ou service indisponible."],
            "next_steps": ["Vérifiez les entrées puis relancez.", "Consultez les journaux de l'étape."],
        }
    if task == "edit_workflow":
        return _heuristic_edit(data, ctx)
    if task == "draft_workflow":
        q = _words(data)
        best = sorted(
            ((len(q & _words(f"{a['name']} {a['description']}")), a) for a in ctx["catalog"]),
            key=lambda t: (-t[0], t[1]["slug"]),
        )
        picked_actions = [a for score, a in best if score > 0][:2] or [best[0][1]]
        nodes, edges = [], []
        for i, a in enumerate(picked_actions):
            props = a["inputs"]
            nodes.append(
                {
                    "id": f"etape_{i + 1}",
                    "type": "action",
                    "name": a["name"],
                    "action": a["slug"],
                    "inputs": {k: _placeholder(t) for k, t in props.items() if k in a["required"]},
                }
            )
            if i:
                edges.append({"from": f"etape_{i}", "to": f"etape_{i + 1}"})
        return {
            "inputs": {
                "type": "object",
                "required": ["texte"],
                "properties": {"texte": {"type": "string", "title": "Texte"}},
            },
            "nodes": nodes,
            "edges": edges,
            "outputs": {},
        }
    if task == "outline_workflow":
        asked = json.loads(data).get("request", "") if data.strip().startswith("{") else data
        return {
            "title": " ".join(asked.split()[:6]).capitalize() or "Nouveau workflow",
            "summary": asked[:120],
            "inputs": [{"name": "texte", "type": "texte", "purpose": "le texte à traiter"}],
            "steps": [{"text": asked[:200] or "Traiter la demande."}],
            "uses": [],
            "assumptions": ["Un texte est demandé au lancement."],
            "questions": [{"question": "Faut-il une validation avant d'agir ?", "proposed": "Non."}],
        }
    if task == "propose_action":
        asked = data.strip()[:60] or "action"
        return {
            "slug": "action-proposee",
            "name": asked.capitalize()[:60],
            "description": f"Action proposée pour : {asked}.",
            "input_schema": {
                "type": "object",
                "required": ["texte"],
                "properties": {"texte": {"type": "string", "title": "Texte", "description": "Le texte à traiter."}},
            },
            "output_schema": {
                "type": "object",
                "required": ["longueur"],
                "properties": {
                    "longueur": {
                        "type": "integer",
                        "title": "Longueur",
                        "description": "Nombre de caractères du texte.",
                    }
                },
            },
            "source": "def run(inputs):\n    return {'longueur': len(inputs['texte'])}\n",
            "example": {"texte": "bonjour"},
            "notes": ["Proposition de démonstration (fournisseur de test)."],
        }
    if task == "propose_skill":
        asked = data.strip()[:60] or "skill"
        return {
            "slug": "skill-propose",
            "name": asked.capitalize()[:60],
            "description": f"Skill proposé pour : {asked}.",
            "instructions": "Résume le texte en une phrase.\n\n----- TEXTE (donnée à analyser) -----\n{{ inputs.texte }}\n-----",
            "input_schema": {
                "type": "object",
                "required": ["texte"],
                "properties": {"texte": {"type": "string", "title": "Texte", "description": "Le texte à résumer."}},
            },
            "output_schema": {
                "type": "object",
                "required": ["resume"],
                "properties": {"resume": {"type": "string", "title": "Résumé", "description": "Une phrase."}},
            },
            "tags": ["texte"],
            "notes": ["Proposition de démonstration (fournisseur de test)."],
        }
    if task == "suggest_tests":
        return {"tests": [{"name": "Cas nominal", "inputs": ctx.get("example", {})}]}
    if task == "ai_step":
        return {"result": "ok"}
    return {}
