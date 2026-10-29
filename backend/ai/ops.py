"""Edit operations: how the assistant changes an existing workflow.

Instead of rewriting the whole graph (and silently altering what it did not mean to touch), a model lists small,
explicit operations; this module applies them to a copy of the graph, deterministically. What the person is told is then
what the operations really did, a claimed change that is not among them is visible, and the prompt stays short.

    {"op": "add_step", "step": {"type": "script", "id": "calcul", …}, "after": "id", "label": "true|false|error",
                                                                     "before": "id"}  (neither: appended at the end)
    {"op": "add_branch", "after": "id", "label": "error", "steps": [{…}, {…}]}   (a chain on one exit: refusal = notify, throw)
    {"op": "remove_step", "id": "…", "keep": "true|false"}                 (keep: for a decision, the branch that stays)
    {"op": "set_fields", "id": "…", "fields": {"expr": "…", "name": "…"}}
    {"op": "connect", "from": "…", "to": "…", "label": "true|false|error"}   {"op": "disconnect", "from": "…", "to": "…"}
    {"op": "set_variable", "name": "…", "type": "integer", "value": 3}       {"op": "remove_variable", "name": "…"}
    {"op": "add_note", "text": "…"}  {"op": "edit_note", "id": "…", "text": "…"}  {"op": "remove_note", "id": "…"}
    {"op": "set_input", "name": "…", "schema": {"type": "string"}, "required": true}  {"op": "remove_input", "name": "…"}
    {"op": "set_output", "name": "…", "expr": "{{ steps.x.output.y }}"}

`apply_ops` never raises: an operation that cannot be applied is skipped and reported (in French, for the model and the
person). The result still goes through the regular sanitiser and validator, which hold the real guarantees (approvals are
never dropped, locked steps are untouched, types and sizes are bounded)."""

import copy

from workflows.validation import case_ids

from .plan import slug_id

MAX_OPS = 40
ADDABLE = (
    "action",
    "script",
    "wait",
    "approval",
    "throw",
    "subworkflow",
    "foreach",
    "ai",
    "condition",
    "switch",
    "http",
    "transform",
)
FIELDS = {
    "action": ("action", "inputs", "retry"),
    "script": ("language", "script", "bindings", "outputs", "retry"),
    "condition": ("expr",),
    "switch": ("cases",),
    "wait": ("seconds",),
    "approval": ("title", "instructions"),
    "throw": ("code", "message"),
    "subworkflow": ("workflow", "inputs"),
    "foreach": ("workflow", "items", "item_input", "max_iterations", "concurrency", "inputs"),
    "ai": ("provider", "skill", "prompt", "inputs", "output_schema", "retry"),
    "http": (
        "integration",
        "operation",
        "params",
        "connection",
        "method",
        "path",
        "query",
        "headers",
        "body",
        "pagination",
        "fail_on_status",
    ),
    "transform": (
        "op",
        "items",
        "where",
        "by",
        "order",
        "right",
        "merge_mode",
        "left_by",
        "right_by",
        "count",
        "skip",
        "size",
        "text",
        "separator",
        "fields",
        "aggregates",
    ),
    "join": (),
}
KINDS = {int: "integer", float: "number", bool: "boolean", list: "array", dict: "object", str: "string"}


class _Graph:
    def __init__(self, graph: dict):
        self.g = copy.deepcopy(graph)
        self.g.setdefault("nodes", [])
        self.g.setdefault("edges", [])
        self.renamed: dict[str, str] = {}  # ids a new step was asked to have -> ids it got
        self.removed_notes: list[str] = []
        self.removed_variables: list[str] = []

    # ---------------------------------------------------------------- lookups
    @property
    def nodes(self) -> list:
        return self.g["nodes"]

    @property
    def edges(self) -> list:
        return self.g["edges"]

    def ref(self, nid):
        return self.renamed.get(nid, nid) if isinstance(nid, str) else nid

    def node(self, nid) -> dict:
        nid = self.ref(nid)
        for n in self.nodes:
            if n.get("id") == nid:
                return n
        raise _Refused(f"L'étape « {nid} » n'existe pas.")

    def ids(self) -> set:
        return {n.get("id") for n in self.nodes}

    def out(self, nid, label="*") -> list:
        return [e for e in self.edges if e["from"] == nid and (label == "*" or (e.get("label") or None) == label)]

    def into(self, nid) -> list:
        return [e for e in self.edges if e["to"] == nid]

    def reaches(self, start, goal) -> bool:
        seen, todo = set(), [start]
        while todo:
            cur = todo.pop()
            if cur == goal:
                return True
            if cur not in seen:
                seen.add(cur)
                todo += [e["to"] for e in self.out(cur)]
        return False

    def edge(self, src, dst, label=None):
        e = {"from": src, "to": dst, **({"label": label} if label else {})}
        if e not in self.edges:
            self.edges.append(e)

    def new_id(self, wanted, fallback) -> str:
        base, k = slug_id(wanted, fallback), 2
        out = base
        while out in self.ids():
            tail = f"_{k}"
            out, k = base[: 32 - len(tail)] + tail, k + 1
        return out


