"""Server-side workflow graph validation (the client mirrors a subset; this is authoritative).

Returns a list of issues {node, level, code, message}. Publishing requires zero 'error' issues.
"""

import ast
import copy
import itertools
import keyword
import re
from collections import defaultdict, deque

import jsonschema

from catalog.models import Action
from vault.models import Connection

from . import debug, transforms
from .expressions import WHOLE_RE, ExpressionError, coerce_structured, parse, paths_in, template_expressions

MAX_NODES = 200
MAX_SCRIPT = 100 * 1024
SCRIPT_LANGUAGES = {"python", "javascript"}
RESERVED = {"System", "inputs", "vars", "steps"}
JS_RESERVED = {
    "break",
    "case",
    "catch",
    "class",
    "const",
    "continue",
    "debugger",
    "default",
    "delete",
    "do",
    "else",
    "export",
    "extends",
    "finally",
    "for",
    "function",
    "if",
    "import",
    "in",
    "instanceof",
    "new",
    "return",
    "super",
    "switch",
    "this",
    "throw",
    "try",
    "typeof",
    "var",
    "void",
    "while",
    "with",
    "yield",
    "let",
    "static",
    "await",
    "async",
    "null",
    "true",
    "false",
}
VAR_TYPES = {"string", "integer", "number", "boolean", "array", "object"}
IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,40}$")
MAX_FOREACH = 100
ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
NODE_TYPES = {
    "action",
    "condition",
    "switch",
    "join",
    "race",
    "wait",
    "approval",
    "subworkflow",
    "foreach",
    "ai",
    "script",
    "throw",
    "http",
    "sql",
    "agent",
    "while",
    "ldap",
    "sftp",
    "smtp",
    "transform",
}
HTTP_CONNECTION_KINDS = ("http_bearer", "api_key", "basic", "oauth2_client", "oauth2_code", "aws_sigv4")
HTTP_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
STATIC_OUTPUTS = {
    "condition": {"result": {"type": "boolean"}},
    "switch": {"result": {"type": "string"}},
    "approval": {
        "approved": {"type": "boolean"},
        "decided_by": {"type": "string"},
        "comment": {"type": "string"},
    },
    "foreach": {"results": {"type": "array"}, "count": {"type": "integer"}},
    "wait": {},
    "join": {},
    "race": {"winner": {"type": "string"}, "winner_name": {"type": "string"}, "value": {}},
    "smtp": {
        "sent": {"type": "boolean"},
        "simulated": {"type": "boolean"},
        "recipients": {"type": "integer"},
        "message_id": {"type": "string"},
    },
    "sftp": {
        "operation": {"type": "string"},
        "path": {"type": "string"},
        "content": {},
        "file": {"type": "object"},
        "entries": {"type": "array"},
        "count": {"type": "integer"},
        "simulated": {"type": "boolean"},
    },
    "ldap": {
        "rows": {"type": "array"},
        "count": {"type": "integer"},
        "truncated": {"type": "boolean"},
        "simulated": {"type": "boolean"},
    },
    "while": {
        "iterations": {"type": "integer"},
        "results": {"type": "array"},
        "last": {"type": "object"},
        "stopped": {"type": "string"},
    },
    "agent": {
        "answer": {"type": "string"},
        "iterations": {"type": "integer"},
        "steps": {"type": "array"},
        "stopped": {"type": "string"},
        "simulated": {"type": "boolean"},
    },
    "sql": {
        "rows": {"type": "array"},
        "count": {"type": "integer"},
        "columns": {"type": "array"},
        "truncated": {"type": "boolean"},
        "simulated": {"type": "boolean"},
    },
    "http": {
        "status": {"type": "integer"},
        "ok": {"type": "boolean"},
        "body": {},
        "headers": {"type": "object"},
        "file": {"type": "object"},
        "simulated": {"type": "boolean"},
    },
}
WEBHOOK_WAIT_OUTPUTS = {"resumed": {"type": "boolean"}, "payload": {"type": "object"}}
ERROR_CAPABLE = {
    "action",
    "subworkflow",
    "foreach",
    "approval",
    "ai",
    "script",
    "http",
    "sql",
    "agent",
    "while",
    "ldap",
    "sftp",
    "smtp",
}


def explain_validation_error(exc) -> str:
    """Human message for a jsonschema error that never echoes the offending value (it may be large or sensitive)."""
    where = ".".join(str(p) for p in exc.absolute_path)
    prefix = f"champ « {where} » : " if where else ""
    v, arg = exc.validator, exc.validator_value
    templates = {
        "maxLength": lambda: f"trop long (maximum {arg} caractères)",
        "minLength": lambda: f"trop court (minimum {arg} caractères)",
        "maximum": lambda: f"trop grand (maximum {arg})",
        "minimum": lambda: f"trop petit (minimum {arg})",
        "maxItems": lambda: f"trop d'éléments (maximum {arg})",
        "minItems": lambda: f"pas assez d'éléments (minimum {arg})",
        "type": lambda: f"type attendu : {arg if isinstance(arg, str) else ' ou '.join(arg)}",
        "enum": lambda: "valeur non autorisée",
        "required": lambda: "champ obligatoire manquant",
        "pattern": lambda: "format invalide",
        "additionalProperties": lambda: "champ non prévu",
    }
    text = templates.get(v, lambda: "valeur non conforme au contrat")()
    return prefix + text


def issue(node, code, message, level="error", **extra):
    return {"node": node, "level": level, "code": code, "message": message, **extra}


def _compatible(source: str | list | None, target: str | list | None) -> bool:
    if not source or not target:
        return True
    src = set([source] if isinstance(source, str) else source)
    tgt = set([target] if isinstance(target, str) else target)
    if "integer" in src:
        src.add("number")
    return bool(src & tgt)


def topo_order(nodes, edges):
    """Kahn order or None if cyclic."""
    indeg = {n["id"]: 0 for n in nodes}
    out = defaultdict(list)
    for e in edges:
        out[e["from"]].append(e["to"])
        indeg[e["to"]] += 1
    queue = deque(sorted(i for i, d in indeg.items() if d == 0))
    order = []
    while queue:
        cur = queue.popleft()
        order.append(cur)
        for nxt in out[cur]:
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                queue.append(nxt)
    return order if len(order) == len(nodes) else None


def ancestors_map(nodes, edges, order):
    parents = defaultdict(set)
    for e in edges:
        parents[e["to"]].add(e["from"])
    anc = {}
    for nid in order:
        s = set()
        for p in parents[nid]:
            s |= {p} | anc[p]
        anc[nid] = s
    return anc


MAX_SCENARIO_VARS = 12
MAX_CASES = 20
RESERVED_LABELS = {"default", "next", "error", "true", "false"}


def case_ids(node) -> list[str]:
    """The ids of a multi-way choice's cases (the labels of its outgoing connections), in order."""
    return [c["id"] for c in node.get("cases") or [] if isinstance(c, dict) and isinstance(c.get("id"), str)]


def _dominators(edges, order):
    parents = defaultdict(set)
    for e in edges:
        parents[e["to"]].add(e["from"])
    dom = {}
    for nid in order:
        ps = [dom[p] for p in parents[nid]]
        dom[nid] = {nid} | (set.intersection(*ps) if ps else set())
    return dom


