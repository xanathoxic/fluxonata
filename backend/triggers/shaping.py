"""How a form field turns the reply of a web service into options (ADR 0026).

A reply is rarely a tidy list: it can be an OBJECT whose keys are the choices (`{"groups": {"group:dev": [...]}}`), it can be
big, and the choice of one field decides what the next one reads (`groups.{groupe}`). The designer describes the shaping as
DATA, with the same small expression language as the workflow steps, so that nothing here runs code of the designer's:

- `items_path`  where the items are; a `{champ}` in it is replaced by the value of another field of the form, as ONE key (a
                dot in the value never opens a second level);
- `items_as`    `list` (default) or `entries`: an object becomes a list of `{key, value}` (value = the key by default);
- `where`       an expression on `item`; only the items for which it is true are kept (an item it cannot read is left out);
- `label_expr`  an expression on `item` that builds the label (falls back to the value);
- `sort`        `label` or `label_desc`.

The visitor of a public form never chooses an expression: only the values of the declared placeholders, as short texts."""

import re

from workflows import expressions as ex

SHAPING_KEYS = {"items_as", "where", "label_expr", "sort"}
ITEMS_AS = ("list", "entries")
SORTS = ("label", "label_desc")
MAX_EXPR = 300
MAX_SCAN = 10_000  # items examined at most, whatever the size of the reply
_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]{0,63})\}")


def placeholders(path) -> list[str]:
    return _PLACEHOLDER.findall(str(path or ""))


def dig(value, path: str, params: dict | None = None):
    """The part of `value` at the dotted `path`. Each `{champ}` takes the value of that parameter as part of ONE key. None when
    the way does not exist or a placeholder has no value."""
    params = params or {}
    for segment in [s for s in str(path or "").split(".") if s]:
        key = _PLACEHOLDER.sub(lambda m: str(params.get(m.group(1), "\0")), segment)
        if "\0" in key:
            return None
        if isinstance(value, dict):
            value = value.get(key)
        elif isinstance(value, list) and key.isdigit() and int(key) < len(value):
            value = value[int(key)]
        else:
            return None
    return value


def _compile(src: str, what: str):
    if not isinstance(src, str) or not src.strip() or len(src) > MAX_EXPR:
        raise ValueError(f"{what} : une expression de {MAX_EXPR} caractères au plus est attendue.")
    try:
        node = ex.parse(src)
    except ex.ExpressionError as exc:
        raise ValueError(f"{what} : {exc}") from None
    if any(p[0] != "item" for p in ex.paths_in(node)):
        raise ValueError(f"{what} : seul « item » est connu ici (ex. item.nom).")
    return node


def check(cfg: dict, path: str) -> dict[str, str]:
    """{path: message} for the shaping keys of a reading (options only); {} when they are well formed."""
    out: dict[str, str] = {}
    declared = cfg.get("params", [])
    for name in placeholders(cfg.get("items_path")):
        if name not in declared:
            out[f"{path}.items_path"] = f"« {{{name}}} n'est pas dans les champs lus : ajoutez-le aux paramètres."
    if cfg.get("items_as", "list") not in ITEMS_AS:
        out[f"{path}.items_as"] = "Forme de la liste : « list » ou « entries »."
    if cfg.get("sort", "") not in ("", *SORTS):
        out[f"{path}.sort"] = "Tri : « label » ou « label_desc »."
    for name in ("where", "label_expr"):
        if cfg.get(name):
            try:
                _compile(cfg[name], name)
            except ValueError as exc:
                out[f"{path}.{name}"] = str(exc)
    return out


def items(body, cfg: dict, params: dict | None = None) -> list:
    """The items of a reply as a list (an object becomes `{key, value}` entries when `items_as` says so); ValueError otherwise."""
    root = dig(body, cfg["items_path"], params) if cfg.get("items_path") else body
    if cfg.get("items_as") == "entries":
        if not isinstance(root, dict):
            raise ValueError("items")
        return [{"key": k, "value": v} for k, v in root.items()][:MAX_SCAN]
    if not isinstance(root, list):
        raise ValueError("items")
    return root[:MAX_SCAN]


def compiled(cfg: dict) -> tuple:
    """(where, label_expr) as syntax trees, None when absent."""
    return tuple(_compile(cfg[n], n) if cfg.get(n) else None for n in ("where", "label_expr"))


def keeps(where, item) -> bool:
    if where is None:
        return True
    try:
        return bool(ex.evaluate_ast(where, {"item": item}))
    except ex.ExpressionError:
        return False


def label_of(expr, item, default):
    if expr is None:
        return default
    try:
        value = ex.evaluate_ast(expr, {"item": item})
    except ex.ExpressionError:
        return default
    return default if value is None or isinstance(value, (dict, list)) else value


def ordered(options: list[dict], sort: str) -> list[dict]:
    if sort not in SORTS:
        return options
    return sorted(options, key=lambda o: str(o["label"]).casefold(), reverse=sort == "label_desc")
