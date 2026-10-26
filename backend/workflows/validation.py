"""Server-side workflow graph validation (the client mirrors a subset; this is authoritative).

Returns a list of issues {node, level, code, message}. Publishing requires zero 'error' issues.
"""

import ast
import keyword
import re
from collections import defaultdict, deque

import jsonschema

from catalog.models import Action
from vault.models import Connection

from .expressions import WHOLE_RE, ExpressionError, parse, paths_in, template_expressions

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
NODE_TYPES = {"action", "condition", "join", "wait", "approval", "subworkflow", "foreach", "ai", "script", "throw"}
STATIC_OUTPUTS = {
    "condition": {"result": {"type": "boolean"}},
    "approval": {
        "approved": {"type": "boolean"},
        "decided_by": {"type": "string"},
        "comment": {"type": "string"},
    },
    "foreach": {"results": {"type": "array"}, "count": {"type": "integer"}},
    "wait": {},
    "join": {},
}
ERROR_CAPABLE = {"action", "subworkflow", "foreach", "approval", "ai", "script"}


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


def issue(node, code, message, level="error"):
    return {"node": node, "level": level, "code": code, "message": message}


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
    incoming = defaultdict(list)
    outgoing = defaultdict(list)
    for e in clean_edges:
        incoming[e["to"]].append(e)
        outgoing[e["from"]].append(e)

    if nodes and not any(not incoming[n["id"]] for n in nodes):
        issues.append(issue(None, "no_entry", "Aucune étape de départ."))

    out_types: dict[str, dict | None] = {}  # node id -> {field: schema} or None when unknown

    for n in nodes:
        nid, ntype = n["id"], n["type"]
        if len(incoming[nid]) > 1 and ntype != "join":
            issues.append(issue(nid, "implicit_join", "Plusieurs flux arrivent ici : ajoutez une étape « Jonction »."))
        if ntype == "join" and len(incoming[nid]) < 2:
            issues.append(issue(nid, "weak_join", "Une jonction attend au moins deux flux entrants.", "warning"))
        labels = [e.get("label") or "next" for e in outgoing[nid]]
        if ntype == "condition":
            if not set(labels) <= {"true", "false"} or len(set(labels)) != len(labels) or not labels:
                issues.append(
                    issue(nid, "bad_branches", "Une condition a au plus une sortie « oui » et une sortie « non ».")
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
                _check_expr(n["expr"], nid, anc[nid], by_id, declared_inputs, out_types, issues, declared_vars)
            out_types[nid] = STATIC_OUTPUTS["condition"]
        elif ntype == "wait":
            s = n.get("seconds")
            if not isinstance(s, int) or not 1 <= s <= 30 * 86400:
                issues.append(issue(nid, "bad_wait", "Délai d'attente : entre 1 seconde et 30 jours."))
            out_types[nid] = {}
        elif ntype == "approval":
            if not str(n.get("title", "")).strip():
                issues.append(issue(nid, "missing_title", "Indiquez ce qui doit être approuvé."))
            out_types[nid] = STATIC_OUTPUTS["approval"]
        elif ntype == "script":
            out_types[nid] = _validate_script(n, issues)
            for src in template_expressions(n.get("bindings", {})):
                _check_expr(src, nid, anc[nid], by_id, declared_inputs, out_types, issues, declared_vars)
        elif ntype == "throw":
            out_types[nid] = {}
            for src in template_expressions(n.get("message", "")):
                _check_expr(src, nid, anc[nid], by_id, declared_inputs, out_types, issues, declared_vars)
        elif ntype == "ai":
            out_types[nid] = _validate_ai(n, workspace, issues, out_types, declared_inputs)
            for src in template_expressions([n.get("prompt", ""), n.get("inputs", {})]):
                _check_expr(src, nid, anc[nid], by_id, declared_inputs, out_types, issues, declared_vars)
        elif ntype == "join":
            out_types[nid] = {}
        elif ntype in ("subworkflow", "foreach"):
            _validate_subworkflow(n, workspace, current_workflow_slug, issues)
            out_types[nid] = STATIC_OUTPUTS["foreach"] if ntype == "foreach" else None
            if ntype == "foreach":
                items = n.get("items")
                if not isinstance(items, str) or not items.strip():
                    issues.append(issue(nid, "missing_items", "Indiquez la liste à parcourir."))
                else:
                    _check_expr(items, nid, anc[nid], by_id, declared_inputs, out_types, issues, declared_vars)
        # template references in inputs of any node
        if ntype in ("action", "subworkflow", "foreach"):
            for src in template_expressions(n.get("inputs", {})):
                _check_expr(src, nid, anc[nid], by_id, declared_inputs, out_types, issues, declared_vars)

    for src in template_expressions(graph.get("outputs", {})):
        _check_expr(src, None, set(by_id), by_id, declared_inputs, out_types, issues, declared_vars)
    return issues


def _check_expr(src, nid, allowed_steps, by_id, declared_inputs, out_types, issues, declared_vars):
    try:
        tree = parse(src)
    except ExpressionError as exc:
        issues.append(issue(nid, "bad_expression", f"Expression invalide « {src.strip()} » : {exc}"))
        return
    for path in paths_in(tree):
        root = path[0]
        if root == "inputs":
            if len(path) < 2 or path[1] not in declared_inputs:
                issues.append(issue(nid, "unknown_input", f"Entrée inconnue : {'.'.join(map(str, path))}."))
        elif root == "vars":
            if len(path) < 2 or path[1] not in declared_vars:
                issues.append(issue(nid, "unknown_variable", f"Variable inconnue : {'.'.join(map(str, path))}."))
        elif root == "steps":
            if len(path) < 3 or path[2] != "output":
                issues.append(issue(nid, "bad_reference", "Référence attendue : steps.<étape>.output.<champ>."))
            elif path[1] not in by_id:
                issues.append(issue(nid, "unknown_step", f"Étape inconnue : {path[1]}."))
            elif path[1] not in allowed_steps:
                issues.append(issue(nid, "not_upstream", f"L'étape « {path[1]} » n'est pas en amont de celle-ci."))
            else:
                fields = out_types.get(path[1])
                if fields is not None and len(path) > 3 and path[3] not in fields:
                    issues.append(issue(nid, "unknown_output", f"« {path[1]} » ne produit pas de champ « {path[3]} »."))
        else:
            issues.append(issue(nid, "bad_reference", f"Racine inconnue : {root} (utilisez inputs ou steps)."))


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
        exprs = template_expressions(value)
        if not exprs:
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
                f"mais la variable fournit {src_type}.",
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
    if lang not in SCRIPT_LANGUAGES:
        issues.append(issue(nid, "bad_language", "Langage de script inconnu (python ou javascript)."))
    if not isinstance(code, str) or not code.strip():
        issues.append(issue(nid, "empty_script", "Le script est vide."))
    elif len(code) > MAX_SCRIPT:
        issues.append(issue(nid, "script_too_large", "Le script dépasse 100 Ko."))
    elif lang == "python":
        try:
            ast.parse(code)  # syntax only: never executed here
        except SyntaxError as exc:
            issues.append(issue(nid, "script_syntax", f"Erreur de syntaxe ligne {exc.lineno} : {exc.msg}."))
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


def _validate_subworkflow(n, workspace, current_slug, issues):
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
    if n["type"] == "foreach":
        mx = n.get("max_iterations")
        if not isinstance(mx, int) or not 1 <= mx <= MAX_FOREACH:
            issues.append(issue(nid, "unbounded_loop", f"Une boucle doit borner ses itérations (1 à {MAX_FOREACH})."))


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
        elif n["type"] in ("subworkflow", "foreach"):
            _, v = resolve_workflow_version(workspace, n["workflow"], n.get("version"))
            n["version"] = v.version
        elif n["type"] == "ai" and n.get("skill"):
            _, sv = resolve_skill_version(workspace, n["skill"], n.get("skill_version"))
            n["skill_version"] = sv.version
            n["provider"] = n.get("provider") or sv.default_provider
    return pinned