def always_run_map(nodes, edges, order):
    """For each step N, the steps whose output certainly exists whenever N runs and the run is not ending in an
    exception. The graph is walked with the engine's rules for every combination of the open choices (each distinct
    decision opens one branch; a step with an error exit may fail): a fork of parallel branches runs ALL of them, a
    branch that ends in an exception is a run that fails anyway, a recovery branch that rejoins is not certain. A step on
    only one branch of a decision is NOT in the set of what follows the join: reading it there is the hazard."""
    kind = {n["id"]: n["type"] for n in nodes}
    expr = {n["id"]: re.sub(r"\s+", " ", str(n.get("expr", "")).strip()) for n in nodes if n["type"] == "condition"}
    incoming, outgoing = defaultdict(list), defaultdict(list)
    for e in edges:
        incoming[e["to"]].append(e)
        outgoing[e["from"]].append(e)
    options: dict = {("d", x): [True, False] for x in expr.values()}
    options |= {("f", n): [False, True] for n in kind if any(e.get("label") == "error" for e in outgoing[n])}
    for n in nodes:
        if n["type"] == "switch":
            options[("s", n["id"])] = [*case_ids(n), "default"]
        if n["type"] == "race" and incoming[n["id"]]:  # which branch arrives first
            options[("r", n["id"])] = [e["from"] for e in incoming[n["id"]]]
    up: dict[str, set] = {}
    for nid in order:
        up[nid] = set().union(*[{e["from"]} | up[e["from"]] for e in incoming[nid]]) if incoming[nid] else set()
    races = [n for n in kind if kind[n] == "race"]
    choices = sorted(options, key=str)
    total = 1
    for c in choices:
        total *= len(options[c])
    if total > 1 << MAX_SCENARIO_VARS:  # too many open choices to enumerate: the cruder, safe rule
        return _dominators(edges, order)
    must: dict = {nid: None for nid in order}  # None: never runs without ending in an exception
    for values in itertools.product(*(options[c] for c in choices)):
        pick = dict(zip(choices, values, strict=True))
        status: dict[str, str] = {}
        ends_badly = False
        for nid in order:
            runs = not incoming[nid]
            for e in incoming[nid]:
                src, label = status.get(e["from"]), e.get("label") or "next"
                if kind[nid] == "race" and e["from"] != pick.get(("r", nid)):
                    continue  # a race starts with the branch that arrives first, whatever the others do
                if src == "ok":
                    if kind[e["from"]] == "condition":
                        ok = label == str(pick[("d", expr[e["from"]])]).lower()
                    elif kind[e["from"]] == "switch":
                        ok = label == pick[("s", e["from"])]
                    else:
                        ok = label == "next"
                else:
                    ok = src == "failed" and label == "error"
                if ok:
                    runs = True
                    break
            if not runs:
                continue
            if kind[nid] == "throw":
                ends_badly = True
                status[nid] = "failed"
            else:
                status[nid] = "failed" if pick.get(("f", nid)) else "ok"
        if ends_badly:
            continue
        done = {n for n, st in status.items() if st == "ok"}
        for nid in status:
            certain = done
            for (
                r
            ) in races:  # after a race, what the slower branches produce is not certain (cancelled or still running)
                if status.get(r) == "ok" and (r == nid or r in up[nid]):
                    win = pick[("r", r)]
                    certain = certain - (up[r] - up[win] - {win})
            must[nid] = certain if must[nid] is None else must[nid] & certain
    everything = set(order)
    return {nid: ({nid} | must[nid]) if must[nid] is not None else everything for nid in order}


def resolve_action_version(workspace, slug, wanted=None):
    action = Action.objects.filter(workspace=workspace, slug=slug).first()
    if action is None:
        return None, None
    qs = action.versions.all()
    version = qs.filter(version=wanted).first() if wanted else action.latest_version()
    return action, version


def resolve_skill_version(workspace, slug, wanted=None):
    from ai.models import AiSkill

    skill = AiSkill.objects.filter(workspace=workspace, slug=slug).first()
    if skill is None:
        return None, None
    version = skill.versions.filter(version=wanted).first() if wanted else skill.latest_version()
    return skill, version


def resolve_workflow_version(workspace, slug, wanted=None):
    from .models import Workflow

    wf = Workflow.objects.filter(workspace=workspace, slug=slug).first()
    if wf is None:
        return None, None
    version = wf.versions.filter(version=wanted).first() if wanted else wf.latest_version()
    return wf, version