class _Refused(Exception):
    pass


# ---------------------------------------------------------------------------------------------------------- steps
def _add_step(w: _Graph, op: dict):
    step = op.get("step")
    if not isinstance(step, dict) or step.get("type") not in ADDABLE:
        raise _Refused(f"step doit être un objet dont le type est l'un de : {', '.join(ADDABLE)}.")
    wanted = step.get("id")
    nid = w.new_id(wanted or step.get("name"), f"etape_{len(w.nodes) + 1}")
    if (
        isinstance(wanted, str) and wanted != nid and wanted not in w.ids()
    ):  # a taken id keeps meaning the existing step
        w.renamed[wanted] = nid
    node = {k: v for k, v in step.items() if k not in ("ui", "version", "skill_version")}
    node["id"] = nid
    after, before, label = (w.ref(op.get("after")), w.ref(op.get("before")), op.get("label") or None)
    if after is not None and w.node(after).get("type") == "condition" and label not in ("true", "false", "error"):
        raise _Refused(f"« {after} » est une décision : précise label « true » ou « false ».")
    if after is not None and w.node(after).get("type") == "switch":
        _switch_label(w, after, label)
    elif label not in (None, "true", "false", "error"):
        raise _Refused("label doit être true, false ou error.")
    if after is None and before is None and w.nodes:
        sinks = [n["id"] for n in w.nodes if not w.out(n["id"])]
        if len(sinks) != 1:
            raise _Refused("Plusieurs fins possibles : précise after (ou before) pour placer la nouvelle étape.")
        after = sinks[0]
    if before is not None:
        w.node(before)
    onward = "true" if node["type"] == "condition" else None
    w.nodes.append(node)
    if after is not None and before is not None:
        between = [e for e in w.out(after) if e["to"] == before]
        if not between:
            raise _Refused(f"Il n'y a pas de liaison entre « {after} » et « {before} ».")
        for e in between:
            e["to"] = nid
        w.edge(nid, before, onward)
    elif after is not None:
        for e in w.out(after, label):  # the new step takes over what followed that exit
            e["from"] = nid
            e["label"] = onward
            if not e["label"]:
                e.pop("label")
        w.edge(after, nid, label)
    elif before is not None:
        for e in w.into(before):
            e["to"] = nid
        w.edge(nid, before, onward)


def _switch_label(w: _Graph, nid: str, label, what: str = "label"):
    allowed = [*case_ids(w.node(nid)), "default"]
    if label not in allowed:
        raise _Refused(f"« {nid} » est un choix multiple : précise {what} parmi {', '.join(allowed)}.")


def _add_branch(w: _Graph, op: dict):
    """A chain of steps hung on one exit of a step (e.g. a refusal: notification, then exception): the first one is
    attached with `label`, each next one follows the previous on its normal exit. Saves the model from labelling a chain."""
    steps = op.get("steps")
    if not isinstance(steps, list) or not steps or not all(isinstance(x, dict) for x in steps):
        raise _Refused("steps doit être une liste non vide d'étapes (objets).")
    if w.node(op.get("after")).get("type") == "condition" and (op.get("label") or None) not in ("true", "false"):
        raise _Refused(f"« {op.get('after')} » est une décision : précise label « true » ou « false ».")
    if w.node(op.get("after")).get("type") == "switch":
        _switch_label(w, op.get("after"), op.get("label") or None)
    prev, label = op.get("after"), op.get("label")
    for step in steps[:10]:
        wanted = step.get("id")
        before = w.ids()
        _add_step(w, {"step": step, "after": prev, "label": label})
        new = next(n["id"] for n in w.nodes if n["id"] not in before)
        prev, label = new, None
        if isinstance(wanted, str) and wanted in before:
            w.renamed.pop(wanted, None)


