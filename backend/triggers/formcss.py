"""Custom CSS of a form: a strict subset, checked and rewritten into one canonical text.

A form is shown to anonymous visitors, so its author's CSS is never trusted as text. The server parses it with a small
grammar, keeps only what it knows is harmless and EMITS the result itself (nothing of the original text is copied through),
so that the browser never has to parse anything the validator did not understand:

- rules are `selector { property: value; }` and `@media (min|max-width: Npx)` blocks of such rules; nothing else (no
  `@import`, `@font-face`, `@keyframes`, no nesting);
- selectors are relative to the form (the browser nests them under the form's own root) and use a closed vocabulary of
  elements, classes, a few attributes, pseudo-classes and pseudo-elements; every selector is written back starting with `&`
  (the form's root), so a rule can only ever reach what is inside the form;
- properties are visual only (no `position: fixed/absolute`, no `z-index`, no `content` other than a plain string); values
  are numbers with known units, colours, keywords, plain strings (font names, `content`) and a short list of functions —
  no `url()`, `image-set()`, `attr()`, `env()`, no `!important`, no backslash escapes;
- bounded size: 20 KB, 200 rules, 40 declarations per rule.

`normalize(src)` returns `(canonical_css, problems)`; `problems` is a list of `{"line", "message"}` and the canonical text is
empty as soon as there is one. Normalising is idempotent."""

import re

MAX_CSS = 20_000
MAX_RULES = 200
MAX_DECLS = 40
MAX_SELECTORS = 10
MAX_COMPOUNDS = 12

LAYOUT = {
    "display",
    "gap",
    "row-gap",
    "column-gap",
    "flex",
    "flex-direction",
    "flex-wrap",
    "flex-grow",
    "flex-shrink",
    "flex-basis",
    "grid-template-columns",
    "align-items",
    "align-self",
    "justify-content",
    "justify-self",
    "justify-items",
    "align-content",
}
BOX = {
    "margin",
    "margin-top",
    "margin-right",
    "margin-bottom",
    "margin-left",
    "padding",
    "padding-top",
    "padding-right",
    "padding-bottom",
    "padding-left",
    "width",
    "min-width",
    "max-width",
    "height",
    "min-height",
    "max-height",
    "overflow",
    "box-sizing",
    "aspect-ratio",
}
LOOK = {
    "color",
    "background",
    "background-color",
    "background-image",
    "background-size",
    "background-position",
    "background-repeat",
    "opacity",
    "box-shadow",
    "text-shadow",
    "outline",
    "outline-color",
    "outline-width",
    "outline-style",
    "outline-offset",
    "border",
    "border-top",
    "border-right",
    "border-bottom",
    "border-left",
    "border-color",
    "border-width",
    "border-style",
    "border-radius",
    "border-top-color",
    "border-right-color",
    "border-bottom-color",
    "border-left-color",
    "border-top-width",
    "border-right-width",
    "border-bottom-width",
    "border-left-width",
    "border-top-style",
    "border-right-style",
    "border-bottom-style",
    "border-left-style",
    "border-top-left-radius",
    "border-top-right-radius",
    "border-bottom-left-radius",
    "border-bottom-right-radius",
    "accent-color",
}
TEXT = {
    "font",
    "font-family",
    "font-size",
    "font-weight",
    "font-style",
    "line-height",
    "letter-spacing",
    "word-spacing",
    "text-align",
    "text-transform",
    "text-decoration",
    "text-decoration-color",
    "text-underline-offset",
    "text-indent",
    "white-space",
    "cursor",
    "transition",
    "transition-property",
    "transition-duration",
    "transition-timing-function",
    "transition-delay",
    "transform",
    "position",
    "content",
}
PROPERTIES = LAYOUT | BOX | LOOK | TEXT
ENUMS = {
    "position": {"relative", "static"},
    "display": {"block", "inline", "inline-block", "flex", "inline-flex", "grid", "none"},
    "overflow": {"visible", "hidden", "auto"},
    "text-align": {"left", "right", "center", "justify", "start", "end"},
    "text-transform": {"none", "uppercase", "lowercase", "capitalize"},
    "font-style": {"normal", "italic"},
    "cursor": {"default", "pointer", "text", "not-allowed", "grab", "help"},
    "white-space": {"normal", "nowrap", "pre", "pre-wrap", "pre-line"},
    "box-sizing": {"border-box", "content-box"},
}
STRING_PROPERTIES = {"content", "font-family", "font"}
FUNCTIONS = {
    "rgb",
    "rgba",
    "hsl",
    "hsla",
    "calc",
    "min",
    "max",
    "clamp",
    "var",
    "linear-gradient",
    "radial-gradient",
    "color-mix",
    "cubic-bezier",
    "translate",
    "translatex",
    "translatey",
    "scale",
    "rotate",
}
UNITS = {"px", "rem", "em", "%", "vh", "vw", "ch", "s", "ms", "deg", "fr", "ex"}
ELEMENTS = {
    "input",
    "select",
    "textarea",
    "label",
    "button",
    "fieldset",
    "legend",
    "p",
    "h2",
    "h3",
    "h4",
    "hr",
    "div",
    "span",
    "table",
    "thead",
    "tbody",
    "tr",
    "th",
    "td",
    "small",
    "strong",
    "caption",
    "option",
    "*",
}
ATTRIBUTES = {
    "data-key",
    "data-type",
    "type",
    "name",
    "aria-invalid",
    "aria-required",
    "disabled",
    "checked",
    "required",
}
PSEUDO_CLASSES = (
    "hover",
    "focus",
    "focus-visible",
    "focus-within",
    "active",
    "disabled",
    "checked",
    "first-child",
    "last-child",
    "placeholder-shown",
    "required",
    "optional",
    "invalid",
    "valid",
)
_PC_ALTERNATION = "|".join(
    sorted(PSEUDO_CLASSES, key=len, reverse=True)
)  # longest first: « focus-visible » before « focus »

