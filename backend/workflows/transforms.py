"""« Transformer » steps: data shaping without code. Pure, bounded and deterministic: the same inputs give the same output.

A transform reads a list (`items`, an expression such as `steps.appel.output.body`) and produces `{items, count}`
(`set` produces its own fields). Per-item expressions see the current element as `item` (e.g. `item.montant > 100`).
Operations: set, map, filter, sort, group, unique, merge, limit."""

from .expressions import ExpressionError, evaluate, resolve

OPS = ("set", "map", "filter", "sort", "group", "unique", "merge", "limit", "split", "chunk")
AGGREGATES = ("count", "sum", "min", "max", "avg", "first", "list")
MAX_ITEMS = 5000
MAX_GROUPS = 1000
PER_ITEM = {
    "map": ("fields",),
    "filter": ("where",),
    "sort": ("by",),
    "group": ("by", "aggregates"),
    "unique": ("by",),
    "merge": ("left_by", "right_by"),
}
LIST_OPS = ("map", "filter", "sort", "group", "unique", "merge", "limit", "chunk")


class TransformError(ValueError):
    pass


def _items(src, ctx, label="items") -> list:
    if not isinstance(src, str) or not src.strip():
        raise TransformError(f"« {label} » : indiquez la liste à traiter (ex. steps.appel.output.body).")
    try:
        value = evaluate(src.strip().removeprefix("{{").removesuffix("}}"), ctx)
    except ExpressionError as exc:
        raise TransformError(f"« {label} » : {exc}") from None
    if not isinstance(value, list):
        raise TransformError(f"« {label} » doit être une liste, pas {type(value).__name__}.")
    if len(value) > MAX_ITEMS:
        raise TransformError(f"Liste trop longue ({len(value)} éléments, {MAX_ITEMS} au plus).")
    return value


def _ev(expr, ctx, item):
    try:
        return evaluate(str(expr), {**ctx, "item": item})
    except ExpressionError as exc:
        raise TransformError(str(exc)) from None


def _hashable(v):
    return repr(v) if isinstance(v, (dict, list)) else v


def _num(v, expr):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise TransformError(f"« {expr} » doit donner un nombre pour ce calcul (reçu : {type(v).__name__}).")
    return v


def apply(node: dict, ctx: dict) -> dict:
    op = node.get("op")
    if op not in OPS:
        raise TransformError(f"Opération inconnue : {op}.")
    if op == "set":
        fields = node.get("fields")
        if not isinstance(fields, dict) or not fields:
            raise TransformError("Définissez au moins un champ.")
        try:
            return dict(resolve(fields, ctx))
        except ExpressionError as exc:
            raise TransformError(str(exc)) from None
    if op == "split":
        return _split(node, ctx)
    items = _items(node.get("items"), ctx)
    if op == "chunk":
        size = node.get("size")
        if not isinstance(size, int) or not 1 <= size <= MAX_ITEMS:
            raise TransformError("« size » : taille des lots, entier de 1 à 5000.")
        out = [items[i : i + size] for i in range(0, len(items), size)]
    elif op == "map":
        fields = node.get("fields")
        if not isinstance(fields, dict) or not fields:
            raise TransformError("Définissez au moins un champ pour chaque élément.")
        try:
            out = [dict(resolve(fields, {**ctx, "item": it})) for it in items]
        except ExpressionError as exc:
            raise TransformError(str(exc)) from None
    elif op == "filter":
        where = node.get("where")
        if not str(where or "").strip():
            raise TransformError("Indiquez la condition de filtre (ex. item.montant > 100).")
        out = [it for it in items if bool(_ev(where, ctx, it))]
    elif op == "sort":
        by = node.get("by") or "item"
        keyed = [(_ev(by, ctx, it), it) for it in items]
        kinds = {type(k) if not isinstance(k, (int, float)) or isinstance(k, bool) else float for k, _ in keyed}
        if len(kinds) > 1:
            raise TransformError("Le tri compare des valeurs de types différents (nombres et textes mélangés).")
        try:
            keyed.sort(key=lambda kv: kv[0], reverse=node.get("order") == "desc")
        except TypeError:
            raise TransformError("Ces valeurs ne peuvent pas être triées.") from None
        out = [it for _, it in keyed]
    elif op == "unique":
        by = node.get("by")
        seen, out = set(), []
        for it in items:
            k = _hashable(_ev(by, ctx, it) if by else it)
            if k not in seen:
                seen.add(k)
                out.append(it)
    elif op == "limit":
        count = node.get("count")
        skip = node.get("skip", 0)
        if not isinstance(count, int) or count < 0 or not isinstance(skip, int) or skip < 0:
            raise TransformError("« count » et « skip » sont des entiers positifs.")
        out = items[skip : skip + count]
    elif op == "group":
        out = _group(node, ctx, items)
    else:  # merge
        out = _merge(node, ctx, items)
    return {"items": out, "count": len(out)}


