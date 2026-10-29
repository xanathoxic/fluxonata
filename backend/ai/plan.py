"""Plan → graph compiler.

A model describes a workflow as a PLAN: a nested sequence of blocks (steps, `if`, `parallel`, `on_error`), the way a
person tells it. This module turns that tree into the graph the platform stores (steps + labelled edges) with no model
involved, so a whole class of mistakes cannot happen: every step is reachable, there is exactly one place where the flow
starts, flows only meet on a join step, a decision gets its « true » and « false » exits, and a failure branch (a refused
approval, say) hangs on the error exit of its step instead of firing on success.

    plan   = {"inputs": …, "variables": […], "notes": ["…"], "steps": [block, …], "outputs": {…}}
    block  = {"type": "script|action|wait|approval|throw|subworkflow|foreach|ai", "id": "…", …fields of the step…,
              "on_error": [block, …]}                                   a step (on_error: what runs if it fails/refused)
           | {"type": "if", "id": "…", "name": "…", "expr": "…", "then": [block…], "else": [block…]}
           | {"type": "parallel", "branches": [[block…], [block…]]}
           | {"type": "switch", "id": "…", "name": "…", "cases": [{"id": "…", "expr": "…", "steps": [block…]}, …],
              "default": [block…]}                                      a multi-way choice: the first case that holds

The output is the "raw graph" the existing sanitiser (`sanitize_draft`) expects, so everything downstream (cleaning,
validation, repair, review) is unchanged. Mistakes the compiler cannot repair (a step after an exception, an `if` with
no expression) raise PlanError with a message written for the model."""

import re
import unicodedata

from workflows.expressions import ExpressionError, parse
from workflows.validation import RESERVED_LABELS

STEP_TYPES = ("action", "script", "wait", "approval", "throw", "subworkflow", "foreach", "ai", "http", "transform")
IF_TYPES = ("if", "condition")
SWITCH_TYPES = ("switch",)
STRUCTURE_KEYS = {"then", "else", "branches", "on_error", "true", "false", "default"}
MAX_PLAN_STEPS = 40
ID_MAX = 32


class PlanError(ValueError):
    """The plan cannot be turned into a graph; the message tells the model what to change."""


def slug_id(text, fallback: str) -> str:
    """A step id the validator accepts (lowercase letter first, then lowercase letters, digits, underscore)."""
    s = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")[:ID_MAX]
    if not s or not s[0].isalpha():
        s = (fallback + "_" + s).strip("_")[:ID_MAX] if s else fallback
    return s


def _blocks(value, where: str) -> list:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(b, dict) for b in value):
        raise PlanError(f"{where} doit être une liste d'étapes (objets).")
    return value


def _walk(blocks: list):
    for b in blocks:
        yield b
        if b.get("type") in IF_TYPES:
            yield from _walk(_blocks(b.get("then", b.get("true")), "then"))
            yield from _walk(_blocks(b.get("else", b.get("false")), "else"))
        elif b.get("type") in SWITCH_TYPES:
            for c in b.get("cases") or []:
                yield from _walk(_blocks(c.get("steps") if isinstance(c, dict) else None, "cases"))
            yield from _walk(_blocks(b.get("default"), "default"))
        elif b.get("type") == "parallel":
            for br in b.get("branches") or []:
                yield from _walk(br if isinstance(br, list) else _blocks((br or {}).get("steps"), "branches"))
        yield from _walk(_blocks(b.get("on_error"), "on_error")) if b.get("type") in STEP_TYPES else iter(())