def validate_graph(graph: dict, workspace, current_workflow_slug: str | None = None) -> list[dict]:  # noqa: C901
    issues: list[dict] = []
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    inputs_schema = graph.get("inputs") or {}
    declared_inputs = inputs_schema.get("properties") or {}
    variables = graph.get("variables") or []
    declared_vars = {v["name"] for v in variables if isinstance(v, dict) and isinstance(v.get("name"), str)}

    if not isinstance(nodes, list) or not isinstance(edges, list):
        return [issue(None, "graph_malformed", "Le graphe est mal formé.")]
    if len(nodes) > MAX_NODES:
        return [issue(None, "too_many_nodes", f"Un workflow est limité à {MAX_NODES} étapes.")]

    ids = [n.get("id") for n in nodes if isinstance(n, dict)]
    if len(ids) != len(nodes):
        return [issue(None, "graph_malformed", "Une étape est mal formée.")]
    seen = set()
    for n in nodes:
        nid = n.get("id")
        if not isinstance(nid, str) or not ID_RE.match(nid):
            issues.append(issue(nid, "bad_id", "Identifiant d'étape invalide (minuscules, chiffres, _)."))
        elif nid in seen:
            issues.append(issue(nid, "duplicate_id", "Identifiant d'étape en double."))
        seen.add(nid)
        if n.get("type") not in NODE_TYPES:
            issues.append(issue(nid, "bad_type", f"Type d'étape inconnu : {n.get('type')}."))
        if not str(n.get("name", "")).strip():
            issues.append(issue(nid, "missing_name", "Donnez un nom à cette étape.", "warning"))
    if any(i["level"] == "error" for i in issues):
        return issues

    issues += _validate_variables(variables)
    issues += _validate_form(graph.get("form"), declared_inputs)
    issues += _validate_input_form(graph.get("input_form"), graph.get("inputs"), workspace)
    issues += [issue(None, "bad_pins", m) for m in debug.validate_pins(graph.get("pins"), set(ids))]
    by_id = {n["id"]: n for n in nodes}
    clean_edges = []
    for e in edges:
        if e.get("from") not in by_id or e.get("to") not in by_id:
            issues.append(issue(None, "dangling_edge", "Une connexion référence une étape absente."))
        elif e["from"] == e["to"]:
            issues.append(issue(e["from"], "cycle", "Une étape ne peut pas se connecter à elle-même."))
        else:
            clean_edges.append(e)
    if any(i["level"] == "error" for i in issues):
        return issues

    order = topo_order(nodes, clean_edges)
    if order is None:
        issues.append(
            issue(
                None, "cycle", "Le workflow contient un cycle. Utilisez une boucle explicite (étape « Pour chaque »)."
            )
        )
        return issues
    anc = ancestors_map(nodes, clean_edges, order)
    dom = always_run_map(nodes, clean_edges, order)
    incoming = defaultdict(list)
    outgoing = defaultdict(list)
    for e in clean_edges:
        incoming[e["to"]].append(e)
        outgoing[e["from"]].append(e)

    if nodes and not any(not incoming[n["id"]] for n in nodes):
        issues.append(issue(None, "no_entry", "Aucune étape de départ."))
    starts = [n for n in nodes if not incoming[n["id"]]]
    for n in starts[1:]:
        issues.append(
            issue(
                n["id"],
                "extra_start",
                "Cette étape n'a aucune entrée : elle démarre dès le lancement, en parallèle des autres. Si elle dépend "
                "d'une condition (« si… sinon… »), reliez-la à la décision.",
                "warning",
            )
        )

    out_types: dict[str, dict | None] = {}  # node id -> {field: schema} or None when unknown

    for n in nodes:
        nid, ntype = n["id"], n["type"]
        if len(incoming[nid]) > 1 and ntype not in ("join", "race"):
            issues.append(issue(nid, "implicit_join", "Plusieurs flux arrivent ici : ajoutez une étape « Jonction »."))
        if ntype == "join" and len(incoming[nid]) < 2:
            issues.append(issue(nid, "weak_join", "Une jonction attend au moins deux flux entrants.", "warning"))
        if ntype == "race":
            if len(incoming[nid]) < 2:
                issues.append(
                    issue(nid, "weak_race", "Un « premier arrivé » attend au moins deux branches.", "warning")
                )
            if n.get("losers", "cancel") not in ("cancel", "finish"):
                issues.append(
                    issue(nid, "bad_race", "Branches plus lentes : « cancel » (annulées) ou « finish » (terminées).")
                )
        labels = [e.get("label") or "next" for e in outgoing[nid]]
        if ntype == "condition":
            if not set(labels) <= {"true", "false"} or len(set(labels)) != len(labels) or not labels:
                issues.append(
                    issue(nid, "bad_branches", "Une condition a au plus une sortie « oui » et une sortie « non ».")
                )
        elif ntype == "switch":
            allowed = {*case_ids(n), "default"}
            if not set(labels) <= allowed or len(set(labels)) != len(labels) or not labels:
                issues.append(
                    issue(
                        nid,
                        "bad_branches",
                        "Un choix multiple a au plus une sortie par cas (et une sortie « default »), chacune étiquetée "
                        "par l'identifiant du cas.",
                    )
                )
        else:
            for lab in set(labels):
                if lab == "error" and ntype not in ERROR_CAPABLE:
                    issues.append(issue(nid, "bad_branches", "Cette étape ne peut pas avoir de sortie d'erreur."))
                elif lab not in ("next", "error"):
                    issues.append(issue(nid, "bad_branches", f"Étiquette de connexion invalide : {lab}."))
            if labels.count("error") > 1:
                issues.append(issue(nid, "bad_branches", "Une seule sortie d'erreur est possible."))

        # ---- per-type configuration
        if ntype == "action":
            _validate_action(n, workspace, issues, out_types, declared_inputs)
        elif ntype == "condition":
            if not str(n.get("expr", "")).strip():
                issues.append(issue(nid, "missing_expr", "La condition est vide."))
            else:
                _check_expr(
                    n["expr"], nid, anc[nid], by_id, declared_inputs, out_types, issues, declared_vars, dom[nid]
                )
            out_types[nid] = STATIC_OUTPUTS["condition"]
        elif ntype == "switch":
            _validate_switch(n, issues)
            for c in n.get("cases") or []:
                if isinstance(c, dict) and str(c.get("expr", "")).strip():
                    _check_expr(
                        c["expr"], nid, anc[nid], by_id, declared_inputs, out_types, issues, declared_vars, dom[nid]
                    )
            out_types[nid] = STATIC_OUTPUTS["switch"]
        elif ntype == "wait":
            s = n.get("seconds")
            if not isinstance(s, int) or not 1 <= s <= 30 * 86400:
                issues.append(issue(nid, "bad_wait", "Délai d'attente : entre 1 seconde et 30 jours."))
            if n.get("resume") not in (None, "timer", "webhook", "form"):
                issues.append(
                    issue(
                        nid,
                        "bad_wait",
                        "Reprise : « timer » (à l'échéance), « webhook » (appel HTTP) ou « form » (formulaire).",
                    )
                )
            if n.get("resume") == "form":
                props = (n.get("form") or {}).get("properties") if isinstance(n.get("form"), dict) else None
                if not isinstance(props, dict) or not props or len(props) > 20:
                    issues.append(issue(nid, "bad_wait", "Formulaire de reprise : de 1 à 20 champs."))
            out_types[nid] = WEBHOOK_WAIT_OUTPUTS if n.get("resume") in ("webhook", "form") else {}
        elif ntype == "http":
            _validate_http(n, workspace, issues)
            for src in _node_expressions(n):
                _check_expr(src, nid, anc[nid], by_id, declared_inputs, out_types, issues, declared_vars, dom[nid])
            out_types[nid] = STATIC_OUTPUTS["http"]
        elif ntype == "smtp":
            _validate_smtp(n, workspace, issues)
            for src in _node_expressions(n):
                _check_expr(src, nid, anc[nid], by_id, declared_inputs, out_types, issues, declared_vars, dom[nid])
            out_types[nid] = STATIC_OUTPUTS["smtp"]
        elif ntype == "sftp":
            _validate_sftp(n, workspace, issues)
            for src in _node_expressions(n):
                _check_expr(src, nid, anc[nid], by_id, declared_inputs, out_types, issues, declared_vars, dom[nid])
            out_types[nid] = STATIC_OUTPUTS["sftp"]
        elif ntype == "ldap":
            _validate_ldap(n, workspace, issues)
            for src in _node_expressions(n):
                _check_expr(src, nid, anc[nid], by_id, declared_inputs, out_types, issues, declared_vars, dom[nid])
            out_types[nid] = STATIC_OUTPUTS["ldap"]
        elif ntype == "agent":
            _validate_agent(n, workspace, issues)
            for src in _node_expressions(n):
                _check_expr(src, nid, anc[nid], by_id, declared_inputs, out_types, issues, declared_vars, dom[nid])
            out_types[nid] = STATIC_OUTPUTS["agent"]
        elif ntype == "sql":
            _validate_sql(n, workspace, issues)
            for src in _node_expressions(n):
                _check_expr(src, nid, anc[nid], by_id, declared_inputs, out_types, issues, declared_vars, dom[nid])
            out_types[nid] = STATIC_OUTPUTS["sql"]
        elif ntype == "transform":
            _validate_transform(n, issues)
            plain, per = transforms.expressions(n)
            for src in plain:
                _check_expr(src, nid, anc[nid], by_id, declared_inputs, out_types, issues, declared_vars, dom[nid])
            for src in per:
                _check_expr(
                    src, nid, anc[nid], by_id, declared_inputs, out_types, issues, declared_vars, dom[nid], ("item",)
                )
            out_types[nid] = transforms.output_fields(n)
        elif ntype == "approval":
            if not str(n.get("title", "")).strip():
                issues.append(issue(nid, "missing_title", "Indiquez ce qui doit être approuvé."))
            out_types[nid] = STATIC_OUTPUTS["approval"]
        elif ntype == "script":
            out_types[nid] = _validate_script(n, issues)
            for src in template_expressions(n.get("bindings", {})):
                _check_expr(src, nid, anc[nid], by_id, declared_inputs, out_types, issues, declared_vars, dom[nid])
        elif ntype == "throw":
            out_types[nid] = {}
            for src in template_expressions(n.get("message", "")):
                _check_expr(src, nid, anc[nid], by_id, declared_inputs, out_types, issues, declared_vars, dom[nid])
        elif ntype == "ai":
            out_types[nid] = _validate_ai(n, workspace, issues, out_types, declared_inputs)
            for src in template_expressions([n.get("prompt", ""), n.get("inputs", {})]):
                _check_expr(src, nid, anc[nid], by_id, declared_inputs, out_types, issues, declared_vars, dom[nid])
        elif ntype == "join":
            out_types[nid] = {}
        elif ntype == "race":
            out_types[nid] = STATIC_OUTPUTS["race"]
        elif ntype == "while":
            _validate_subworkflow(n, workspace, current_workflow_slug, issues, out_types, declared_inputs)
            out_types[nid] = STATIC_OUTPUTS["while"]
            if not str(n.get("condition") or "").strip():
                issues.append(
                    issue(
                        nid,
                        "missing_expr",
                        "Indiquez la condition pour continuer à répéter (ex. last.statut != 'termine').",
                    )
                )
            delay = n.get("delay_seconds", 0)
            if not isinstance(delay, int) or not 0 <= delay <= 86400:
                issues.append(issue(nid, "bad_wait", "Délai entre deux répétitions : de 0 à 86400 secondes."))
            for src in [n.get("condition", ""), *template_expressions(n.get("inputs", {}))]:
                if str(src).strip():
                    _check_expr(
                        src,
                        nid,
                        anc[nid],
                        by_id,
                        declared_inputs,
                        out_types,
                        issues,
                        declared_vars,
                        dom[nid],
                        ("last", "iteration"),
                    )
        elif ntype in ("subworkflow", "foreach"):
            _validate_subworkflow(n, workspace, current_workflow_slug, issues, out_types, declared_inputs)
            out_types[nid] = STATIC_OUTPUTS["foreach"] if ntype == "foreach" else None
            if ntype == "foreach":
                items = n.get("items")
                if not isinstance(items, str) or not items.strip():
                    issues.append(issue(nid, "missing_items", "Indiquez la liste à parcourir."))
                else:
                    _check_expr(
                        items, nid, anc[nid], by_id, declared_inputs, out_types, issues, declared_vars, dom[nid]
                    )
        # template references in inputs of any node
        if ntype in ("action", "subworkflow", "foreach"):
            for src in template_expressions(n.get("inputs", {})):
                _check_expr(src, nid, anc[nid], by_id, declared_inputs, out_types, issues, declared_vars, dom[nid])

    for src in template_expressions(graph.get("outputs", {})):
        _check_expr(src, None, set(by_id), by_id, declared_inputs, out_types, issues, declared_vars)
    _check_failure_branches(nodes, clean_edges, by_id, issues)
    seen: set = set()  # the same step read twice on one line gives the same finding twice
    return [i for i in issues if (k := (i["node"], i["code"], i["message"])) not in seen and not seen.add(k)]


