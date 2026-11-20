"""Documentation générée d'un produit de l'API store : endpoints, schémas d'entrée, sortie, erreurs, exemples d'appel.

Tout est calculé à la demande à partir de l'état réel (workflows publiés du produit, plans, schémas d'entrée de leur dernière version) :
rien n'est écrit à la main, donc rien ne peut se périmer. Aucune valeur secrète n'apparaît : les exemples portent un marqueur de clé
(`fxs_VOTRE_CLE`) et un marqueur d'hôte (`<hôte>`, remplacé par l'interface). Aucune entrée ni sortie d'une exécution réelle n'est lue.
"""

import json

from .models import Product

KEY_PLACEHOLDER = "fxs_VOTRE_CLE"
HOST_PLACEHOLDER = "<hôte>"
OPENAPI_SERVER = "https://hote.exemple"  # remplacé par l'adresse réelle côté interface avant téléchargement
ENV_ORDER = ["dev", "test", "prod"]
MAX_PARAMETERS = 200  # borne la taille de la réponse pour un schéma démesuré

ERRORS = [
    (400, "invalid", "Entrées invalides (le message dit quel champ) ou corps mal formé."),
    (401, "invalid_key", "Clé absente, invalide, expirée ou révoquée."),
    (403, "inactive", "Souscription suspendue ou produit non publié."),
    (403, "forbidden", "Environnement non autorisé par votre plan."),
    (403, "governance", "Refus de la gouvernance de l'espace (par exemple version non promue en production)."),
    (404, "forbidden", "Workflow ou exécution inconnus pour ce produit (même réponse qu'un produit absent)."),
    (409, "unavailable", "Environnement ou version publiée indisponible."),
    (429, "rate_limited", "Quota par minute ou par jour atteint : réessayez plus tard."),
]
RUN_STATUSES = [
    ("running", "En cours."),
    ("compensating", "Une étape a échoué, les compensations s'exécutent."),
    ("succeeded", "Réussie : `outputs` est renseigné."),
    ("failed", "Échouée : `outputs` vaut `null`."),
    ("cancelled", "Annulée."),
]

RESPONSE_FIELDS = [
    ("status", "État global : " + ", ".join(f"`{c}`" for c, _ in RUN_STATUSES) + "."),
    ("finished", "`true` quand l'exécution est terminée (réussie, échouée ou annulée) : arrêtez d'interroger."),
    ("outputs", "Sorties du workflow, seulement à la réussite ; sinon `null`."),
    ("progress.steps_done", "Étapes passées : réussies, ou ignorées parce que leur branche n'a pas été prise."),
    (
        "progress.steps_total",
        "Nombre d'étapes du workflow. Avec des branches, toutes ne s'exécutent pas : `steps_done` rejoint ce total à la fin.",
    ),
    (
        "progress.current_step",
        "Étape en cours : `name`, `status` (`running`, `retry_wait`, `waiting`, `uncertain`) et `status_label`. `null` si rien ne tourne ; pour une exécution échouée, c'est l'étape qui a échoué.",
    ),
    ("progress.also_active", "Nombre d'autres étapes actives en parallèle (0 en l'absence de branches parallèles)."),
    ("progress.workflow_status", "État global, repris dans `progress` avec son libellé (`workflow_status_label`)."),
]


_STRING_EXAMPLES = {
    "date": "2026-01-31",
    "date-time": "2026-01-31T09:00:00Z",
    "email": "nom@exemple.fr",
    "uri": "https://exemple.fr",
}


def _typed_example(kind, spec: dict) -> object:
    """Valeur plausible selon le type déclaré (aucune valeur déclarée ni énumérée n'a été trouvée avant)."""
    if kind == "string":
        return _STRING_EXAMPLES.get(spec.get("format"), "texte")
    if kind == "integer":
        low = spec.get("minimum")
        return low if isinstance(low, int) and low > 1 else 1
    if kind == "number":
        return float(spec.get("minimum", 1.0))
    if kind == "boolean":
        return True
    if kind == "array":
        item = example_value(spec.get("items") or {})
        return [] if item is None else [item]
    if kind == "object":
        return {n: example_value(p) for n, p in (spec.get("properties") or {}).items()}
    return None


