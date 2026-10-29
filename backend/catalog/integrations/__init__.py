"""Declarative integrations: a web service described as DATA (how to connect, what operations it offers), not as code.

An operation turns the values a person fills in (`params`) into one HTTP request that the « Requête HTTP » step sends
through a connection. Adding an integration is adding an entry to `INTEGRATIONS`; `check()` (run by the tests) verifies
every entry is well-formed. These descriptions are written from the services' public API documentation; they have been
checked against a fake server, NOT against the real services (see docs/INTEGRATIONS.md)."""

import json
import re
from urllib.parse import quote

from .catalog import INTEGRATIONS

PARAM_IN = ("path", "query", "body", "header")
PARAM_TYPES = ("string", "text", "integer", "number", "boolean", "object", "array")
BODY_FORMATS = ("json", "form", "text")
METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
CONNECTION_KINDS = ("http_bearer", "api_key", "basic", "oauth2_client", "oauth2_code", "aws_sigv4")
_PLACEHOLDER = re.compile(r"\{([a-z_][a-z0-9_]*)\}")
INDEX = {i["slug"]: i for i in INTEGRATIONS}


def find(slug: str, op_id: str):
    integ = INDEX.get(slug)
    if integ is None:
        return None, None
    return integ, next((o for o in integ["operations"] if o["id"] == op_id), None)


def public() -> list[dict]:
    """What the editor needs (everything is static data: no secret, no workspace content)."""
    return INTEGRATIONS


def _coerce(value, kind):
    if kind == "string" or kind == "text":
        return value if isinstance(value, str) else json.dumps(value) if isinstance(value, (dict, list)) else str(value)
    return value


def _put(body: dict, key: str, value) -> None:
    """`a.b` writes into a nested object (e.g. JSON-RPC `params.name`)."""
    *parents, last = key.split(".")
    cur = body
    for k in parents:
        cur = cur.setdefault(k, {})
    cur[last] = value


def render(op: dict, params: dict) -> dict:
    """Request parts of an operation for the given (already resolved) parameter values. Raises ValueError (French message)."""
    values = params if isinstance(params, dict) else {}
    path, query, headers, body = op["path"], {}, dict(op.get("headers") or {}), dict(op.get("body_defaults") or {})
    for p in op["params"]:
        v = values.get(p["name"])
        missing = v is None or v == ""
        if missing:
            if p.get("required"):
                raise ValueError(f"Paramètre obligatoire manquant : « {p['label']} ».")
            if p.get("default") is None:
                continue
            v = p["default"]
        v = _coerce(v, p["type"])
        if p["in"] == "path":
            path = path.replace("{" + p["name"] + "}", quote(str(v), safe="/" if p.get("raw") else ""))
        elif p["in"] == "query":
            if p.get("key") == "*" and isinstance(v, dict):
                query.update({str(k): x for k, x in v.items()})
            else:
                query[p.get("key", p["name"])] = (
                    json.dumps(v) if isinstance(v, (dict, list)) else str(v).lower() if isinstance(v, bool) else v
                )
        elif p["in"] == "header":
            headers[p.get("key", p["name"])] = str(v)
        elif p.get("key") == "*" and isinstance(v, dict):
            body.update(v)
        else:
            _put(body, p.get("key", p["name"]), v)
    fmt = op.get("body_format", "json")
    payload = None
    if fmt == "text":
        payload = next((body[k] for k in body), None)
    elif body:
        payload = body
    elif op.get("body_static") is not None:
        payload = op["body_static"]
    return {
        "method": op["method"],
        "path": path,
        "query": query,
        "headers": headers,
        "body": payload,
        "body_format": fmt,
        "pagination": op.get("pagination"),
        "fail_if": op.get("fail_if"),
        "sample": op.get("sample"),
    }


def check() -> list[str]:
    """Structural problems in the catalogue (empty = all good). Used by the test-suite."""
    problems: list[str] = []
    seen: set = set()
    for i in INTEGRATIONS:
        slug = i.get("slug", "?")
        if slug in seen or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", slug):
            problems.append(f"{slug}: identifiant invalide ou en double")
        seen.add(slug)
        for key in ("name", "category", "description", "connection", "operations"):
            if not i.get(key):
                problems.append(f"{slug}: {key} manquant")
        conn = i.get("connection") or {}
        if conn.get("kind") not in CONNECTION_KINDS:
            problems.append(f"{slug}: type de connexion invalide")
        if not str((conn.get("config") or {}).get("base_url", "")).startswith("https://"):
            problems.append(f"{slug}: base_url https manquante")
        ids: set = set()
        for op in i.get("operations", []):
            oid = f"{slug}/{op.get('id', '?')}"
            if op.get("id") in ids or not re.fullmatch(r"[a-z0-9_]+", op.get("id", "")):
                problems.append(f"{oid}: identifiant invalide ou en double")
            ids.add(op.get("id"))
            if op.get("method") not in METHODS:
                problems.append(f"{oid}: méthode invalide")
            if not str(op.get("path", "")).startswith("/") or not op.get("name") or not op.get("description"):
                problems.append(f"{oid}: chemin/nom/description invalides")
            if op.get("body_format", "json") not in BODY_FORMATS:
                problems.append(f"{oid}: body_format invalide")
            names = [p.get("name") for p in op.get("params", [])]
            if len(set(names)) != len(names):
                problems.append(f"{oid}: paramètres en double")
            for p in op.get("params", []):
                if p.get("in") not in PARAM_IN or p.get("type") not in PARAM_TYPES or not p.get("label"):
                    problems.append(f"{oid}: paramètre {p.get('name')} invalide")
            in_path = set(_PLACEHOLDER.findall(op.get("path", "")))
            declared = {p["name"] for p in op.get("params", []) if p.get("in") == "path"}
            if in_path != declared:
                problems.append(f"{oid}: variables de chemin {sorted(in_path)} ≠ paramètres {sorted(declared)}")
            if op.get("body_format") == "text" and sum(1 for p in op["params"] if p["in"] == "body") != 1:
                problems.append(f"{oid}: un corps texte prend exactement un paramètre de corps")
            if op["method"] == "GET" and any(p["in"] == "body" for p in op.get("params", [])):
                problems.append(f"{oid}: GET avec corps")
    return problems