def _validate_http(n: dict, workspace, issues: list) -> None:
    nid = n["id"]
    if n.get("integration"):
        from catalog import integrations

        _, op = integrations.find(str(n["integration"]), str(n.get("operation", "")))
        if op is None:
            issues.append(issue(nid, "bad_http", "Intégration ou opération inconnue."))
        else:
            given = n.get("params") if isinstance(n.get("params"), dict) else {}
            for prm in op["params"]:
                if prm.get("required") and given.get(prm["name"]) in (None, ""):
                    issues.append(issue(nid, "missing_param", f"Renseignez « {prm['label']} »."))
    else:
        if str(n.get("method") or "GET").upper() not in HTTP_METHODS:
            issues.append(issue(nid, "bad_http", "Méthode HTTP : GET, POST, PUT, PATCH ou DELETE."))
        path = n.get("path", "/")
        if not isinstance(path, str) or not path.startswith(("/", "{{")):
            issues.append(
                issue(
                    nid,
                    "bad_http",
                    "Le chemin de la requête doit commencer par « / » (l'adresse vient de la connexion).",
                )
            )
    cname = n.get("connection")
    if not isinstance(cname, str) or not cname:
        issues.append(
            issue(nid, "missing_connection", "Choisissez la connexion (type « Service HTTP ») utilisée par la requête.")
        )
    elif not Connection.objects.filter(workspace=workspace, name=cname, kind__in=HTTP_CONNECTION_KINDS).exists():
        issues.append(issue(nid, "unknown_connection", f"Connexion « Service HTTP » introuvable : {cname}."))
    if n.get("body_format") not in (None, "json", "form", "multipart", "file"):
        issues.append(issue(nid, "bad_http", "Format du corps : json, form, multipart ou file."))
    if n.get("body_format") == "file" and not n.get("file"):
        issues.append(
            issue(nid, "bad_http", "Corps « fichier » : indiquez le fichier à envoyer (sortie « file » d'une étape).")
        )
    if n.get("response_file") and n.get("pagination"):
        issues.append(issue(nid, "bad_http", "Une réponse enregistrée comme fichier ne peut pas être paginée."))
    pg = n.get("pagination")
    if pg is not None and (not isinstance(pg, dict) or pg.get("type") not in ("page", "offset", "cursor", "link")):
        issues.append(issue(nid, "bad_http", "Pagination : type « page », « offset », « cursor » ou « link »."))
    elif isinstance(pg, dict) and pg.get("type") == "cursor" and not str(pg.get("next_path", "")).strip():
        issues.append(
            issue(nid, "bad_http", "Pagination par curseur : indiquez où lire le curseur suivant (next_path).")
        )
    t = n.get("timeout_seconds")
    if t is not None and (not isinstance(t, int) or not 1 <= t <= 60):
        issues.append(issue(nid, "bad_http", "Délai de la requête : entre 1 et 60 secondes."))
    if n.get("mode", "test") not in ("test", "reel"):
        issues.append(issue(nid, "bad_http", "Mode : « test » (simulation) ou « reel »."))
    if n.get("mode", "test") == "test":
        issues.append(
            issue(
                nid,
                "http_test_mode",
                "Mode test : aucune requête n'est envoyée (la sortie est simulée).",
                level="warning",
            )
        )


def _validate_smtp(n: dict, workspace, issues: list) -> None:
    nid = n["id"]
    for field, label in (("to", "un destinataire"), ("subject", "l'objet"), ("body", "le corps du message")):
        if not str(n.get(field) or "").strip():
            issues.append(issue(nid, "bad_smtp", f"Indiquez {label}."))
    cname = n.get("connection")
    if not isinstance(cname, str) or not cname:
        issues.append(issue(nid, "missing_connection", "Choisissez la connexion (type « Serveur SMTP »)."))
    elif not Connection.objects.filter(workspace=workspace, name=cname, kind="smtp").exists():
        issues.append(issue(nid, "unknown_connection", f"Connexion « Serveur SMTP » introuvable : {cname}."))
    if n.get("mode", "test") not in ("test", "reel"):
        issues.append(issue(nid, "bad_smtp", "Mode : « test » (simulation) ou « reel »."))
    if n.get("mode", "test") == "test":
        issues.append(
            issue(
                nid,
                "smtp_test_mode",
                "Mode test : aucun e-mail n'est envoyé (la sortie est simulée).",
                level="warning",
            )
        )


def _validate_sftp(n: dict, workspace, issues: list) -> None:
    nid = n["id"]
    if n.get("operation", "list") not in ("list", "read", "write", "delete"):
        issues.append(issue(nid, "bad_sftp", "Opération : list, read, write ou delete."))
    if not str(n.get("path") or "").strip():
        issues.append(issue(nid, "bad_sftp", "Indiquez le chemin du fichier ou du dossier."))
    if n.get("operation") == "write" and n.get("content") is None and not n.get("file"):
        issues.append(issue(nid, "bad_sftp", "Indiquez le contenu à écrire."))
    cname = n.get("connection")
    if not isinstance(cname, str) or not cname:
        issues.append(issue(nid, "missing_connection", "Choisissez la connexion (type « Serveur SFTP »)."))
    elif not Connection.objects.filter(workspace=workspace, name=cname, kind="sftp").exists():
        issues.append(issue(nid, "unknown_connection", f"Connexion « Serveur SFTP » introuvable : {cname}."))
    if n.get("mode", "test") not in ("test", "reel"):
        issues.append(issue(nid, "bad_sftp", "Mode : « test » (simulation) ou « reel »."))
    if n.get("mode", "test") == "test":
        issues.append(
            issue(
                nid,
                "sftp_test_mode",
                "Mode test : rien n'est envoyé au serveur (la sortie est simulée).",
                level="warning",
            )
        )


def _validate_ldap(n: dict, workspace, issues: list) -> None:
    nid = n["id"]
    f = str(n.get("filter") or "").strip()
    if not (f.startswith("(") and f.endswith(")")) or f.count("(") != f.count(")"):
        issues.append(
            issue(nid, "bad_ldap", "Filtre LDAP entre parenthèses requis, ex. (&(objectClass=person)(mail={mail})).")
        )
    cname = n.get("connection")
    if not isinstance(cname, str) or not cname:
        issues.append(issue(nid, "missing_connection", "Choisissez la connexion (type « Annuaire LDAP »)."))
    elif not Connection.objects.filter(workspace=workspace, name=cname, kind="ldap").exists():
        issues.append(issue(nid, "unknown_connection", f"Connexion « Annuaire LDAP » introuvable : {cname}."))
    if n.get("scope", "sub") not in ("sub", "one", "base"):
        issues.append(issue(nid, "bad_ldap", "Portée : sub, one ou base."))
    if n.get("mode", "test") not in ("test", "reel"):
        issues.append(issue(nid, "bad_ldap", "Mode : « test » (simulation) ou « reel »."))
    if n.get("mode", "test") == "test":
        issues.append(
            issue(
                nid,
                "ldap_test_mode",
                "Mode test : aucune recherche n'est envoyée (la sortie est vide).",
                level="warning",
            )
        )


def _validate_agent(n: dict, workspace, issues: list) -> None:
    nid = n["id"]
    if not str(n.get("goal") or "").strip():
        issues.append(issue(nid, "bad_agent", "Décrivez l'objectif de l'agent."))
    tools = n.get("tools")
    if not isinstance(tools, list) or not tools or len(tools) > 10:
        issues.append(issue(nid, "bad_agent", "Choisissez de 1 à 10 actions que l'agent peut utiliser."))
    else:
        known = set(
            Action.objects.filter(workspace=workspace, slug__in=[t for t in tools if isinstance(t, str)]).values_list(
                "slug", flat=True
            )
        )
        for t in tools:
            if t not in known:
                issues.append(issue(nid, "unknown_action", f"Outil de l'agent introuvable dans le catalogue : {t}."))
        for t in Action.objects.filter(workspace=workspace, slug__in=list(known), status="proposed").values_list(
            "name", flat=True
        ):
            issues.append(issue(nid, "proposed_action", f"L'outil « {t} » est une proposition d'assistant non relue."))
    steps = n.get("max_steps")
    if steps is not None and (not isinstance(steps, int) or not 1 <= steps <= 10):
        issues.append(issue(nid, "bad_agent", "Nombre de tours : de 1 à 10."))
    if n.get("approval", "effectful") not in ("never", "effectful", "always"):
        issues.append(issue(nid, "bad_agent", "Approbation : never, effectful ou always."))
    if n.get("mode", "test") not in ("test", "reel"):
        issues.append(issue(nid, "bad_agent", "Mode : « test » (outils simulés) ou « reel »."))
    if n.get("mode", "test") == "test":
        issues.append(
            issue(
                nid,
                "agent_test_mode",
                "Mode test : l'agent interroge le modèle mais ses outils sont simulés (rien n'est exécuté).",
                level="warning",
            )
        )
    elif n.get("approval") == "never":
        issues.append(
            issue(
                nid,
                "agent_no_approval",
                "Mode réel sans approbation : l'agent peut exécuter des outils à effet externe sans validation humaine.",
                level="warning",
            )
        )