def example_value(spec) -> object:
    """Valeur plausible pour une propriété d'un schéma : défaut, exemple, première valeur énumérée, sinon selon le type."""
    if not isinstance(spec, dict):
        return None
    declared = spec.get("examples") or spec.get("enum")
    if "default" in spec:
        return spec["default"]
    if declared:
        return declared[0]
    kind = spec.get("type")
    if isinstance(kind, list):
        kind = next((k for k in kind if k != "null"), None)
    return _typed_example(kind, spec)


def example_inputs(schema: dict) -> dict:
    return {name: example_value(spec) for name, spec in (schema.get("properties") or {}).items()}


def _type_label(spec: dict) -> str:
    kind = spec.get("type")
    if isinstance(kind, list):
        kind = " ou ".join(kind)
    if kind == "array":
        inner = (spec.get("items") or {}).get("type")
        kind = f"liste de {inner}" if isinstance(inner, str) else "liste"
    label = kind or "tout type"
    return f"{label} ({spec['format']})" if spec.get("format") else label


def _constraints(spec: dict) -> list[str]:
    out = []
    for key, text in (
        ("minimum", "min. {}"),
        ("maximum", "max. {}"),
        ("minLength", "au moins {} caractères"),
        ("maxLength", "au plus {} caractères"),
        ("minItems", "au moins {} éléments"),
        ("maxItems", "au plus {} éléments"),
        ("pattern", "motif {}"),
    ):
        if key in spec:
            out.append(text.format(spec[key]))
    return out


def parameters(schema: dict) -> list[dict]:
    """Propriétés de premier niveau d'un schéma d'entrée, à plat, avec obligatoire / défaut / valeurs permises."""
    required = set(schema.get("required") or [])
    rows = []
    for name, spec in list((schema.get("properties") or {}).items())[:MAX_PARAMETERS]:
        spec = spec if isinstance(spec, dict) else {}
        rows.append(
            {
                "name": name,
                "type": _type_label(spec),
                "required": name in required and "default" not in spec,
                "default": spec.get("default"),
                "has_default": "default" in spec,
                "enum": spec.get("enum"),
                "description": spec.get("description") or spec.get("title") or "",
                "constraints": _constraints(spec),
            }
        )
    return rows


def output_fields(graph) -> list[str]:
    """Noms des sorties déclarées par le workflow. Leur type n'est pas déclaré par la plateforme : on ne l'invente pas."""
    outs = graph.get("outputs") if isinstance(graph, dict) else None
    return sorted(outs) if isinstance(outs, dict) else []


def _shell_quote(text: str) -> str:
    return "'" + text.replace("'", "'\\''") + "'"


