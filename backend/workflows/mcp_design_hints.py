"""Aide à la décision « action réutilisable ou script ? » du MCP de conception (ADR 0025).

Ce ne sont PAS des erreurs : des signaux que l'assistant peut suivre ou écarter, et dont la personne qui relit voit le raisonnement.
- `design_hints(graph)` : scripts dupliqués, scripts longs, très grand workflow ;
- `action_usage(...)` : combien de workflows et d'étapes utilisent déjà une action (est-elle vraiment réutilisée ?)."""

import re
from collections import defaultdict

DUPLICATE_AT = 3  # un même script répété à partir de 3 fois mérite une action
LONG_SCRIPT_LINES = 40
BIG_GRAPH = 100
USAGE_SCAN_LIMIT = 1000  # workflows examinés au plus (borne le coût d'une lecture)


# Appels réseau reconnaissables dans du code : un script ou une action n'a AUCUN réseau, l'étape native (`http`, `sql`…) le fait pour lui.
NETWORK_CALL = re.compile(
    r"\b(requests\.|urllib|http\.client|httpx|aiohttp|socket\.|smtplib|psycopg|pymysql|ldap3|paramiko|fetch\s*\(|axios|XMLHttpRequest|curl\s|wget\s)"
)
NETWORK_MESSAGE = (
    "Ce code semble appeler le réseau, or un script ou une action n'a aucun accès réseau : il échouera. "
    "Utilisez une étape native avec une connexion (`http` pour une API, `sql`, `ldap`, `sftp`, `smtp`) et traitez sa sortie dans le code."
)


def uses_network(code: str) -> bool:
    return bool(NETWORK_CALL.search(code or ""))


def _norm(code: str) -> str:
    return re.sub(r"\s+", " ", code or "").strip()


def design_hints(graph: dict) -> list[dict]:
    nodes = [n for n in (graph or {}).get("nodes", []) if isinstance(n, dict)]
    hints: list[dict] = []
    groups: dict[tuple, list[str]] = defaultdict(list)
    for n in nodes:
        if n.get("type") == "script" and _norm(n.get("script", "")):
            groups[(n.get("language"), _norm(n["script"]))].append(n["id"])
    for ids in groups.values():
        if len(ids) >= DUPLICATE_AT:
            hints.append(
                {
                    "code": "duplicate_script",
                    "nodes": ids,
                    "message": f"Le même script apparaît {len(ids)} fois : une action réutilisable (contrat, version, test) évite la duplication.",
                }
            )
    for n in nodes:
        if n.get("type") == "script" and uses_network(str(n.get("script", ""))):
            hints.append({"code": "network_in_script", "nodes": [n["id"]], "message": NETWORK_MESSAGE})
    for n in nodes:
        if n.get("type") == "script" and len(str(n.get("script", "")).splitlines()) >= LONG_SCRIPT_LINES:
            hints.append(
                {
                    "code": "long_script",
                    "nodes": [n["id"]],
                    "message": f"Script de plus de {LONG_SCRIPT_LINES} lignes : une action se versionne, se teste et se relit mieux qu'un script en ligne.",
                }
            )
    if len(nodes) >= BIG_GRAPH:
        hints.append(
            {
                "code": "big_workflow",
                "nodes": [],
                "message": f"Grand workflow ({len(nodes)} étapes) : des sous-parties autonomes seraient plus lisibles en sous-workflows, qu'une personne devra publier avant que celui-ci les appelle.",
            }
        )
    return hints


def action_usage(workflows) -> dict[str, dict]:
    """{slug d'action: {workflows: n, steps: m}} sur les brouillons ET dernières versions publiées des workflows donnés (au plus USAGE_SCAN_LIMIT)."""
    wf_sets: dict[str, set] = defaultdict(set)
    steps: dict[str, int] = defaultdict(int)
    for wf in list(workflows)[:USAGE_SCAN_LIMIT]:
        latest = wf.latest_version()

        def used(graph) -> list[str]:
            return [
                n["action"]
                for n in (graph or {}).get("nodes", [])
                if isinstance(n, dict) and n.get("type") == "action" and n.get("action")
            ]

        draft, published = used(wf.draft_graph), used(latest.graph) if latest is not None else []
        for slug in (
            draft or published
        ):  # les étapes du graphe COURANT : le brouillon s'il en a un, sinon la dernière version publiée
            steps[slug] += 1
        seen_in_wf = set(draft) | set(published)
        for slug in seen_in_wf:
            wf_sets[slug].add(wf.pk)
    return {slug: {"workflows": len(ids), "steps": steps.get(slug, 0)} for slug, ids in wf_sets.items()}
