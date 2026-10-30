"""Restricted expression language (no eval, no attribute access to Python objects).

Grammar: or > and > not > comparison (== != < <= > >= in contains) > sum (+ -) > product (* / %) > unary - > primary
primary: number | 'string' | true | false | null | path | func(args) | (expr)
path: ident(.ident | [int])*
functions: len lower upper trim str number round abs min max sum join split replace starts_with ends_with first last
keys unique sort date_add date_diff date_format, and default(valeur, repli). Roots: inputs, vars, steps, run, item (transforms only). `+` adds numbers or joins two texts; no operation on booleans or null.
Templates: "{{ expr }}" inside strings (whole-string template keeps the value's type).
"""

import json
import re
from datetime import UTC, datetime, timedelta

MAX_LEN = 500
MAX_DEPTH = 20


def _number(v):
    if isinstance(v, bool) or v is None:
        raise ValueError("nombre attendu")
    if isinstance(v, (int, float)):
        return v
    f = float(str(v).strip().replace(",", "."))
    return int(f) if f.is_integer() and "." not in str(v) and "e" not in str(v).lower() else f


def _list(v):
    if not isinstance(v, list):
        raise ValueError("liste attendue")
    return v


def _min_max(fn):
    def run(*a):
        vals = _list(a[0]) if len(a) == 1 else list(a)
        return fn(vals)

    return run


FUNCS = {
    "len": len,
    "lower": lambda s: s.lower(),
    "upper": lambda s: s.upper(),
    "trim": lambda s: s.strip(),
    "str": lambda v: "" if v is None else (str(v).lower() if isinstance(v, bool) else str(v)),
    "number": _number,
    "round": lambda x, n=0: round(_number(x), int(n)) if int(n) > 0 else int(round(_number(x))),
    "abs": lambda x: abs(_number(x)),
    "min": _min_max(min),
    "max": _min_max(max),
    "sum": lambda items: sum(_number(x) for x in _list(items)),
    "join": lambda items, sep="": str(sep).join("" if x is None else str(x) for x in _list(items)),
    "split": lambda s, sep=",": s.split(sep),
    "replace": lambda s, a, b: s.replace(a, b),
    "starts_with": lambda s, p: s.startswith(p),
    "ends_with": lambda s, p: s.endswith(p),
    "first": lambda items: _list(items)[0],
    "last": lambda items: _list(items)[-1],
    "keys": lambda o: sorted(o) if isinstance(o, dict) else (_ for _ in ()).throw(ValueError("objet attendu")),
    "unique": lambda items: list(dict.fromkeys(_list(items))),
    "sort": lambda items: sorted(_list(items)),
}
MAX_TEXT = 100_000
_UNITS = {"seconds": 1, "minutes": 60, "hours": 3600, "days": 86400, "weeks": 604800}