def _validate_sql(n: dict, workspace, issues: list) -> None:
    nid = n["id"]
    q = str(n.get("statement") or "").strip().rstrip(";").strip()
    if not q:
        issues.append(issue(nid, "bad_sql", "Saisissez la requête SQL (SELECT)."))
    elif ";" in q:
        issues.append(issue(nid, "bad_sql", "Une seule instruction par étape (pas de « ; » au milieu)."))
    elif len(q) > 8000:
        issues.append(issue(nid, "bad_sql", "Requête trop longue (8000 caractères au plus)."))
    cname = n.get("connection")
    if not isinstance(cname, str) or not cname:
        issues.append(
            issue(
                nid, "missing_connection", "Choisissez la connexion (type « Base PostgreSQL » ou « MySQL / MariaDB »)."
            )
        )
    elif not Connection.objects.filter(workspace=workspace, name=cname, kind__in=("postgres", "mysql")).exists():
        issues.append(issue(nid, "unknown_connection", f"Connexion « Base PostgreSQL » introuvable : {cname}."))
    if n.get("mode", "test") not in ("test", "reel"):
        issues.append(issue(nid, "bad_sql", "Mode : « test » (simulation) ou « reel »."))
    if n.get("mode", "test") == "test":
        issues.append(
            issue(
                nid, "sql_test_mode", "Mode test : aucune requête n'est envoyée (la sortie est vide).", level="warning"
            )
        )


def _validate_transform(n: dict, issues: list) -> None:
    nid, op = n["id"], n.get("op")
    if op not in transforms.OPS:
        issues.append(
            issue(nid, "bad_transform", "Choisissez l'opération (définir, transformer, filtrer, trier, regrouper…).")
        )
        return
    need = {
        "set": ("fields",),
        "map": ("items", "fields"),
        "filter": ("items", "where"),
        "group": ("items", "by"),
        "merge": ("items", "right"),
        "sort": ("items",),
        "unique": ("items",),
        "limit": ("items",),
        "chunk": ("items", "size"),
        "split": ("text",),
    }[op]
    for key in need:
        v = n.get(key)
        if v in (None, "", {}) or (isinstance(v, str) and not v.strip()):
            issues.append(issue(nid, "bad_transform", f"Renseignez « {key} »."))
    if op == "merge" and n.get("merge_mode", "inner") in ("inner", "left"):
        for key in ("left_by", "right_by"):
            if not str(n.get(key) or "").strip():
                issues.append(issue(nid, "bad_transform", f"Renseignez « {key} » (clé de fusion)."))
    if op == "group":
        for a in n.get("aggregates") or []:
            if not isinstance(a, dict) or not a.get("name") or a.get("fn") not in transforms.AGGREGATES:
                issues.append(
                    issue(nid, "bad_transform", "Agrégat : nom et fonction (count, sum, min, max, avg, first, list).")
                )
    if op == "limit" and not isinstance(n.get("count"), int):
        issues.append(issue(nid, "bad_transform", "Indiquez le nombre d'éléments à garder (entier)."))


def _node_expressions(n) -> list[str]:
    """Every expression a step evaluates (decision, loop list, template values of its inputs, bindings and prompt)."""
    out = []
    if n["type"] == "condition":
        out.append(n.get("expr", ""))
    if n["type"] == "foreach":
        out.append(n.get("items", ""))
    if n["type"] == "switch":
        out += [c.get("expr", "") for c in n.get("cases") or [] if isinstance(c, dict)]
    out += template_expressions(n.get("inputs") or {})
    if n["type"] == "transform":
        plain, per = transforms.expressions(n)
        out += plain + per
    if n["type"] == "while":
        out.append(str(n.get("condition") or ""))
    if n["type"] == "smtp":
        out += template_expressions([n.get("to", ""), n.get("cc", ""), n.get("subject", ""), n.get("body", "")])
    if n["type"] == "sftp":
        out += template_expressions([n.get("path", ""), n.get("content", "")])
    if n["type"] == "ldap":
        out += template_expressions(n.get("params") or {})
    if n["type"] == "throw":
        out += template_expressions(n.get("message") or "")
    if n["type"] == "agent":
        out += template_expressions(n.get("goal") or "")
    if n["type"] == "sql":
        out += template_expressions(n.get("params") or {})
    if n["type"] == "http":
        for key in ("path", "query", "headers", "body", "params", "files"):
            out += template_expressions(n.get(key) or "")
    if n["type"] == "script":
        out += template_expressions(n.get("bindings") or {})
    if n["type"] == "ai":
        out += template_expressions(n.get("prompt") or "")
    return [e for e in out if isinstance(e, str)]


def failed_reads(nodes, edges) -> list[tuple[str, str]]:
    """(step, failed step) pairs: a step that only runs after another one FAILED, and reads that one's output."""
    out = defaultdict(list)
    for e in edges:
        out[e["from"]].append(e)

    def reach(starts):
        seen, todo = set(), list(starts)
        while todo:
            cur = todo.pop()
            if cur not in seen:
                seen.add(cur)
                todo += [e["to"] for e in out[cur]]
        return seen

    reads: dict[str, set] = {}
    for n in nodes:
        refs = set()
        for src in _node_expressions(n):
            try:
                refs |= {p[1] for p in paths_in(parse(src), strict=True) if p[0] == "steps" and len(p) > 1}
            except (
                ExpressionError
            ):  # unreadable as a whole (an invented filter…): the references still say what it reads
                refs |= set(re.findall(r"steps\.([a-z][a-z0-9_]*)\b", src))
        reads[n["id"]] = refs
    found = []
    for n in nodes:
        sid = n["id"]
        handlers = [e["to"] for e in out[sid] if e.get("label") == "error"]
        if not handlers:
            continue
        only_on_failure = reach(handlers) - reach([e["to"] for e in out[sid] if e.get("label") != "error"])
        found += [(nid, sid) for nid in sorted(only_on_failure) if sid in reads.get(nid, ())]
    return found


def _check_failure_branches(nodes, edges, by_id, issues):
    """What only runs when a step FAILED cannot read that step's output: a failed step produced none."""
    for nid, sid in failed_reads(nodes, edges):
        issues.append(
            issue(
                nid,
                "reads_failed_output",
                f"L'étape « {by_id[nid].get('name') or nid} » ne s'exécute que si « {by_id[sid].get('name') or sid} » échoue, "
                "mais elle lit sa sortie : une étape en échec n'a produit aucune sortie. Utilisez une valeur "
                "connue avant cette étape (entrées du workflow, étape précédente) ou un texte fixe.",
            )
        )


def _check_expr(
    src, nid, allowed_steps, by_id, declared_inputs, out_types, issues, declared_vars, must_run=None, extra_roots=()
):
    try:
        tree = parse(src)
    except ExpressionError as exc:
        hint = ""
        if re.search(r"[-+*/%]|Fonction inconnue", f"{src} {exc}"):
            hint = (
                " Les expressions n'acceptent que des calculs simples (+ - * / %) et les fonctions len, lower, upper, trim, str, number,"
                " round, abs, min, max, sum, join, split, replace, starts_with, ends_with, first, last, keys, unique, sort, default :"
                " calculez le reste dans une étape script (sortie booléenne ou numérique), puis comparez cette sortie ici."
            )
        issues.append(issue(nid, "bad_expression", f"Expression invalide « {src.strip()} » : {exc}{hint}"))
        return
    strict = set(paths_in(tree, strict=True))
    for path in paths_in(tree):
        root = path[0]
        if root == "inputs":
            if len(path) < 2 or path[1] not in declared_inputs:
                issues.append(issue(nid, "unknown_input", f"Entrée inconnue : {'.'.join(map(str, path))}."))
        elif root in extra_roots:
            pass
        elif root == "run":
            if len(path) != 2 or path[1] not in ("id", "started_at", "environment"):
                issues.append(issue(nid, "bad_reference", "run : id, started_at ou environment."))
        elif root == "vars":
            if len(path) < 2 or path[1] not in declared_vars:
                issues.append(issue(nid, "unknown_variable", f"Variable inconnue : {'.'.join(map(str, path))}."))
        elif root == "steps":
            if len(path) < 3 or path[2] != "output":
                issues.append(issue(nid, "bad_reference", "Référence attendue : steps.<étape>.output.<champ>."))
            elif path[1] not in by_id:
                issues.append(issue(nid, "unknown_step", f"Étape inconnue : {path[1]}."))
            elif path[1] not in allowed_steps:
                issues.append(
                    issue(
                        nid,
                        "not_upstream",
                        f"L'étape « {by_id[path[1]].get('name') or path[1]} » n'est pas en amont de celle-ci : "
                        "reliez-la avant cette étape.",
                    )
                )
            else:
                fields = out_types.get(path[1])
                if fields is not None and len(path) > 3 and path[3] not in fields:
                    issues.append(issue(nid, "unknown_output", f"« {path[1]} » ne produit pas de champ « {path[3]} »."))
                if must_run is not None and path[1] not in must_run and path in strict:
                    issues.append(
                        issue(
                            nid,
                            "maybe_skipped",
                            f"L'étape « {by_id[path[1]].get('name') or path[1]} » n'est pas sur tous les chemins qui "
                            "mènent ici : un chemin (branche d'une décision, branche parallèle ou sortie d'erreur) peut "
                            "arriver jusqu'ici sans être passé par elle, et sa sortie n'existerait alors pas. Si l'autre "
                            "chemin fait de toute façon échouer le workflow, c'est sans risque. Sinon : "
                            "{{ default(steps.<étape>.output.<champ>, 'valeur de repli') }} lit la valeur si l'étape a "
                            "tourné et prend le repli sinon ; ou lisez une valeur produite avant la séparation.",
                            "warning",
                            ref=path[1],
                        )
                    )
        else:
            issues.append(
                issue(nid, "bad_reference", f"Racine inconnue : {root} (utilisez inputs, vars, steps ou run).")
            )