_SEL_TOKEN = re.compile(
    r"""\s*(?:
    (?P<amp>&)|
    (?P<comb>[>+~])|
    (?P<cls>\.[A-Za-z_][A-Za-z0-9_-]*)|
    (?P<id>\#[A-Za-z_][A-Za-z0-9_-]*)|
    (?P<el>[A-Za-z][A-Za-z0-9]*|\*)|
    (?P<attr>\[\s*(?P<an>[a-z-]+)\s*(?:(?P<op>[\^$*]?=)\s*(?P<av>"[A-Za-z0-9 _.-]*"|'[A-Za-z0-9 _.-]*'|[A-Za-z0-9_.-]+)\s*)?\])|
    (?P<pe>::(?:placeholder|before|after|selection))|
    (?P<pc>:(?:@@PC@@|nth-child\(\s*(?:odd|even|[+-]?\d*n?(?:\s*[+-]\s*\d+)?)\s*\)))
    )""".replace("@@PC@@", _PC_ALTERNATION),
    re.X,
)
_VALUE_TOKEN = re.compile(
    r"""\s*(?:
    (?P<str>"[^"\n]*"|'[^'\n]*')|
    (?P<num>[+-]?(?:\d+\.?\d*|\.\d+)(?P<unit>%|[a-zA-Z]+)?)|
    (?P<hex>\#[0-9a-fA-F]{3,8}\b)|
    (?P<fn>[a-zA-Z-][a-zA-Z0-9-]*\()|
    (?P<id>--?[a-zA-Z_][a-zA-Z0-9_-]*|[a-zA-Z_][a-zA-Z0-9_-]*)|
    (?P<p>[,/()+*-])
    )""",
    re.X,
)
_MEDIA = re.compile(
    r"^@media\s*(?:\(\s*(?P<k1>min|max)-width\s*:\s*(?P<v1>\d{1,4})px\s*\)(?:\s+and\s+\(\s*(?P<k2>min|max)-width\s*:\s*(?P<v2>\d{1,4})px\s*\))?"
    r"|\(\s*prefers-color-scheme\s*:\s*(?P<scheme>dark|light)\s*\)|\(\s*prefers-reduced-motion\s*:\s*reduce\s*\))$"
)
_NTH = re.compile(r"\s+")