class _Compiler:
    def __init__(self):
        self.nodes: list[dict] = []
        self.edges: list[dict] = []
        self.used: set[str] = set()
        self.ids: dict[int, str] = {}  # id(block) -> final step id
        self.renamed: dict[str, str] = {}  # id written by the model -> final id (references are rewritten)

    # ------------------------------------------------------------------ ids
    def alloc(self, wanted: str) -> str:
        base, k = wanted[:ID_MAX], 2
        out = base
        while out in self.used:
            tail = f"_{k}"
            out, k = base[: ID_MAX - len(tail)] + tail, k + 1
        self.used.add(out)
        return out

    def allocate_all(self, blocks: list):
        for i, b in enumerate(_walk(blocks), 1):
            kind = (
                "decision"
                if b.get("type") in IF_TYPES
                else "choix"
                if b.get("type") in SWITCH_TYPES
                else "parallele"
                if b.get("type") == "parallel"
                else "etape"
            )
            if b.get("type") == "parallel":
                continue
            wanted = slug_id(b.get("id"), f"{kind}_{i}")
            final = self.alloc(wanted)
            self.ids[id(b)] = final
            if isinstance(b.get("id"), str) and b["id"] != final:
                self.renamed.setdefault(b["id"], final)

    # ------------------------------------------------------------------ edges
    def link(self, pending, nid: str):
        """Connect the exits waiting to continue to `nid`; several exits only meet on a join step."""
        if not pending:
            return
        srcs = list(dict.fromkeys(pending))
        if len(srcs) > 1:
            jid = self.alloc(f"jonction_{nid}")
            at = next(i for i, n in enumerate(self.nodes) if n["id"] == nid)
            self.nodes.insert(at, {"id": jid, "type": "join", "name": "Jonction"})
            for src, label in srcs:
                self.edge(src, jid, label)
            self.edge(jid, nid, None)
        else:
            self.edge(srcs[0][0], nid, srcs[0][1])

    def edge(self, src: str, dst: str, label):
        e = {"from": src, "to": dst, **({"label": label} if label else {})}
        if e not in self.edges:
            self.edges.append(e)

    # ------------------------------------------------------------------ blocks
    def sequence(self, blocks: list, pending):
        for b in blocks:
            pending = self.block(b, pending)
        return pending

    def block(self, b: dict, pending):
        t = b.get("type")
        if pending is not None and not pending:
            raise PlanError(
                f"L'étape « {b.get('name') or b.get('id') or t} » est inaccessible : elle suit une exception ou une "
                "décision dont toutes les branches se terminent. Place-la avant l'exception ou dans une branche."
            )
        if len(self.nodes) >= MAX_PLAN_STEPS * 2:
            raise PlanError(f"Le plan est trop grand (plus de {MAX_PLAN_STEPS} étapes).")
        if t == "parallel":
            return self.parallel(b, pending)
        if t in IF_TYPES:
            return self.decision(b, pending)
        if t in SWITCH_TYPES:
            return self.switch(b, pending)
        if t in STEP_TYPES:
            return self.step(b, pending)
        raise PlanError(
            f"Type d'étape inconnu « {t} » : utilise {', '.join(STEP_TYPES)}, if (décision), switch (choix multiple) ou parallel (en parallèle)."
        )

    def add(self, b: dict, ntype: str) -> dict:
        node = {k: v for k, v in b.items() if k not in STRUCTURE_KEYS and k != "id"}
        node.update(id=self.ids[id(b)], type=ntype)
        self.nodes.append(node)
        return node

    def step(self, b: dict, pending):
        node = self.add(b, b["type"])
        self.link(pending, node["id"])
        if b["type"] == "throw":
            return []  # the flow ends here
        out = [(node["id"], None)]
        handler = _blocks(b.get("on_error"), "on_error")
        if handler:
            out += self.sequence(handler, [(node["id"], "error")]) or []
        return out

    def decision(self, b: dict, pending):
        if not str(b.get("expr") or "").strip():
            raise PlanError(f"La décision « {b.get('name') or b.get('id') or 'if'} » n'a pas d'expression (expr).")
        yes = _blocks(b.get("then", b.get("true")), "then")
        no = _blocks(b.get("else", b.get("false")), "else")
        if not yes and not no:
            raise PlanError(f"La décision « {b.get('name') or b.get('id')} » n'a aucune branche (then / else).")
        node = self.add(b, "condition")
        self.link(pending, node["id"])
        out = self.sequence(yes, [(node["id"], "true")])
        return out + self.sequence(no, [(node["id"], "false")])

    def switch(self, b: dict, pending):
        name = b.get("name") or b.get("id") or "switch"
        cases = b.get("cases")
        if not isinstance(cases, list) or not cases or not all(isinstance(c, dict) for c in cases):
            raise PlanError(f"Le choix multiple « {name} » demande des cas (cases : liste de {{id, expr, steps}}).")
        if any(not str(c.get("expr") or "").strip() for c in cases):
            raise PlanError(f"Chaque cas du choix multiple « {name} » demande une expression (expr).")
        default = _blocks(b.get("default"), "default")
        node = self.add(b, "switch")
        ids, seen = [], set()
        for i, c in enumerate(cases, 1):
            cid = slug_id(c.get("id"), f"cas_{i}")
            if cid in RESERVED_LABELS or cid in seen:
                cid = slug_id(f"cas_{cid}_{i}", f"cas_{i}")
            seen.add(cid)
            ids.append(cid)
        node["cases"] = [{"id": cid, "expr": str(c["expr"]).strip()} for cid, c in zip(ids, cases, strict=True)]
        self.link(pending, node["id"])
        out: list = []
        for cid, c in zip(ids, cases, strict=True):
            branch = _blocks(c.get("steps"), "cases")
            out += self.sequence(branch, [(node["id"], cid)]) if branch else [(node["id"], cid)]
        out += self.sequence(default, [(node["id"], "default")]) if default else [(node["id"], "default")]
        return out

    def parallel(self, b: dict, pending):
        branches = [
            br if isinstance(br, list) else _blocks((br or {}).get("steps"), "branches")
            for br in b.get("branches") or []
        ]
        if len(branches) < 2 or any(not br for br in branches):
            raise PlanError(
                "parallel demande au moins deux branches (branches : liste de listes d'étapes), aucune vide."
            )
        if pending and len({p for p in pending}) > 1:  # one fork point: merge what is still waiting first
            jid = self.alloc("jonction_parallele")
            self.nodes.append({"id": jid, "type": "join", "name": "Jonction"})
            for src, label in dict.fromkeys(pending):
                self.edge(src, jid, label)
            pending = [(jid, None)]
        out: list = []
        for br in branches:
            out += self.sequence(br, pending)
        return out