def _validate_switch(n, issues):
    nid, cases = n["id"], n.get("cases")
    if not isinstance(cases, list) or not cases:
        issues.append(
            issue(nid, "missing_cases", "Un choix multiple a besoin d'au moins un cas (identifiant et condition).")
        )
        return
    if len(cases) > MAX_CASES:
        issues.append(issue(nid, "too_many_cases", f"Un choix multiple a au plus {MAX_CASES} cas."))
    seen = set()
    for c in cases:
        cid = c.get("id") if isinstance(c, dict) else None
        if not isinstance(cid, str) or not ID_RE.match(cid) or cid in RESERVED_LABELS:
            issues.append(
                issue(
                    nid,
                    "bad_case",
                    f"Identifiant de cas invalide : {cid!r} (minuscules, chiffres, _ ; pas « default »).",
                )
            )
        elif cid in seen:
            issues.append(issue(nid, "bad_case", f"Identifiant de cas en double : {cid}."))
        elif not str(c.get("expr", "")).strip():
            issues.append(issue(nid, "missing_expr", f"Le cas « {cid} » n'a pas de condition."))
        seen.add(cid)


def _validate_action(n, workspace, issues, out_types, declared_inputs):
    nid = n["id"]
    slug = n.get("action")
    action, version = resolve_action_version(workspace, slug, n.get("version")) if slug else (None, None)
    if action is None:
        issues.append(issue(nid, "unknown_action", f"Action introuvable dans cet espace : {slug}."))
        out_types[nid] = None
        return
    if version is None:
        issues.append(issue(nid, "version_unavailable", f"Version {n.get('version')} indisponible pour {slug}."))
        out_types[nid] = None
        return
    out_types[nid] = (version.output_schema or {}).get("properties", {})
    if action.status == "proposed":
        issues.append(
            issue(
                nid,
                "proposed_action",
                f"L'action « {action.name} » est une proposition d'assistant : relisez-la et activez-la avant de publier ou lancer.",
            )
        )
    if action.status == "deprecated":
        issues.append(
            issue(
                nid,
                "deprecated_action",
                f"L'action « {action.name} » est dépréciée : {action.deprecation_reason or 'prévoyez de la remplacer'}.",
                "warning",
            )
        )
    given_conns = n.get("connections") or {}
    for slot in version.secrets_required or []:
        cname = given_conns.get(slot["slot"])
        if not cname:
            issues.append(
                issue(
                    nid, "missing_connection", f"Choisissez la connexion « {slot['slot']} » requise par cette action."
                )
            )
        elif not Connection.objects.filter(workspace=workspace, name=cname).exists():
            issues.append(issue(nid, "unknown_connection", f"Connexion introuvable : {cname}."))
    for slot_name in given_conns:
        if slot_name not in {s["slot"] for s in version.secrets_required or []}:
            issues.append(issue(nid, "unknown_slot", f"Cette action n'utilise pas de connexion « {slot_name} »."))
    comp = n.get("compensation")
    if comp:
        c_action, c_version = resolve_action_version(workspace, comp.get("action"), comp.get("version"))
        if c_action is None or c_version is None:
            issues.append(issue(nid, "unknown_compensation", "L'action de compensation est introuvable."))
    _validate_params(nid, version.input_schema or {}, n.get("inputs") or {}, slug, issues, out_types, declared_inputs)


LOOSE_REFERENCE = re.compile(r"^(?:steps\.[a-z][a-z0-9_]*\.output\.\w+|inputs\.\w+|vars\.\w+)(?:\.\w+)*$")


def _validate_params(nid, schema, given, label, issues, out_types, declared_inputs):
    """Check the mapped parameters of a step (action or AI skill) against the contract it must satisfy."""
    props = schema.get("properties", {})
    for key in given:
        if key not in props:
            issues.append(issue(nid, "unknown_param", f"Paramètre inconnu pour {label} : {key}."))
    for key in schema.get("required", []):
        if given.get(key) in (None, ""):
            issues.append(
                issue(nid, "missing_param", f"Paramètre obligatoire manquant : {props.get(key, {}).get('title', key)}.")
            )
    for key, value in given.items():
        if key not in props or value in (None, ""):
            continue
        value = coerce_structured(value, props[key])  # JSON text typed for a list/object parameter is that list/object
        if isinstance(value, str) and props[key].get("type") in ("array", "object") and not WHOLE_RE.match(value):
            issues.append(
                issue(
                    nid,
                    "bad_param_value",
                    f"{props[key].get('title', key)} : une {'liste' if props[key]['type'] == 'array' else 'structure'} "
                    "(JSON) est attendue, ou une variable entre accolades ; ce texte n'est pas du JSON valide.",
                )
            )
            continue
        exprs = template_expressions(value)
        if not exprs and isinstance(value, str) and LOOSE_REFERENCE.match(value.strip()):
            issues.append(
                issue(
                    nid,
                    "literal_reference",
                    f"« {props[key].get('title', key)} » contient le texte « {value.strip()} » tel quel : pour lire cette "
                    f"valeur, écrivez-la entre accolades ({{{{ {value.strip()} }}}}).",
                )
            )
        elif not exprs:
            try:
                jsonschema.validate(value, props[key])
            except jsonschema.ValidationError as exc:
                issues.append(
                    issue(nid, "bad_param_value", f"{props[key].get('title', key)} : {explain_validation_error(exc)}")
                )
        elif isinstance(value, str) and WHOLE_RE.match(value):
            _check_type(nid, key, props[key], WHOLE_RE.match(value).group(1), declared_inputs, out_types, issues)


def _check_type(nid, key, target_schema, src, declared_inputs, out_types, issues):
    try:
        tree = parse(src)
    except ExpressionError:
        return
    if tree[0] != "path":
        return
    path = tree[1]
    src_type = None
    if path[0] == "steps" and len(path) == 4 and path[2] == "output":
        fields = out_types.get(path[1])
        src_type = (fields or {}).get(path[3], {}).get("type")
    elif path[0] == "inputs" and len(path) == 2:
        src_type = (declared_inputs.get(path[1]) or {}).get("type")
    if not _compatible(src_type, target_schema.get("type")):
        issues.append(
            issue(
                nid,
                "type_mismatch",
                f"« {target_schema.get('title', key)} » attend {target_schema.get('type')}, "
                f"mais la variable fournit {src_type}."
                + (
                    " Pour la passer comme texte, intégrez-la dans une phrase (ex. « Machines : {{ … }} »), "
                    "ou calculez un texte dans un script."
                    if target_schema.get("type") == "string" and src_type in ("array", "object")
                    else ""
                ),
            )
        )


