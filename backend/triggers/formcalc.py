"""Calculated fields: a formula over the values of the other fields of a form.

`{prix} * {quantite}`, `{prenom} & " " & upper({nom})`, `if({age} >= 18, "majeur", "mineur")`, `round({ttc} / 1.2, 2)`.
The language is closed (numbers, text, true/false, `{champ}`, operators, a fixed list of functions): it can reach nothing but the values of
the form. The browser runs the very same language (`frontend/src/forms/calc.ts`) and both are tested on `fixtures/form_calc_cases.json`;
the server stays the judge: the value of a calculated field is always the one it computes, whatever was sent.

An operand that does not make sense (an empty field in a sum, a text in a product, a division by zero) gives *no value* (`None`): the
field stays empty rather than showing a wrong number. Every number is a float inside; a result without decimals is returned as an int.
"""

import math
import operator
import re
from collections.abc import Callable
from datetime import date
from functools import lru_cache, partial

from .formrules import num_of, text_of

MAX_FORMULA = 500
MAX_NODES = 100
MAX_DEPTH = 20
MAX_TEXT = 2000
LIMIT = 1e15

# name: (fewest arguments, most arguments; None = any number)
FUNCTIONS: dict[str, tuple[int, int | None]] = {
    "if": (3, 3),
    "and": (1, None),
    "or": (1, None),
    "not": (1, 1),
    "round": (1, 2),
    "floor": (1, 1),
    "ceil": (1, 1),
    "abs": (1, 1),
    "min": (1, None),
    "max": (1, None),
    "sum": (1, None),
    "avg": (1, None),
    "len": (1, 1),
    "upper": (1, 1),
    "lower": (1, 1),
    "trim": (1, 1),
    "concat": (1, None),
    "left": (2, 2),
    "right": (2, 2),
    "number": (1, 1),
    "text": (1, 1),
    "is_empty": (1, 1),
    "coalesce": (1, None),
    "days_between": (2, 2),
    "count": (1, 1),
    "sum_col": (2, 2),
}
COMPUTABLE = {"text", "textarea", "number", "email", "date"}

_ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_TOKEN = re.compile(
    r"""\s*(?:(?P<num>\d+(?:\.\d+)?)|"(?P<dq>[^"]*)"|'(?P<sq>[^']*)'|\{(?P<ref>[A-Za-z_][A-Za-z0-9_]{0,63})\}"""
    r"""|(?P<id>[A-Za-z_][A-Za-z0-9_]*)|(?P<op><=|>=|<>|!=|[=<>+\-*/%&(),]))"""
)


class FormulaError(ValueError):
    pass


def _tokens(text: str) -> list[tuple[str, object]]:
    out: list[tuple[str, object]] = []
    pos = 0
    while pos < len(text):
        if text[pos:].strip() == "":
            break
        m = _TOKEN.match(text, pos)
        if not m or m.end() == pos:
            raise FormulaError(f"Caractère inattendu près de « {text[pos : pos + 12].strip()} ».")
        pos = m.end()
        if m.group("num") is not None:
            out.append(("num", float(m.group("num"))))
        elif m.group("dq") is not None:
            out.append(("str", m.group("dq")))
        elif m.group("sq") is not None:
            out.append(("str", m.group("sq")))
        elif m.group("ref") is not None:
            out.append(("ref", m.group("ref")))
        elif m.group("id") is not None:
            out.append(("id", m.group("id")))
        else:
            out.append(("op", m.group("op")))
    return out


