"""Restricted expression language (no eval, no attribute access to Python objects).

Grammar: or > and > not > comparison (== != < <= > >= in contains) > primary
primary: number | 'string' | true | false | null | path | func(args) | (expr)
path: ident(.ident | [int])*     functions: len, lower, upper
Templates: "{{ expr }}" inside strings (whole-string template keeps the value's type).
"""

import re

MAX_LEN = 500
MAX_DEPTH = 20
FUNCS = {
    "len": len,
    "lower": lambda s: s.lower(),
    "upper": lambda s: s.upper(),
}
KEYWORDS = {"and", "or", "not", "in", "contains", "true", "false", "null"}
TOKEN_RE = re.compile(
    r"""\s*(?:
    (?P<num>\d+(?:\.\d+)?)|
    (?P<str>'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*")|
    (?P<op>==|!=|<=|>=|<|>)|
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
        left = self.primary()
        kind, val = self.peek()
        if kind == "op" or (kind == "id" and val in ("in", "contains")):
            self.take()
            return ("cmp", val, left, self.primary())
        return left

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
                    if val not in FUNCS:
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


def paths_in(node) -> list[tuple]:
    """All variable paths referenced by an AST (for static validation)."""
    kind = node[0]
    if kind == "path":
        return [node[1]]
    if kind in ("or", "and"):
        return paths_in(node[1]) + paths_in(node[2])
    if kind == "not":
        return paths_in(node[1])
    if kind == "cmp":
        return paths_in(node[2]) + paths_in(node[3])
    if kind == "call":
        return [p for a in node[2] for p in paths_in(a)]
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
    if kind == "call":
        args = [evaluate_ast(a, ctx) for a in node[2]]
        try:
            return FUNCS[node[1]](*args)
        except Exception as exc:  # noqa: BLE001
            raise ExpressionError(f"Appel de {node[1]}() impossible : {exc}") from None
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