class _Bad(Exception):
    def __init__(self, line: int, message: str):
        super().__init__(message)
        self.line, self.message = line, message


def _strip_comments(src: str) -> str:
    """Comments become blanks (newlines kept: line numbers stay right)."""
    out, i, n = [], 0, len(src)
    while i < n:
        if src.startswith("/*", i):
            end = src.find("*/", i + 2)
            if end < 0:
                raise _Bad(src.count("\n", 0, i) + 1, "Commentaire non fermé.")
            out.append("".join("\n" if c == "\n" else " " for c in src[i : end + 2]))
            i = end + 2
        else:
            out.append(src[i])
            i += 1
    return "".join(out)


def _scan(text: str, i: int, stops: str) -> int:
    """Index of the first character of `stops` outside quotes, brackets and parentheses (len(text) if none)."""
    quote, depth = "", 0
    while i < len(text):
        c = text[i]
        if quote:
            if c == quote:
                quote = ""
            elif c == "\n":
                return i
        elif c in "\"'":
            quote = c
        elif c in "([":
            depth += 1
        elif c in ")]":
            depth -= 1
        elif depth <= 0 and c in stops:
            return i
        i += 1
    return len(text)


def _line(src: str, i: int) -> int:
    return src.count("\n", 0, i) + 1


def _selector(raw: str, line: int) -> str:
    """One selector, rewritten from its tokens; it always starts with `&` (the form's root) and `&` appears nowhere else,
    so the rule can only reach the inside of the form."""
    raw = raw.strip()
    if not raw:
        raise _Bad(line, "Sélecteur vide.")
    pos, out, count, first, after_comb = 0, [], 0, True, False
    while pos < len(raw):
        m = _SEL_TOKEN.match(raw, pos)
        if not m or m.end() == pos:
            raise _Bad(line, f"Sélecteur non autorisé : « {raw[pos : pos + 20].strip()} ».")
        spaced = len(m.group(0)) != len(m.group(0).lstrip())
        kind = next(
            name for name in ("amp", "comb", "cls", "id", "el", "attr", "pe", "pc") if m.group(name) is not None
        )
        text = m.group(kind)
        if kind == "amp" and not first:
            raise _Bad(line, "« & » (la racine du formulaire) ne peut être qu'au début d'un sélecteur.")
        if first and kind != "amp":
            out.append("& ")  # a selector that does not start with « & » is a descendant of the form
            after_comb = True
        if kind == "comb":
            out.append(f" {text} ")
            after_comb = True
        else:
            if kind == "el" and text.lower() not in ELEMENTS:
                raise _Bad(line, f"Élément « {text} » non autorisé.")
            if kind == "attr" and m.group("an") not in ATTRIBUTES:
                raise _Bad(line, f"Attribut « {m.group('an')} » non autorisé dans un sélecteur.")
            if kind == "pc" and text.startswith(":nth-child"):
                text = ":nth-child(" + _NTH.sub("", text[len(":nth-child(") : -1]) + ")"
            if kind == "attr":
                text = _NTH.sub("", text)
            if out and not after_comb and spaced and not first:
                out.append(" " + ("*" if kind in ("pe", "pc") else ""))  # `a :hover` means `a *:hover`: written out
            out.append(text)
            after_comb = False
            if kind != "amp":
                count += 1
        first = False
        pos = m.end()
    if count > MAX_COMPOUNDS:
        raise _Bad(line, "Sélecteur trop long.")
    return re.sub(r" {2,}", " ", "".join(out)).strip()