def _drop_orphans(w: _Graph, candidates: list):
    """Steps that only existed to serve what was removed (an exception hanging on a removed step, a dropped branch)."""
    todo = list(candidates)
    while todo:
        nid = todo.pop()
        if nid not in w.ids():
            continue
        if w.into(nid):
            continue
        node = w.node(nid)
        if node.get("type") == "approval":
            continue  # a control gate is never removed as a side effect
        todo += [e["to"] for e in w.out(nid)]
        w.nodes.remove(node)
        w.edges[:] = [e for e in w.edges if nid not in (e["from"], e["to"])]


def _remove_step(w: _Graph, op: dict):
    node = w.node(op.get("id"))
    nid = node["id"]
    if node.get("type") == "approval":
        raise _Refused(
            "Une approbation ne peut pas être supprimée par l'assistant : supprimez-la vous-même dans l'éditeur."
        )
    keep = op.get("keep")
    outs = w.out(nid)
    if node.get("type") == "switch":
        _switch_label(w, nid, keep, what="keep")
        passing = [e for e in outs if e.get("label") == keep]
        dropped = [e["to"] for e in outs if e.get("label") != keep]
    elif node.get("type") == "condition":
        if keep not in ("true", "false"):
            raise _Refused(f"« {nid} » est une décision : précise keep (« true » ou « false »), la branche qui reste.")
        passing = [e for e in outs if e.get("label") == keep]
        dropped = [e["to"] for e in outs if e.get("label") != keep]
    else:
        passing = [e for e in outs if not e.get("label")]
        dropped = [e["to"] for e in outs if e.get("label")]  # e.g. the exception that hung on this step
    ins = w.into(nid)
    for i in ins:
        for p in passing:
            w.edge(i["from"], p["to"], i.get("label"))
    w.nodes.remove(node)
    w.edges[:] = [e for e in w.edges if nid not in (e["from"], e["to"])]
    _drop_orphans(w, dropped)


def _set_fields(w: _Graph, op: dict):
    node = w.node(op.get("id"))
    fields = op.get("fields")
    if not isinstance(fields, dict) or not fields:
        raise _Refused("fields doit être un objet non vide.")
    allowed = ("name", *FIELDS.get(node.get("type"), ()))
    bad = [k for k in fields if k not in allowed]
    if bad:
        raise _Refused(
            f"Champ(s) non modifiable(s) sur « {node['id']} » ({node.get('type')}) : {', '.join(bad)}. "
            f"Champs possibles : {', '.join(allowed)}."
        )
    for key, value in fields.items():
        if key in ("inputs", "bindings") and isinstance(value, dict) and isinstance(node.get(key), dict):
            merged = {**node[key], **value}  # what is not mentioned stays; a null value removes the key
            node[key] = {k: v for k, v in merged.items() if v is not None}
        else:
            node[key] = value


def _connect(w: _Graph, op: dict):
    src, dst = w.node(op.get("from"))["id"], w.node(op.get("to"))["id"]
    label = op.get("label") or None
    if src == dst or w.reaches(dst, src):
        raise _Refused(f"Relier « {src} » à « {dst} » créerait une boucle (utilisez une étape « pour chaque »).")
    if w.node(src).get("type") == "switch":
        _switch_label(w, src, label)
    if w.node(src).get("type") == "condition" and label not in ("true", "false"):
        used = {e.get("label") for e in w.out(src)}
        free = [x for x in ("true", "false") if x not in used]
        if not free:
            raise _Refused(f"La décision « {src} » a déjà ses deux sorties.")
        label = free[0]
    w.edge(src, dst, label)


def _disconnect(w: _Graph, op: dict):
    src, dst = w.node(op.get("from"))["id"], w.node(op.get("to"))["id"]
    before = len(w.edges)
    w.edges[:] = [e for e in w.edges if not (e["from"] == src and e["to"] == dst)]
    if len(w.edges) == before:
        raise _Refused(f"Il n'y a pas de liaison de « {src} » vers « {dst} ».")