def _rewrite(value, renamed: dict):
    """The model referred to a step by the id it wrote; ids are normalised, so references follow."""
    if isinstance(value, str):
        for old, new in renamed.items():
            value = re.sub(rf"(?<![\w.]){re.escape('steps.' + old)}(?![\w])", "steps." + new, value)
        return value
    if isinstance(value, list):
        return [_rewrite(v, renamed) for v in value]
    if isinstance(value, dict):
        return {k: _rewrite(v, renamed) for k, v in value.items()}
    return value


def compile_plan(plan, check_expressions: bool = True) -> dict:
    """Plan → raw graph ({inputs, variables, notes, nodes, edges, outputs}) for `sanitize_draft`. With
    `check_expressions` False an unreadable decision expression is kept as written: the validator reports it and a
    correction round handles it (used after the model was already asked once)."""
    if not isinstance(plan, dict):
        raise PlanError("Le plan doit être un objet JSON.")
    blocks = _blocks(plan.get("steps"), "steps")
    if not blocks:
        raise PlanError("Le plan n'a aucune étape (steps).")
    c = _Compiler()
    c.allocate_all(blocks)
    if sum(1 for _ in _walk(blocks)) > MAX_PLAN_STEPS:
        raise PlanError(f"Le plan est trop grand (plus de {MAX_PLAN_STEPS} étapes) : regroupe ou simplifie.")
    c.sequence(blocks, None)
    nodes = _rewrite(c.nodes, c.renamed) if c.renamed else c.nodes
    for n in nodes:  # an expression the platform could not read now costs one retry, not a repair round later
        if n["type"] == "switch" and check_expressions:
            for case in n.get("cases") or []:
                try:
                    parse(str(case["expr"]).strip().removeprefix("{{").removesuffix("}}"))
                except ExpressionError as exc:
                    raise PlanError(
                        f"Le choix « {n.get('name') or n['id']} », cas « {case['id']} » : expression invalide "
                        f"« {case['expr']} » ({exc}) Expression non reconnue : calculs simples (+ - * / %) et fonctions listées seulement ; "
                        "sinon calcule la valeur dans une étape script avant le choix."
                    ) from None
        if n["type"] == "condition" and check_expressions:
            try:
                parse(str(n["expr"]).strip().removeprefix("{{").removesuffix("}}"))
            except ExpressionError as exc:
                raise PlanError(
                    f"La décision « {n.get('name') or n['id']} » : expression invalide « {n['expr']} » ({exc}) "
                    "Une expression ne contient que des calculs simples (+ - * / %), les fonctions listées dans les règles, des chemins et "
                    "des comparaisons : calcule le reste dans une étape script AVANT la décision (sortie booléenne ou numérique), "
                    "puis compare cette sortie."
                ) from None
    graph = {"inputs": plan.get("inputs"), "nodes": nodes, "edges": c.edges, "outputs": plan.get("outputs")}
    if isinstance(plan.get("variables"), list):
        graph["variables"] = plan["variables"]
    notes = [{"text": n} if isinstance(n, str) else n for n in plan.get("notes") or [] if isinstance(n, (str, dict))]
    if notes:
        graph["notes"] = notes
    return graph