def _dt(v):
    if not isinstance(v, str):
        raise ValueError("date ISO 8601 attendue (texte)")
    d = datetime.fromisoformat(v.strip().replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def _unit(u):
    if u not in _UNITS:
        raise ValueError("unité : seconds, minutes, hours, days ou weeks")
    return _UNITS[u]


def _date_diff(a, b, unit="days"):
    r = (_dt(a) - _dt(b)).total_seconds() / _unit(unit)
    return int(r) if r == int(r) else r


FUNCS.update(
    {
        "date_add": lambda v, n, unit="days": (_dt(v) + timedelta(seconds=_number(n) * _unit(unit))).isoformat(),
        "date_diff": _date_diff,
        "date_format": lambda v, fmt="%Y-%m-%d": _dt(v).strftime(fmt),
    }
)
# default(value, fallback): the value, or the fallback when it does not exist (a step that did not run on this path) or is null
SPECIAL = {"default"}
KEYWORDS = {"and", "or", "not", "in", "contains", "true", "false", "null"}
TOKEN_RE = re.compile(
    r"""\s*(?:
    (?P<num>\d+(?:\.\d+)?)|
    (?P<str>'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*")|
    (?P<op>==|!=|<=|>=|<|>)|
    (?P<ar>[-+*/%])|
    (?P<id>[A-Za-z_][A-Za-z0-9_]*)|
    (?P<p>[().\[\],])
    )""",
    re.X,
)
TEMPLATE_RE = re.compile(r"\{\{(.*?)\}\}", re.S)
WHOLE_RE = re.compile(r"^\s*\{\{((?:(?!\}\}).)*)\}\}\s*$", re.S)


class ExpressionError(ValueError):
    pass


def tokenize(src: str) -> list[tuple[str, str]]:
    if len(src) > MAX_LEN:
        raise ExpressionError("Expression trop longue.")
    pos, out = 0, []
    while pos < len(src):
        if src[pos:].strip() == "":
            break
        m = TOKEN_RE.match(src, pos)
        if not m or m.end() == pos:
            raise ExpressionError(f"Caractère inattendu près de « {src[pos : pos + 10].strip()} ».")
        pos = m.end()
        kind = m.lastgroup
        out.append((kind, m.group(kind)))
    return out


class Parser:
    def __init__(self, src: str):
        self.toks = tokenize(src)
        self.i = 0
        self.depth = 0

    def peek(self):
        return self.toks[self.i] if self.i < len(self.toks) else (None, None)

    def take(self):
        t = self.peek()
        self.i += 1
        return t

    def is_kw(self, word):
        return self.peek() == ("id", word)

    def parse(self):
        node = self.or_()
        if self.i != len(self.toks):
            raise ExpressionError(f"Élément inattendu : « {self.peek()[1]} ».")
        return node

    def or_(self):
        node = self.and_()
        while self.is_kw("or"):
            self.take()
            node = ("or", node, self.and_())
        return node

    def and_(self):
        node = self.not_()
        while self.is_kw("and"):
            self.take()
            node = ("and", node, self.not_())
        return node

    def not_(self):
        if self.is_kw("not"):
            self.take()
            return ("not", self.not_())
        return self.cmp()

    def cmp(self):
        left = self.sum_()
        kind, val = self.peek()
        if kind == "op" or (kind == "id" and val in ("in", "contains")):
            self.take()
            return ("cmp", val, left, self.sum_())
        return left

    def sum_(self):
        node = self.product()
        while self.peek()[0] == "ar" and self.peek()[1] in "+-":
            op = self.take()[1]
            node = ("arith", op, node, self.product())
        return node

    def product(self):
        node = self.unary()
        while self.peek()[0] == "ar" and self.peek()[1] in "*/%":
            op = self.take()[1]
            node = ("arith", op, node, self.unary())
        return node

    def unary(self):
        if self.peek() == ("ar", "-"):
            self.take()
            self.depth += 1
            if self.depth > MAX_DEPTH:
                raise ExpressionError("Expression trop imbriquée.")
            try:
                return ("arith", "-", ("lit", 0), self.unary())
            finally:
                self.depth -= 1
        return self.primary()

    def primary(self):
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise ExpressionError("Expression trop imbriquée.")
        try:
            kind, val = self.take()
            if kind == "num":
                return ("lit", float(val) if "." in val else int(val))
            if kind == "str":
                return ("lit", bytes(val[1:-1], "utf-8").decode("unicode_escape") if "\\" in val else val[1:-1])
            if kind == "p" and val == "(":
                node = self.or_()
                if self.take() != ("p", ")"):
                    raise ExpressionError("Parenthèse fermante manquante.")
                return node
            if kind == "id":
                if val in ("true", "false", "null"):
                    return ("lit", {"true": True, "false": False, "null": None}[val])
                if val in KEYWORDS:
                    raise ExpressionError(f"Mot réservé « {val} » mal placé.")
                if self.peek() == ("p", "("):
                    if val not in FUNCS and val not in SPECIAL:
                        raise ExpressionError(f"Fonction inconnue : {val}.")
                    self.take()
                    args = []
                    if self.peek() != ("p", ")"):
                        args.append(self.or_())
                        while self.peek() == ("p", ","):
                            self.take()
                            args.append(self.or_())
                    if self.take() != ("p", ")"):
                        raise ExpressionError("Parenthèse fermante manquante.")
                    if val == "default" and len(args) != 2:
                        raise ExpressionError("default(valeur, repli) attend deux arguments.")
                    return ("call", val, args)
                return self.path(val)
            raise ExpressionError("Expression incomplète.")
        finally:
            self.depth -= 1

    def path(self, first):
        parts = [first]
        while True:
            if self.peek() == ("p", "."):
                self.take()
                kind, val = self.take()
                if kind != "id":
                    raise ExpressionError("Nom attendu après « . ».")
                parts.append(val)
            elif self.peek() == ("p", "["):
                self.take()
                kind, val = self.take()
                if kind != "num" or "." in val:
                    raise ExpressionError("Index entier attendu entre crochets.")
                if self.take() != ("p", "]"):
                    raise ExpressionError("Crochet fermant manquant.")
                parts.append(int(val))
            else:
                return ("path", tuple(parts))


def parse(src: str):
    return Parser(src).parse()


def paths_in(node, strict: bool = False) -> list[tuple]:
    """All variable paths referenced by an AST (for static validation). `strict` leaves out the paths that
    default(…) protects: they may be missing without making the expression fail."""
    kind = node[0]
    if kind == "path":
        return [node[1]]
    if kind in ("or", "and"):
        return paths_in(node[1], strict) + paths_in(node[2], strict)
    if kind == "not":
        return paths_in(node[1], strict)
    if kind in ("cmp", "arith"):
        return paths_in(node[2], strict) + paths_in(node[3], strict)
    if kind == "call":
        args = node[2][1:] if strict and node[1] == "default" else node[2]
        return [p for a in args for p in paths_in(a, strict)]
    return []


def _lookup(parts, ctx):
    cur = ctx
    for p in parts:
        try:
            cur = cur[p]
        except (KeyError, IndexError, TypeError):
            raise ExpressionError(f"Valeur introuvable : {'.'.join(str(x) for x in parts)}") from None
    return cur


def evaluate_ast(node, ctx):
    kind = node[0]
    if kind == "lit":
        return node[1]
    if kind == "path":
        return _lookup(node[1], ctx)
    if kind == "or":
        return bool(evaluate_ast(node[1], ctx)) or bool(evaluate_ast(node[2], ctx))
    if kind == "and":
        return bool(evaluate_ast(node[1], ctx)) and bool(evaluate_ast(node[2], ctx))
    if kind == "not":
        return not evaluate_ast(node[1], ctx)
    if kind == "call" and node[1] == "default":
        try:
            value = evaluate_ast(node[2][0], ctx)
        except ExpressionError:
            value = None
        return evaluate_ast(node[2][1], ctx) if value is None else value
    if kind == "call":
        args = [evaluate_ast(a, ctx) for a in node[2]]
        try:
            return FUNCS[node[1]](*args)
        except Exception as exc:  # noqa: BLE001
            raise ExpressionError(f"Appel de {node[1]}() impossible : {exc}") from None
    if kind == "arith":
        op, a, b = node[1], evaluate_ast(node[2], ctx), evaluate_ast(node[3], ctx)
        if op == "+" and isinstance(a, str) and isinstance(b, str):
            if len(a) + len(b) > MAX_TEXT:
                raise ExpressionError("Texte trop long.")
            return a + b
        if (
            isinstance(a, bool)
            or isinstance(b, bool)
            or not isinstance(a, (int, float))
            or not isinstance(b, (int, float))
        ):
            raise ExpressionError(
                f"Calcul impossible : « {op} » attend deux nombres (ou deux textes pour +), "
                f"pas {type(a).__name__} et {type(b).__name__}."
            )
        try:
            if op == "+":
                return a + b
            if op == "-":
                return a - b
            if op == "*":
                return a * b
            if op == "/":
                return a / b
            return a % b
        except ZeroDivisionError:
            raise ExpressionError("Division par zéro.") from None
    if kind == "cmp":
        op, a, b = node[1], evaluate_ast(node[2], ctx), evaluate_ast(node[3], ctx)
        try:
            if op == "==":
                return a == b
            if op == "!=":
                return a != b
            if op == "in":
                return a in b
            if op == "contains":
                return b in a
            return {"<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[op]
        except TypeError:
            raise ExpressionError(f"Comparaison impossible entre {type(a).__name__} et {type(b).__name__}.") from None
    raise ExpressionError("Expression invalide.")


def evaluate(src: str, ctx: dict):
    return evaluate_ast(parse(src), ctx)


def coerce_structured(value, schema: dict):
    """A parameter typed array/object that holds JSON text (typed in a text box, or saved so by an older editor) is that
    structure: the templates inside it are resolved afterwards, element by element, and never spliced into JSON text."""
    want = (schema or {}).get("type")
    if isinstance(value, str) and want in ("array", "object") and not WHOLE_RE.match(value):
        try:
            parsed = json.loads(value)
        except ValueError:
            return value
        if isinstance(parsed, list if want == "array" else dict):
            return parsed
    return value


def coerce_inputs(inputs: dict, schema: dict) -> dict:
    props = (schema or {}).get("properties", {})
    return {k: coerce_structured(v, props.get(k, {})) for k, v in (inputs or {}).items()}


def resolve(value, ctx: dict):
    """Resolve templates in any JSON value."""
    if isinstance(value, str):
        m = WHOLE_RE.match(value)
        if m:
            return evaluate(m.group(1), ctx)
        return TEMPLATE_RE.sub(lambda mm: _to_text(evaluate(mm.group(1), ctx)), value)
    if isinstance(value, list):
        return [resolve(v, ctx) for v in value]
    if isinstance(value, dict):
        return {k: resolve(v, ctx) for k, v in value.items()}
    return value


def _to_text(v) -> str:
    return "" if v is None else str(v)


def template_expressions(value) -> list[str]:
    """Raw expression sources found in templates of a JSON value."""
    if isinstance(value, str):
        return [m.group(1) for m in TEMPLATE_RE.finditer(value)]
    if isinstance(value, list):
        return [e for v in value for e in template_expressions(v)]
    if isinstance(value, dict):
        return [e for v in value.values() for e in template_expressions(v)]
    return []