class _Parser:
    def __init__(self, tokens: list[tuple[str, object]]):
        self.toks = tokens
        self.i = 0
        self.nodes = 0
        self.depth = 0

    def peek(self) -> tuple[str, object] | None:
        return self.toks[self.i] if self.i < len(self.toks) else None

    def take_op(self, *ops: str) -> str | None:
        t = self.peek()
        if t and t[0] == "op" and t[1] in ops:
            self.i += 1
            return str(t[1])
        return None

    def node(self, n: tuple):
        self.nodes += 1
        if self.nodes > MAX_NODES:
            raise FormulaError(f"Formule trop longue ({MAX_NODES} éléments au plus).")
        return n

    def parse(self):
        node = self.expr()
        if self.peek() is not None:
            raise FormulaError("Texte inattendu après la fin de la formule.")
        return node

    def expr(self):
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise FormulaError("Parenthèses trop imbriquées.")
        node = self.cmp()
        self.depth -= 1
        return node

    def cmp(self):
        left = self.concat()
        while op := self.take_op("=", "<>", "!=", "<=", ">=", "<", ">"):
            left = self.node(("bin", "<>" if op == "!=" else op, left, self.concat()))
        return left

    def concat(self):
        left = self.add()
        while self.take_op("&"):
            left = self.node(("bin", "&", left, self.add()))
        return left

    def add(self):
        left = self.mul()
        while op := self.take_op("+", "-"):
            left = self.node(("bin", op, left, self.mul()))
        return left

    def mul(self):
        left = self.unary()
        while op := self.take_op("*", "/", "%"):
            left = self.node(("bin", op, left, self.unary()))
        return left

    def unary(self):
        if self.take_op("-"):
            return self.node(("neg", self.unary()))
        return self.primary()

    def call(self, name: str):
        """`name(` was read: the arguments up to `)`, checked against the function's arity."""
        if name not in FUNCTIONS:
            raise FormulaError(f"La fonction « {name} » n'existe pas.")
        args = []
        if not self.take_op(")"):
            while True:
                args.append(self.expr())
                if self.take_op(")"):
                    break
                if not self.take_op(","):
                    raise FormulaError(f"« , » ou « ) » attendu dans {name}(…).")
        low, high = FUNCTIONS[name]
        if len(args) < low or (high is not None and len(args) > high):
            wanted = str(low) if high == low else f"{low} à {high}" if high else f"au moins {low}"
            raise FormulaError(f"{name}(…) attend {wanted} valeur(s).")
        return self.node(("call", name, args))

    def word(self, name: str):
        """A bare name: `true`/`false`, or a function call."""
        if name in ("true", "false"):
            return self.node(("bool", name == "true"))
        if not self.take_op("("):
            raise FormulaError(f"« {name} » n'est pas connu : un champ s'écrit {{champ}}, un texte entre guillemets.")
        return self.call(name)

    def primary(self):
        t = self.peek()
        if t is None:
            raise FormulaError("La formule se termine trop tôt.")
        self.i += 1
        kind, val = t
        if kind in ("num", "str", "ref"):
            return self.node((kind, val))
        if kind == "id":
            return self.word(str(val))
        if val == "(":
            node = self.expr()
            if not self.take_op(")"):
                raise FormulaError("Parenthèse fermante manquante.")
            return node
        raise FormulaError(f"« {val} » n'est pas attendu ici.")


@lru_cache(maxsize=512)
def parse(formula: str):
    if not isinstance(formula, str) or not formula.strip():
        raise FormulaError("La formule est vide.")
    if len(formula) > MAX_FORMULA:
        raise FormulaError(f"{MAX_FORMULA} caractères au plus.")
    return _Parser(_tokens(formula)).parse()


def refs(formula) -> set[str]:
    """Keys of the fields a formula reads (nothing for an unreadable formula: it is refused when the definition is checked)."""
    if not isinstance(formula, str):
        return set()
    try:
        return _refs(parse(formula))
    except FormulaError:
        return set()


def _refs(node) -> set[str]:
    kind = node[0]
    if kind == "ref":
        return {node[1]}
    if kind == "neg":
        return _refs(node[1])
    if kind == "bin":
        return _refs(node[2]) | _refs(node[3])
    if kind == "call":
        return set().union(*(_refs(a) for a in node[2])) if node[2] else set()
    return set()


def check(formula, others: dict[str, dict]) -> str:
    """ "" when the formula is valid, else a message in French. `others`: the data fields it may read."""
    try:
        node = parse(formula)
    except FormulaError as exc:
        return str(exc)
    for key in sorted(_refs(node)):
        if key not in others:
            return f"« {key} » doit être un autre champ du formulaire."
    return ""


# ---- evaluation ---------------------------------------------------------------------------------------------------------------


def _num(v) -> float | None:
    n = num_of(v)
    return n if n is not None and math.isfinite(n) and abs(n) < LIMIT else None


def _truthy(v) -> bool:
    if isinstance(v, bool):
        return v
    if v is None:
        return False
    if isinstance(v, (int, float)):
        return v != 0
    return len(text_of(v)) > 0


def _empty(v) -> bool:
    return v is None or v == "" or v == []


def _equal(a, b) -> bool:
    na, nb = num_of(a), num_of(b)
    return na == nb if na is not None and nb is not None else text_of(a) == text_of(b)