def _value(prop: str, raw: str, line: int) -> str:
    raw = " ".join(raw.split())
    if not raw:
        raise _Bad(line, f"Valeur vide pour « {prop} ».")
    if "!" in raw:
        raise _Bad(line, "« !important » n'est pas autorisé.")
    allowed = ENUMS.get(prop)
    if allowed is not None and raw.lower() not in allowed:
        raise _Bad(line, f"« {prop} » accepte : {', '.join(sorted(allowed))}.")
    pos, stack, tokens = 0, [], 0
    while pos < len(raw):
        m = _VALUE_TOKEN.match(raw, pos)
        if not m or m.end() == pos:
            raise _Bad(line, f"Valeur non autorisée pour « {prop} » : « {raw[pos : pos + 20].strip()} ».")
        tokens += 1
        if m.group("str") is not None and prop not in STRING_PROPERTIES:
            raise _Bad(line, f"« {prop} » n'accepte pas de texte entre guillemets.")
        if m.group("num") is not None and m.group("unit") and m.group("unit").lower() not in UNITS:
            raise _Bad(line, f"Unité « {m.group('unit')} » non autorisée.")
        if m.group("fn") is not None:
            name = m.group("fn")[:-1].lower()
            if name not in FUNCTIONS:
                raise _Bad(line, f"Fonction « {name}() » non autorisée (pas de url(), image-set(), attr()…).")
            stack.append(name)
            if name == "var" and not re.match(r"\s*--[a-zA-Z0-9-]+\s*[,)]", raw[m.end() :]):
                raise _Bad(line, "var() ne lit qu'une propriété personnalisée (--nom).")
        elif m.group("p") == "(":
            if not stack:
                raise _Bad(line, "Parenthèse non autorisée ici.")  # only inside a function such as calc()
            stack.append("(")
        elif m.group("p") == ")":
            if not stack:
                raise _Bad(line, "Parenthèse fermante en trop.")
            stack.pop()
        pos = m.end()
    if stack:
        raise _Bad(line, "Parenthèse non fermée.")
    if prop == "content" and not (raw == "none" or re.fullmatch(r"""("[^"\n]*"|'[^'\n]*')""", raw)):
        raise _Bad(line, "« content » accepte un texte simple entre guillemets ou « none ».")
    return raw


def _declarations(body: str, base: int, src: str) -> list[tuple[str, str]]:
    out, i = [], 0
    while i < len(body):
        j = _scan(body, i, ";")
        piece = body[i:j]
        i = j + 1
        if not piece.strip():
            continue
        line = _line(src, base + i - 1)
        if ":" not in piece:
            raise _Bad(line, f"Déclaration invalide : « {piece.strip()[:30]} ».")
        prop, _, value = piece.partition(":")
        prop = prop.strip().lower()
        if not re.fullmatch(r"--fb-[a-z0-9-]+", prop) and prop not in PROPERTIES:
            raise _Bad(line, f"Propriété « {prop} » non autorisée.")
        out.append((prop, _value("--fb" if prop.startswith("--fb-") else prop, value, line)))
        if len(out) > MAX_DECLS:
            raise _Bad(line, f"{MAX_DECLS} déclarations au plus par règle.")
    return out


def _body_end(text: str, i: int, nested: bool) -> int:
    """Index of the `}` closing the block whose `{` is just before `i`. A style rule may not contain braces."""
    depth, quote = 1, ""
    while i < len(text):
        c = text[i]
        if quote:
            if c == quote:
                quote = ""
        elif c in "\"'":
            quote = c
        elif c == "{":
            if not nested:
                raise _Bad(_line(text, i), "Une règle ne peut pas en contenir une autre.")
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise _Bad(_line(text, len(text) - 1), "Accolade non fermée.")