# ------------------------------------------------------------------------------------------ variables, notes, I/O
def _set_variable(w: _Graph, op: dict):
    name = op.get("name")
    if not isinstance(name, str) or not name:
        raise _Refused("name est obligatoire.")
    variables = w.g.setdefault("variables", [])
    kind = op.get("type") or KINDS.get(type(op.get("value")), "string")
    for v in variables:
        if v.get("name") == name:
            v["value"] = op.get("value")
            return
    variables.append({"name": name, "type": kind, "value": op.get("value")})


def _remove_variable(w: _Graph, op: dict):
    name = op.get("name")
    if name not in {v.get("name") for v in w.g.get("variables") or []}:
        raise _Refused(f"La variable « {name} » n'existe pas.")
    w.g["variables"] = [v for v in w.g["variables"] if v.get("name") != name]
    w.removed_variables.append(name)


def _add_note(w: _Graph, op: dict):
    text = op.get("text")
    if not isinstance(text, str) or not text.strip():
        raise _Refused("text est obligatoire.")
    w.g.setdefault("notes", []).append({"text": text})  # the sanitiser gives it an identifier


def _find_note(w: _Graph, op: dict) -> dict:
    for n in w.g.get("notes") or []:
        if n.get("id") == op.get("id"):
            return n
    raise _Refused(f"La note « {op.get('id')} » n'existe pas.")


def _edit_note(w: _Graph, op: dict):
    note = _find_note(w, op)
    if not isinstance(op.get("text"), str) or not op["text"].strip():
        raise _Refused("text est obligatoire.")
    note["text"] = op["text"]


def _remove_note(w: _Graph, op: dict):
    note = _find_note(w, op)
    w.g["notes"].remove(note)
    w.removed_notes.append(note["id"])


def _set_input(w: _Graph, op: dict):
    name, schema = op.get("name"), op.get("schema")
    if not isinstance(name, str) or not name or not isinstance(schema, dict):
        raise _Refused("name et schema (objet JSON schema) sont obligatoires.")
    inputs = w.g.setdefault("inputs", {"type": "object", "properties": {}, "required": []})
    inputs.setdefault("properties", {})[name] = schema
    required = [r for r in inputs.get("required") or [] if r != name]
    inputs["required"] = required + ([name] if op.get("required") else [])


def _remove_input(w: _Graph, op: dict):
    name = op.get("name")
    props = (w.g.get("inputs") or {}).get("properties") or {}
    if name not in props:
        raise _Refused(f"L'entrée « {name} » n'existe pas.")
    del props[name]
    w.g["inputs"]["required"] = [r for r in w.g["inputs"].get("required") or [] if r != name]


def _set_output(w: _Graph, op: dict):
    name, expr = op.get("name"), op.get("expr")
    if not isinstance(name, str) or not name or not isinstance(expr, str):
        raise _Refused("name et expr sont obligatoires.")
    w.g.setdefault("outputs", {})[name] = expr


HANDLERS = {
    "add_step": _add_step,
    "add_branch": _add_branch,
    "remove_step": _remove_step,
    "set_fields": _set_fields,
    "connect": _connect,
    "disconnect": _disconnect,
    "set_variable": _set_variable,
    "remove_variable": _remove_variable,
    "add_note": _add_note,
    "edit_note": _edit_note,
    "remove_note": _remove_note,
    "set_input": _set_input,
    "remove_input": _remove_input,
    "set_output": _set_output,
}


def apply_ops(graph: dict, ops) -> tuple[dict, list[str]]:
    """The graph after the operations, and what could not be done (one French sentence each)."""
    w = _Graph(graph)
    errors: list[str] = []
    if not isinstance(ops, list):
        return w.g, ["ops doit être une liste d'opérations."]
    for i, op in enumerate(ops[:MAX_OPS], 1):
        name = op.get("op") if isinstance(op, dict) else None
        handler = HANDLERS.get(name)
        if handler is None:
            errors.append(f"Opération {i} : « {name} » est inconnue (possibles : {', '.join(HANDLERS)}).")
            continue
        try:
            handler(w, op)
        except _Refused as exc:
            errors.append(f"Opération {i} ({name}) : {exc}")
    if len(ops) > MAX_OPS:
        errors.append(f"Seules les {MAX_OPS} premières opérations sont appliquées ({len(ops)} reçues).")
    if w.removed_notes:
        w.g["removed_notes"] = w.removed_notes
    if w.removed_variables:
        w.g["removed_variables"] = w.removed_variables
    return w.g, errors