def _split(node, ctx) -> dict:
    """Text -> list of pieces (`separator`, default a comma; pieces trimmed, empty ones dropped unless `keep_empty`)."""
    try:
        text = evaluate(_bare(node.get("text", "")), ctx)
    except ExpressionError as exc:
        raise TransformError(str(exc)) from None
    if not isinstance(text, str):
        raise TransformError(f"« text » doit être un texte, pas {type(text).__name__}.")
    sep = node.get("separator", ",")
    if not isinstance(sep, str) or sep == "":
        raise TransformError("Le séparateur ne peut pas être vide.")
    sep = {"\\n": "\n", "\\t": "\t"}.get(sep, sep)
    parts = [p.strip() for p in text.split(sep)]
    if not node.get("keep_empty"):
        parts = [p for p in parts if p]
    if len(parts) > MAX_ITEMS:
        raise TransformError(f"Trop d'éléments ({MAX_ITEMS} au plus).")
    return {"items": parts, "count": len(parts)}


def _group(node, ctx, items):
    by = node.get("by")
    if not str(by or "").strip():
        raise TransformError("Indiquez la clé de regroupement (ex. item.service).")
    aggs = node.get("aggregates") or []
    groups: dict = {}
    for it in items:
        key = _ev(by, ctx, it)
        groups.setdefault(_hashable(key), (key, []))[1].append(it)
        if len(groups) > MAX_GROUPS:
            raise TransformError(f"Trop de groupes ({MAX_GROUPS} au plus).")
    out = []
    for key, members in groups.values():
        row: dict = {"key": key, "count": len(members), "items": members}
        for a in aggs:
            name, fn, expr = a.get("name"), a.get("fn"), a.get("expr", "item")
            if not name or fn not in AGGREGATES:
                raise TransformError(f"Agrégat invalide : {a}.")
            values = [_ev(expr, ctx, m) for m in members]
            if fn == "count":
                row[name] = len(values)
            elif fn == "list":
                row[name] = values
            elif fn == "first":
                row[name] = values[0]
            else:
                nums = [_num(v, expr) for v in values]
                row[name] = {"sum": sum, "min": min, "max": max, "avg": lambda x: sum(x) / len(x)}[fn](nums)
        out.append(row)
    return out


def _merge(node, ctx, left):
    mode = node.get("merge_mode", "inner")
    right = _items(node.get("right"), ctx, "right")
    if mode == "append":
        return (left + right)[:MAX_ITEMS]
    if mode not in ("inner", "left"):
        raise TransformError("Mode de fusion : inner, left ou append.")
    lb, rb = node.get("left_by"), node.get("right_by")
    if not str(lb or "").strip() or not str(rb or "").strip():
        raise TransformError("Indiquez la clé de chaque côté (ex. item.id).")
    index: dict = {}
    for r in right:
        index.setdefault(_hashable(_ev(rb, ctx, r)), []).append(r)
    out = []
    for it in left:
        matches = index.get(_hashable(_ev(lb, ctx, it)), [])
        for m in matches or ([None] if mode == "left" else []):
            if isinstance(it, dict) and (m is None or isinstance(m, dict)):
                out.append({**it, **(m or {})})
            else:
                out.append({"left": it, "right": m})
    return out[:MAX_ITEMS]


def _bare(src) -> str:
    return str(src).strip().removeprefix("{{").removesuffix("}}").strip()


def expressions(node: dict) -> tuple[list[str], list[str]]:
    """(plain expressions, per-item expressions) a transform evaluates — for the validator."""
    from .expressions import template_expressions

    op = node.get("op")
    plain: list[str] = []
    per: list[str] = []
    if op == "set":
        plain += template_expressions(node.get("fields") or {})
    if op == "split" and str(node.get("text") or "").strip():
        plain.append(_bare(node["text"]))
    if op in LIST_OPS and str(node.get("items") or "").strip():
        plain.append(_bare(node["items"]))
    if op == "map":
        per += template_expressions(node.get("fields") or {})
    if op == "filter" and str(node.get("where") or "").strip():
        per.append(_bare(node["where"]))
    if op in ("sort", "unique") and str(node.get("by") or "").strip():
        per.append(_bare(node["by"]))
    if op == "group":
        if str(node.get("by") or "").strip():
            per.append(_bare(node["by"]))
        per += [_bare(a.get("expr", "item")) for a in node.get("aggregates") or [] if isinstance(a, dict)]
    if op == "merge":
        if str(node.get("right") or "").strip():
            plain.append(_bare(node["right"]))
        per += [_bare(node[k]) for k in ("left_by", "right_by") if str(node.get(k) or "").strip()]
    return plain, per


def output_fields(node: dict):
    """Static shape of the output, for the validator and the editor: field -> JSON-schema-ish, or None when unknown."""
    if node.get("op") == "set":
        return {k: {} for k in (node.get("fields") or {})}
    return {"items": {"type": "array"}, "count": {"type": "integer"}}