def _validate_variables(variables) -> list[dict]:
    out, seen = [], set()
    if not isinstance(variables, list):
        return [issue(None, "bad_variables", "Les variables du workflow sont mal formées.")]
    py = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "array": list, "object": dict}
    for v in variables:
        name = v.get("name") if isinstance(v, dict) else None
        if not isinstance(name, str) or not IDENT.match(name) or name in RESERVED:
            out.append(
                issue(
                    None,
                    "bad_variable_name",
                    f"Nom de variable invalide : {name!r} (lettres, chiffres, _ ; pas de mot réservé).",
                )
            )
            continue
        if name in seen:
            out.append(issue(None, "duplicate_variable", f"Variable en double : {name}."))
        seen.add(name)
        t = v.get("type", "string")
        if t not in VAR_TYPES:
            out.append(issue(None, "bad_variable_type", f"Type inconnu pour la variable {name} : {t}."))
        elif v.get("value") is not None and not (
            isinstance(v["value"], py[t]) and not (t in ("integer", "number") and isinstance(v["value"], bool))
        ):
            out.append(issue(None, "bad_variable_value", f"La valeur de la variable {name} n'est pas de type {t}."))
        env_values = v.get("env_values")
        if env_values is not None:
            if not isinstance(env_values, dict) or set(env_values) - {"dev", "test", "prod"}:
                out.append(
                    issue(None, "bad_variable_value", f"Valeurs par environnement de {name} : dev, test ou prod.")
                )
            elif t in py:
                for env_kind, val in env_values.items():
                    if val is not None and not (
                        isinstance(val, py[t]) and not (t in ("integer", "number") and isinstance(val, bool))
                    ):
                        out.append(
                            issue(
                                None,
                                "bad_variable_value",
                                f"La valeur de {name} pour « {env_kind} » n'est pas de type {t}.",
                            )
                        )
    return out


def _validate_form(form, declared_inputs) -> list[dict]:
    if form in (None, {}):
        return []
    if not isinstance(form, dict) or not isinstance(form.get("steps"), list) or not form["steps"]:
        return [issue(None, "bad_form", "Le formulaire d'entrée doit comporter au moins une étape.")]
    out, used = [], set()
    for i, st in enumerate(form["steps"], 1):
        if not isinstance(st, dict) or not str(st.get("title", "")).strip() or not isinstance(st.get("fields"), list):
            out.append(issue(None, "bad_form", f"L'étape {i} du formulaire est incomplète (titre et champs requis)."))
            continue
        for f in st["fields"]:
            if f not in declared_inputs:
                out.append(issue(None, "bad_form", f"L'étape {i} du formulaire référence une entrée inconnue : {f}."))
            elif f in used:
                out.append(issue(None, "bad_form", f"L'entrée {f} apparaît dans plusieurs étapes du formulaire."))
            used.add(f)
    missing = [k for k in declared_inputs if k not in used]
    if missing:
        out.append(issue(None, "bad_form", f"Entrées absentes du formulaire : {', '.join(missing)}.", "warning"))
    return out


def _validate_input_form(definition, inputs, workspace) -> list[dict]:
    """The designed input form (`input_form`): well formed, bound to the workflow's own inputs, connections known."""
    if definition in (None, {}):
        return []
    from formbuilder import service as formservice
    from triggers import formengine

    mapping = definition.get("map") if isinstance(definition, dict) else None  # which field feeds which input
    if mapping is not None and not formengine._valid_map(mapping):
        return [issue(None, "bad_input_form", "Formulaire d'entrée : correspondance des entrées invalide.")]
    if isinstance(definition, dict) and "ref" in definition and "sections" not in definition:
        if workspace is None:
            return []
        if not (definition["ref"] or {}).get("form"):
            return [
                issue(
                    None,
                    "bad_input_form",
                    "Formulaire d'entrée : choisissez le formulaire de la bibliothèque à utiliser.",
                )
            ]
        try:
            definition = formservice.resolve(workspace, definition["ref"])[1].definition
        except formservice.FormRefError as exc:
            return [issue(None, "bad_input_form", f"Formulaire d'entrée : {exc}")]
    problems = formengine.check_definition(definition) or formengine.check_against_inputs(
        definition, inputs or {}, mapping
    )
    out = [issue(None, "bad_input_form", f"Formulaire d'entrée : {k} : {v}") for k, v in list(problems.items())[:5]]
    if problems or workspace is None:
        return out
    from triggers import shaping
    from vault.auth import HTTP_KINDS
    from vault.models import Connection

    for key, src in formengine.iter_sources(definition):
        for name in shaping.connection_names(src):
            if not Connection.objects.filter(workspace=workspace, name=name, kind__in=HTTP_KINDS).exists():
                out.append(
                    issue(
                        None,
                        "input_form_connection",
                        f"Formulaire d'entrée : « {key} » lit un service par la connexion « {name} », introuvable.",
                        "warning",
                    )
                )
    return out


CONTEXT_ROOTS = {"steps"}  # a script gets `inputs` and `vars`, but never the outputs of other steps


def _script_reads_context(nid, used, issues):
    """A script sees `inputs`, `vars` and its bindings, not `steps`: reading it directly fails at run time."""
    for root in sorted(used):
        issues.append(
            issue(
                nid,
                "script_reads_context",
                f"Le script lit « {root} » directement, mais un script ne voit pas les sorties des autres étapes : "
                "il ne reçoit que inputs, vars et ses liaisons. Ajoutez une liaison (ex. resultats = {{ steps.etape.output.champ }}) et utilisez cette variable.",
            )
        )


def _python_names(tree) -> tuple[set, set]:
    """(names the code may read, names it assigns) — deliberately generous (any scope) so a hit is a real mistake."""
    import builtins

    stored, defined = set(), set(dir(builtins))
    for x in ast.walk(tree):
        if isinstance(x, ast.Name) and isinstance(x.ctx, (ast.Store, ast.Del)):
            stored.add(x.id)
        elif isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(x.name)
        elif isinstance(x, ast.arg):
            defined.add(x.arg)
        elif isinstance(x, (ast.Import, ast.ImportFrom)):
            defined.update((a.asname or a.name).split(".")[0] for a in x.names)
        elif isinstance(x, ast.ExceptHandler) and x.name:
            defined.add(x.name)
        elif isinstance(x, ast.MatchAs) and x.name:
            defined.add(x.name)
        elif isinstance(x, ast.MatchStar) and x.name:
            defined.add(x.name)
    return defined | stored, stored


def _script_static_checks(nid, lang, code, tree, bound, outputs, issues):
    """Mistakes a model (or a hurried person) makes in scripts, found without running anything: names that exist
    nowhere, declared outputs the code never sets."""
    if tree is not None:
        known, stored = _python_names(tree)
        known |= bound | {"System", "inputs", "vars"} | CONTEXT_ROOTS  # `steps` has its own, clearer message
        loaded = {x.id for x in ast.walk(tree) if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Load)}
        for name in sorted(loaded - known):
            issues.append(
                issue(
                    nid,
                    "script_undefined_name",
                    f"Le script utilise « {name} » qui n'est défini nulle part : déclarez-le dans les liaisons "
                    "(entrées) ou affectez-le avant de l'utiliser.",
                )
            )
        assigned = stored
    elif lang == "javascript":
        assigned = {
            o
            for o in outputs
            if re.search(rf"(?<![\w.$])(?:(?:let|const|var)\s+)?{re.escape(o)}\s*(?:[-+*/]?=)(?!=)", code)
            or re.search(rf"\b(?:let|const|var)\s+{re.escape(o)}\b", code)
        }
    else:
        return
    for o in outputs:
        if o not in assigned:
            issues.append(
                issue(
                    nid,
                    "script_output_not_set",
                    f"La sortie « {o} » est déclarée mais le script ne l'affecte jamais : écrivez {o} = … dans le code.",
                )
            )