_ORDER = {"<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge}


def _order(op: str, a, b) -> bool:
    na, nb = num_of(a), num_of(b)
    if na is not None and nb is not None:
        return _ORDER[op](na, nb)
    if isinstance(a, str) and isinstance(b, str) and _ISO_DATE.match(a) and _ISO_DATE.match(b):
        return _ORDER[op](a, b)
    return False


def _arith(op: str, a, b):
    x, y = _num(a), _num(b)
    if x is None or y is None:
        return None
    if op == "+":
        r = x + y
    elif op == "-":
        r = x - y
    elif op == "*":
        r = x * y
    elif y == 0:
        return None
    elif op == "/":
        r = x / y
    else:
        r = math.fmod(x, y)
    return r if math.isfinite(r) and abs(r) < LIMIT else None


def _round(x: float, digits: int) -> float:
    p = 10.0**digits
    r = math.floor(abs(x) * p + 0.5) / p
    return -r if x < 0 else r


def _days(a, b):
    ms = []
    for v in (a, b):
        m = _ISO_DATE.match(v) if isinstance(v, str) else None
        if not m:
            return None
        y, mo, d = (int(g) for g in m.groups())
        try:
            ms.append(date(y, mo, d).toordinal())
        except ValueError:
            return None
    return float(ms[1] - ms[0])


def _aggregate(name: str, vals: list):
    """min, max, sum, avg of the numbers among the values (empty ones are ignored)."""
    nums = [n for v in vals if not _empty(v) and (n := _num(v)) is not None]
    if name == "sum":
        return sum(nums)
    if not nums:
        return None
    if name == "min":
        return min(nums)
    if name == "max":
        return max(nums)
    return sum(nums) / len(nums)


def _rounding(name: str, vals: list):
    x = _num(vals[0])
    if x is None:
        return None
    if name == "abs":
        return abs(x)
    if name == "floor":
        return float(math.floor(x))
    if name == "ceil":
        return float(math.ceil(x))
    digits = 0.0 if len(vals) == 1 else _num(vals[1])
    if digits is None or not digits.is_integer() or not 0 <= digits <= 10:
        return None
    return _round(x, int(digits))


def _left_right(name: str, vals: list):
    n = _num(vals[1])
    if n is None or n < 0:
        return None
    n = int(math.floor(n))
    s = text_of(vals[0])
    return s[:n] if name == "left" else (s[-n:] if n else "")


def _count(vals: list):  # lines of a repeated control, boxes ticked…
    return float(len(vals[0])) if isinstance(vals[0], list) else (0.0 if _empty(vals[0]) else 1.0)


def _sum_col(vals: list):  # the total of one column of a repeated control
    key = text_of(vals[1])
    rows = vals[0] if isinstance(vals[0], list) else []
    return sum(n for r in rows if isinstance(r, dict) and (n := _num(r.get(key))) is not None)


# one entry per function of the formula language that takes its arguments already evaluated (`if` is lazy, see `_call`)
_CALLS: dict[str, Callable[[list], object]] = {
    "and": lambda vals: all(_truthy(v) for v in vals),
    "or": lambda vals: any(_truthy(v) for v in vals),
    "not": lambda vals: not _truthy(vals[0]),
    **{f: partial(_aggregate, f) for f in ("min", "max", "sum", "avg")},
    **{f: partial(_rounding, f) for f in ("floor", "ceil", "abs", "round")},
    "len": lambda vals: float(len(text_of(vals[0]))),
    "upper": lambda vals: text_of(vals[0]).upper(),
    "lower": lambda vals: text_of(vals[0]).lower(),
    "trim": lambda vals: text_of(vals[0]).strip(),
    "concat": lambda vals: "".join(text_of(v) for v in vals),
    **{f: partial(_left_right, f) for f in ("left", "right")},
    "number": lambda vals: _num(vals[0]),
    "text": lambda vals: text_of(vals[0]),
    "is_empty": lambda vals: _empty(vals[0]),
    "count": _count,
    "sum_col": _sum_col,
    "coalesce": lambda vals: next((v for v in vals if not _empty(v)), None),
    "days_between": lambda vals: _days(vals[0], vals[1]),
}


def _call(name: str, args: list, values: dict):
    if name == "if":
        return _eval(args[1] if _truthy(_eval(args[0], values)) else args[2], values)
    return _CALLS[name]([_eval(a, values) for a in args])


def _eval(node, values: dict):
    kind = node[0]
    if kind in ("num", "str", "bool"):
        return node[1]
    if kind == "ref":
        return values.get(node[1])
    if kind == "neg":
        n = _num(_eval(node[1], values))
        return None if n is None else -n
    if kind == "call":
        return _call(node[1], node[2], values)
    op = node[1]
    a, b = _eval(node[2], values), _eval(node[3], values)
    if op == "&":
        s = text_of(a) + text_of(b)
        return s if len(s) <= MAX_TEXT else None
    if op == "=":
        return _equal(a, b)
    if op == "<>":
        return not _equal(a, b)
    if op in ("<", "<=", ">", ">="):
        return _order(op, a, b)
    return _arith(op, a, b)


def evaluate(formula: str, values: dict):
    """The result of a formula, or None when it cannot be computed. A whole number comes back as an int."""
    try:
        result = _eval(parse(formula), values)
    except (FormulaError, RecursionError):
        return None
    if isinstance(result, float):
        if not math.isfinite(result) or abs(result) >= LIMIT:
            return None
        return int(result) if result.is_integer() else result
    if isinstance(result, str) and len(result) > MAX_TEXT:
        return None
    return result


def value_for(kind: str, result):
    """The result as the value of a field of this type (a number for « number », text otherwise); None if it does not fit."""
    if result is None:
        return None
    if kind == "number":
        n = _num(result)
        return None if n is None else (int(n) if n.is_integer() else n)
    return text_of(result)