def snippets(method: str, url: str, body: dict | None, *, idempotent: bool) -> dict[str, str]:
    """Trois façons d'appeler le même endpoint : curl, Python (bibliothèque standard), JavaScript (fetch)."""
    headers = {"Authorization": f"Bearer {KEY_PLACEHOLDER}"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if idempotent:
        headers["Idempotency-Key"] = "<identifiant-unique>"
    payload = json.dumps(body, ensure_ascii=False) if body is not None else None
    curl = [f"curl -X {method} {url}"] + [f"  -H {_shell_quote(f'{k}: {v}')}" for k, v in headers.items()]
    if payload is not None:
        curl.append(f"  -d {_shell_quote(payload)}")
    py = ["import json, urllib.request", ""]
    if body is not None:
        py.append(f"corps = {body!r}")
    py += [
        "requete = urllib.request.Request(",
        f"    {url!r},",
        *(["    data=json.dumps(corps).encode(),"] if body is not None else []),
        f"    headers={headers!r},",
        f"    method={method!r},",
        ")",
        "with urllib.request.urlopen(requete) as reponse:",
        "    print(reponse.status, json.load(reponse))",
    ]
    js = [
        f"const reponse = await fetch({json.dumps(url)}, {{",
        f"  method: {json.dumps(method)},",
        f"  headers: {json.dumps(headers, ensure_ascii=False)},",
        *([f"  body: JSON.stringify({payload})," if payload is not None else ""]),
        "});",
        "console.log(reponse.status, await reponse.json());",
    ]
    return {
        "curl": " \\\n".join(curl),
        "python": "\n".join(py),
        "javascript": "\n".join(ligne for ligne in js if ligne),
    }


def _base_path(product: Product) -> str:
    return f"/api/v1/plugins/apistore/v1/{product.workspace.slug}/{product.slug}"


def build(product: Product) -> dict:
    """Document complet d'un produit. `product` doit avoir ses `links__workflow` et `plans` préchargés."""
    base = _base_path(product)
    plans = list(product.plans.all())
    envs = [e for e in ENV_ORDER if any(e in pl.environments for pl in plans)]
    default_env = envs[0] if envs else "test"
    endpoints, unavailable = [], []
    for link in sorted(product.links.all(), key=lambda lk: lk.workflow.slug):
        wf = link.workflow
        version = wf.latest_version()
        if version is None:
            unavailable.append(wf.slug)
            continue
        schema = version.input_schema if isinstance(version.input_schema, dict) else {}
        body = {"environment": default_env, "inputs": example_inputs(schema)}
        path = f"{base}/workflows/{wf.slug}/runs/"
        outs = output_fields(version.graph)
        endpoints.append(
            {
                "id": f"run-{wf.slug}",
                "kind": "invoke",
                "title": wf.name,
                "workflow": wf.slug,
                "version": version.version,
                "method": "POST",
                "path": path,
                "description": (
                    "Lance le workflow (asynchrone) : la réponse donne un `run_id` à suivre avec l'endpoint de suivi. "
                    "Rejouer la même `Idempotency-Key` renvoie la même exécution (200) sans consommer de quota."
                ),
                "headers": [
                    {
                        "name": "Authorization",
                        "required": True,
                        "description": f"`Bearer {KEY_PLACEHOLDER}` : la clé de votre souscription.",
                    },
                    {"name": "Content-Type", "required": True, "description": "`application/json`."},
                    {
                        "name": "Idempotency-Key",
                        "required": False,
                        "description": "Identifiant unique de la demande, pour la rejouer sans doublon.",
                    },
                ],
                "environments": envs,
                "parameters": parameters(schema),
                "input_schema": schema,
                "request_example": body,
                "responses": [
                    {
                        "status": 202,
                        "description": "Exécution créée.",
                        "example": {"run_id": "<uuid>", "status": "running", "replayed": False},
                    },
                    {
                        "status": 200,
                        "description": "Rejeu d'une `Idempotency-Key` déjà vue.",
                        "example": {"run_id": "<uuid>", "status": "running", "replayed": True},
                    },
                ],
                "outputs": outs,
                "examples": snippets("POST", HOST_PLACEHOLDER + path, body, idempotent=True),
            }
        )
    status_path = f"{base}/runs/<run_id>/"
    sample_outputs = {name: "<valeur>" for name in sorted({o for e in endpoints for o in e["outputs"]})}
    endpoints.append(
        {
            "id": "run-status",
            "kind": "status",
            "title": "Suivre une exécution",
            "workflow": None,
            "version": None,
            "method": "GET",
            "path": status_path,
            "description": (
                "État d'une exécution lancée avec votre clé (les autres sont introuvables). Interrogez-la jusqu'à `finished: true`. "
                "`progress` dit où en est le workflow ; `outputs` n'est renseigné qu'à la réussite (leur type n'est pas décrit par la plateforme)."
            ),
            "headers": [{"name": "Authorization", "required": True, "description": f"`Bearer {KEY_PLACEHOLDER}`."}],
            "environments": [],
            "parameters": [
                {
                    "name": "run_id",
                    "type": "uuid (dans le chemin)",
                    "required": True,
                    "default": None,
                    "has_default": False,
                    "enum": None,
                    "description": "Identifiant reçu au lancement.",
                    "constraints": [],
                }
            ],
            "input_schema": None,
            "request_example": None,
            "response_fields": RESPONSE_FIELDS,
            "responses": [
                {
                    "status": 200,
                    "description": "État courant.",
                    "example": {
                        "run_id": "<uuid>",
                        "status": "running",
                        "outputs": None,
                        "finished": False,
                        "progress": {
                            "steps_done": 2,
                            "steps_total": 5,
                            "current_step": {"name": "Validation", "status": "waiting", "status_label": "En attente"},
                            "also_active": 0,
                            "workflow_status": "running",
                            "workflow_status_label": "En cours",
                        },
                    },
                },
                {
                    "status": 200,
                    "description": "Exécution réussie : `outputs` renseigné, `current_step` à `null`.",
                    "example": {
                        "run_id": "<uuid>",
                        "status": "succeeded",
                        "outputs": sample_outputs,
                        "finished": True,
                        "progress": {
                            "steps_done": 5,
                            "steps_total": 5,
                            "current_step": None,
                            "also_active": 0,
                            "workflow_status": "succeeded",
                            "workflow_status_label": "Réussie",
                        },
                    },
                },
                {
                    "status": 404,
                    "description": "Exécution inconnue ou lancée par une autre souscription.",
                    "example": {"detail": "Exécution introuvable.", "code": "not_found"},
                },
            ],
            "outputs": [],
            "examples": snippets("GET", HOST_PLACEHOLDER + status_path, None, idempotent=False),
        }
    )
    return {
        "product": {
            "slug": product.slug,
            "name": product.name,
            "description": product.description,
            "status": product.status,
        },
        "base_path": base,
        "auth": {
            "scheme": "Bearer",
            "header": f"Authorization: Bearer {KEY_PLACEHOLDER}",
            "text": "Toute requête porte la clé de votre souscription. Elle n'est montrée qu'une fois à son émission ; gardez-la secrète.",
        },
        "environments": envs,
        "plans": [
            {
                "slug": pl.slug,
                "name": pl.name,
                "per_minute": pl.per_minute,
                "per_day": pl.per_day,
                "environments": pl.environments,
                "approval": pl.approval,
            }
            for pl in plans
        ],
        "endpoints": endpoints,
        "unavailable": unavailable,
        "run_statuses": [{"status": s, "meaning": m} for s, m in RUN_STATUSES],
        "errors": [{"status": s, "code": c, "meaning": m} for s, c, m in ERRORS],
        "error_format": {"detail": "Message lisible", "code": "invalid"},
        "mcp": {
            "path": f"{base}/mcp/",
            "text": "Les mêmes workflows sont aussi exposés comme outils au protocole MCP (JSON-RPC), avec la même clé, le même plan et les mêmes quotas.",
        },
    }


def _responses(items: list) -> dict:
    """Réponses OpenAPI par code HTTP ; deux cas pour un même code deviennent deux `examples` nommés (jamais l'un écrase l'autre)."""
    grouped: dict[str, list] = {}
    for r in items:
        grouped.setdefault(str(r["status"]), []).append(r)
    out = {}
    for code, group in grouped.items():
        if len(group) == 1:
            content = {"example": group[0]["example"]}
        else:
            content = {
                "examples": {
                    f"cas{i}": {"summary": g["description"], "value": g["example"]} for i, g in enumerate(group, 1)
                }
            }
        out[code] = {"description": group[0]["description"], "content": {"application/json": content}}
    return out


def openapi(doc: dict) -> dict:
    """Description OpenAPI 3.1 du produit, importable dans un outil client. Dérivée du même document que la page."""
    paths: dict = {}
    for ep in doc["endpoints"]:
        # Les chemins du modèle d'URL OpenAPI s'écrivent {run_id}, pas <run_id>.
        path = ep["path"].replace("<run_id>", "{run_id}")
        if ep["kind"] == "invoke":
            paths[path] = {
                "post": {
                    "operationId": ep["id"],
                    "summary": ep["title"],
                    "description": ep["description"],
                    "security": [{"cle": []}],
                    "parameters": [
                        {
                            "name": "Idempotency-Key",
                            "in": "header",
                            "required": False,
                            "schema": {"type": "string", "maxLength": 100},
                        }
                    ],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["environment"],
                                    "properties": {
                                        "environment": {"type": "string", "enum": ep["environments"] or ENV_ORDER},
                                        "inputs": ep["input_schema"],
                                    },
                                },
                                "example": ep["request_example"],
                            }
                        },
                    },
                    "responses": _responses(ep["responses"])
                    | {
                        str(e["status"]): {"description": e["meaning"]}
                        for e in doc["errors"]
                        if e["status"] not in (200, 202)
                    },
                }
            }
        else:
            paths[path] = {
                "get": {
                    "operationId": ep["id"],
                    "summary": ep["title"],
                    "description": ep["description"],
                    "security": [{"cle": []}],
                    "parameters": [
                        {
                            "name": "run_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string", "format": "uuid"},
                        }
                    ],
                    "responses": _responses(ep["responses"]),
                }
            }
    return {
        "openapi": "3.1.0",
        "info": {"title": doc["product"]["name"], "description": doc["product"]["description"], "version": "1"},
        "servers": [{"url": OPENAPI_SERVER}],
        "components": {
            "securitySchemes": {
                "cle": {"type": "http", "scheme": "bearer", "description": "Clé de souscription (fxs_…)."}
            }
        },
        "paths": paths,
    }