def _validate_script(n, issues):
    """A script task: language, code, bound variables and typed outputs. Returns {output: schema} for downstream checks."""
    nid = n["id"]
    if n.get("ai_review"):
        issues.append(
            issue(
                nid,
                "ai_review_required",
                "Script écrit par l'IA : relisez le code, testez-le, puis confirmez « J'ai relu ce code » dans l'éditeur de script.",
            )
        )
    lang, code = n.get("language"), n.get("script", "")
    tree = None
    if lang not in SCRIPT_LANGUAGES:
        issues.append(issue(nid, "bad_language", "Langage de script inconnu (python ou javascript)."))
    if not isinstance(code, str) or not code.strip():
        issues.append(issue(nid, "empty_script", "Le script est vide."))
    elif len(code) > MAX_SCRIPT:
        issues.append(issue(nid, "script_too_large", "Le script dépasse 100 Ko."))
    elif lang == "python":
        try:
            tree = ast.parse(code)  # syntax only: never executed here
        except SyntaxError as exc:
            tree = None
            issues.append(issue(nid, "script_syntax", f"Erreur de syntaxe ligne {exc.lineno} : {exc.msg}."))
        else:
            used = {x.id for x in ast.walk(tree) if isinstance(x, ast.Name) and x.id in CONTEXT_ROOTS}
            _script_reads_context(nid, used, issues)
            dotted = {
                x.value.id
                for x in ast.walk(tree)
                if isinstance(x, ast.Attribute)
                and isinstance(x.ctx, ast.Load)
                and isinstance(x.value, ast.Name)
                and x.value.id in ("inputs", "vars")
            }
            writes = {
                x.value.id
                for x in ast.walk(tree)
                if isinstance(x, (ast.Attribute, ast.Subscript))
                and isinstance(x.ctx, (ast.Store, ast.Del))
                and isinstance(x.value, ast.Name)
                and x.value.id in ("inputs", "vars")
            }
            for root in sorted(writes):
                issues.append(
                    issue(
                        nid,
                        "script_writes_context",
                        f"Le script écrit dans « {root} » : sans effet (lecture seule). Pour transmettre une valeur, "
                        "déclarez-la dans les sorties du script et affectez-la.",
                    )
                )
            for root in sorted(dotted):  # in Python they are dictionaries: inputs.x raises AttributeError
                issues.append(
                    issue(
                        nid,
                        "script_dict_access",
                        f"En Python, « {root} » est un dictionnaire : écrivez {root}['nom'] (ou {root}.get('nom')), "
                        f"pas {root}.nom.",
                    )
                )
    elif lang == "javascript":
        _script_reads_context(nid, set(re.findall(r"\bsteps\s*[.\[]", code)), issues)
    names = set()
    for k in n.get("bindings") or {}:
        if (
            not IDENT.match(k)
            or k in RESERVED
            or (lang == "python" and keyword.iskeyword(k))
            or (lang == "javascript" and k in JS_RESERVED)
        ):
            issues.append(issue(nid, "bad_binding", f"Nom de variable d'entrée invalide ou réservé : {k}."))
        names.add(k)
    outs = {}
    for o in n.get("outputs") or []:
        name, t = (
            (o.get("name") if isinstance(o, dict) else None),
            (o.get("type", "string") if isinstance(o, dict) else None),
        )
        if not isinstance(name, str) or not IDENT.match(name) or name in RESERVED or name in names or name in outs:
            issues.append(issue(nid, "bad_output", f"Nom de sortie invalide, réservé ou en double : {name!r}."))
        elif t not in VAR_TYPES:
            issues.append(issue(nid, "bad_output", f"Type de sortie inconnu pour {name} : {t}."))
        else:
            outs[name] = {"type": t}
    if (
        isinstance(code, str)
        and code.strip()
        and len(code) <= MAX_SCRIPT
        and (tree is not None or lang == "javascript")
    ):
        _script_static_checks(nid, lang, code, tree, names, list(outs), issues)
    t = n.get("timeout_seconds", 60)
    if not isinstance(t, int) or not 1 <= t <= 600:
        issues.append(issue(nid, "bad_timeout", "Délai du script : 1 à 600 secondes."))
    return outs


def _validate_ai(n, workspace, issues, out_types, declared_inputs):
    from ai.models import AiProvider

    nid = n["id"]
    provider = n.get("provider", "")
    if n.get("skill"):
        slug = n["skill"]
        skill, version = resolve_skill_version(workspace, slug, n.get("skill_version"))
        if skill is None:
            issues.append(issue(nid, "unknown_skill", f"Skill IA introuvable dans cet espace : {slug}."))
            return None
        if version is None:
            issues.append(
                issue(
                    nid, "version_unavailable", f"Version {n.get('skill_version')} indisponible pour le skill {slug}."
                )
            )
            return None
        if skill.status == "proposed":
            issues.append(
                issue(
                    nid,
                    "proposed_skill",
                    f"Le skill « {skill.name} » est une proposition d'assistant : relisez-le et activez-le avant de publier ou lancer.",
                )
            )
        if skill.status == "deprecated":
            issues.append(
                issue(
                    nid,
                    "deprecated_skill",
                    f"Le skill « {skill.name} » est déprécié : {skill.deprecation_reason or 'prévoyez de le remplacer'}.",
                    "warning",
                )
            )
        provider = provider or version.default_provider
        if not AiProvider.objects.filter(workspace=workspace, name=provider, enabled=True).exists():
            issues.append(issue(nid, "unknown_provider", "Choisissez un fournisseur d'IA configuré pour cet espace."))
        _validate_params(
            nid,
            version.input_schema or {},
            n.get("inputs") or {},
            f"le skill {slug}",
            issues,
            out_types,
            declared_inputs,
        )
        return (version.output_schema or {}).get("properties", {})
    if not str(n.get("prompt", "")).strip():
        issues.append(issue(nid, "missing_prompt", "Décrivez la tâche confiée à l'IA ou choisissez un skill."))
    if not AiProvider.objects.filter(workspace=workspace, name=provider, enabled=True).exists():
        issues.append(issue(nid, "unknown_provider", "Choisissez un fournisseur d'IA configuré pour cet espace."))
    schema = n.get("output_schema")
    try:
        if not isinstance(schema, dict) or schema.get("type") != "object" or not schema.get("properties"):
            raise jsonschema.SchemaError("objet avec propriétés attendu")
        jsonschema.Draft202012Validator.check_schema(schema)
    except jsonschema.SchemaError:
        issues.append(
            issue(nid, "bad_output_schema", "Définissez le format de réponse attendu (objet avec champs typés).")
        )
        return None
    return schema["properties"]


def _validate_subworkflow(n, workspace, current_slug, issues, out_types=None, declared_inputs=None):
    nid = n["id"]
    slug = n.get("workflow")
    wf, version = resolve_workflow_version(workspace, slug, n.get("version")) if slug else (None, None)
    if wf is None:
        issues.append(issue(nid, "unknown_workflow", f"Workflow introuvable : {slug}."))
        return
    if slug == current_slug:
        issues.append(issue(nid, "recursion", "Un workflow ne peut pas s'appeler lui-même."))
    if version is None:
        issues.append(issue(nid, "version_unavailable", f"Aucune version publiée de {slug}."))
    if n["type"] == "foreach" and n.get("interval_seconds") is not None:
        iv = n["interval_seconds"]
        if not isinstance(iv, int) or not 0 <= iv <= 3600:
            issues.append(issue(nid, "bad_wait", "Intervalle entre deux itérations : de 0 à 3600 secondes."))
    if n["type"] in ("foreach", "while"):
        mx = n.get("max_iterations")
        if not isinstance(mx, int) or not 1 <= mx <= MAX_FOREACH:
            issues.append(issue(nid, "unbounded_loop", f"Une boucle doit borner ses itérations (1 à {MAX_FOREACH})."))
    if version is not None and out_types is not None:
        # the inputs given to the called workflow must satisfy ITS contract (a run would be refused otherwise)
        schema = copy.deepcopy(version.input_schema or {})
        item = n.get("item_input") if n["type"] == "foreach" else None
        if item:
            if item not in (schema.get("properties") or {}):
                issues.append(
                    issue(nid, "unknown_param", f"Le workflow {slug} n'a pas d'entrée « {item} » pour l'élément.")
                )
            schema.get("properties", {}).pop(item, None)
            schema["required"] = [r for r in schema.get("required", []) if r != item]
        _validate_params(nid, schema, n.get("inputs") or {}, slug, issues, out_types, declared_inputs or {})


validate = validate_graph


def pin_graph(graph: dict, workspace) -> dict:
    """Return a copy where every action/sub-workflow reference carries an exact version."""
    import copy

    pinned = copy.deepcopy(graph)
    for n in pinned["nodes"]:
        if n["type"] == "action":
            _, v = resolve_action_version(workspace, n["action"], n.get("version"))
            n["version"] = v.version
            comp = n.get("compensation")
            if comp:
                _, cv = resolve_action_version(workspace, comp["action"], comp.get("version"))
                comp["version"] = cv.version
        elif n["type"] in ("subworkflow", "foreach", "while"):
            _, v = resolve_workflow_version(workspace, n["workflow"], n.get("version"))
            n["version"] = v.version
        elif n["type"] == "ai" and n.get("skill"):
            _, sv = resolve_skill_version(workspace, n["skill"], n.get("skill_version"))
            n["skill_version"] = sv.version
            n["provider"] = n.get("provider") or sv.default_provider
    if pinned.get("input_form"):
        from formbuilder import service as formservice

        try:
            pinned["input_form"] = formservice.pin_input_form(workspace, pinned["input_form"])
        except formservice.FormRefError as exc:
            raise ValueError(f"Formulaire d'entrée : {exc}") from None
    return pinned