def _rules(text: str, start: int, end: int, src: str, counter: list[int], in_media: bool) -> list[str]:
    out: list[str] = []
    i = start
    while True:
        while i < end and text[i].isspace():
            i += 1
        if i >= end:
            return out
        line = _line(src, i)
        if text[i] == "}":
            raise _Bad(line, "Accolade fermante en trop.")
        at = re.match(r"@[a-zA-Z-]+", text[i:end]) if text[i] == "@" else None
        if at and at.group(0).lower() != "@media":
            raise _Bad(
                line,
                f"Règle « {at.group(0)} » non autorisée : seul @media (largeur, schéma de couleurs ou mouvement réduit) l'est.",
            )
        j = _scan(text, i, "{;}")
        if j >= end or text[j] != "{":
            raise _Bad(line, "Règle incomplète : il manque « { … } » (les déclarations isolées ne sont pas acceptées).")
        prelude = text[i:j].strip()
        counter[0] += 1
        if counter[0] > MAX_RULES:
            raise _Bad(line, f"{MAX_RULES} règles au plus.")
        if prelude.startswith("@"):
            m = _MEDIA.match(" ".join(prelude.split()))
            if in_media or not m:
                name = prelude.split()[0].split("(")[0]
                raise _Bad(
                    line,
                    f"Règle « {name} » non autorisée (seul @media avec min-width, max-width, prefers-color-scheme ou prefers-reduced-motion l'est, sans imbrication).",
                )
            close = _body_end(text, j + 1, True)
            inner = _rules(text, j + 1, close, src, counter, True)
            if m.group("scheme"):
                cond = f"(prefers-color-scheme: {m.group('scheme')})"
            elif m.group("k1"):
                cond = f"({m.group('k1')}-width: {int(m.group('v1'))}px)"
                if m.group("k2"):
                    cond += f" and ({m.group('k2')}-width: {int(m.group('v2'))}px)"
            else:
                cond = "(prefers-reduced-motion: reduce)"
            out.append(
                f"@media {cond} {{\n"
                + "\n".join("  " + ln if ln else ln for r in inner for ln in r.split("\n"))
                + "\n}"
            )
            i = close + 1
            continue
        close = _body_end(text, j + 1, False)
        parts = []
        k = 0
        while k <= len(prelude):
            e = _scan(prelude, k, ",")
            parts.append(prelude[k:e])
            k = e + 1
            if e >= len(prelude):
                break
        if len(parts) > MAX_SELECTORS:
            raise _Bad(line, f"{MAX_SELECTORS} sélecteurs au plus par règle.")
        selectors = [_selector(p, line) for p in parts]
        decls = _declarations(text[j + 1 : close], j + 1, src)
        if not decls:
            raise _Bad(line, "Règle sans déclaration.")
        out.append(",\n".join(selectors) + " {\n" + "\n".join(f"  {p}: {v};" for p, v in decls) + "\n}")
        i = close + 1


def normalize(src) -> tuple[str, list[dict]]:
    """(canonical CSS, problems). Empty CSS is valid and stays empty."""
    if src is None or (isinstance(src, str) and not src.strip()):
        return "", []
    if not isinstance(src, str):
        return "", [{"line": 1, "message": "Du texte est attendu."}]
    if len(src) > MAX_CSS:
        return "", [{"line": 1, "message": f"CSS trop long ({MAX_CSS // 1000} Ko au plus)."}]
    for n, c in enumerate(src):
        if c == "\\":
            return "", [{"line": _line(src, n), "message": "L'antislash (échappement) n'est pas autorisé."}]
        if c == "<":
            return "", [{"line": _line(src, n), "message": "Le caractère « < » n'est pas autorisé."}]
        if not (c.isprintable() or c in "\n\t\r"):
            return "", [{"line": _line(src, n), "message": "Caractère de contrôle non autorisé."}]
    try:
        text = _strip_comments(src.replace("\r", ""))
        if "/*" in text or "*/" in text:
            raise _Bad(1, "Commentaire invalide.")
        rules = _rules(text, 0, len(text), text, [0], False)
    except _Bad as exc:
        return "", [{"line": exc.line, "message": exc.message}]
    return "\n\n".join(rules), []
