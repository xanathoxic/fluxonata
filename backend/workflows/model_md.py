"""Moteur Markdown du nœud « Modèle de document » (ADR 0036) : un sous-ensemble volontairement limité, sûr par construction.

Un seul moteur sert l'aperçu de l'éditeur, le fichier (HTML, Word, PDF, PowerPoint) et l'e-mail : le rendu est identique partout.

Sûreté : le HTML brut écrit dans le texte est affiché tel quel (échappé), jamais interprété ; les liens ne peuvent être que
http, https ou mailto ; une image ne peut être qu'une image **de la plate-forme** (`image:<identifiant>`), jamais une adresse
distante (pas de pixel espion, pas de requête sortante). Les valeurs insérées par `{{ expression }}` sont échappées : une valeur
ne peut ni ouvrir un titre, ni une liste, ni un lien.

Pris en charge : titres (# à ######), paragraphes, **gras**, *italique*, ~~barré~~, `code`, blocs de code, listes (à puces,
numérotées, imbriquées), citations, filet (---), liens, images, tableaux (| a | b |), saut de ligne forcé (deux espaces ou \\)."""

import re
import uuid
from collections.abc import Callable
from html import escape

from workflows.expressions import TEMPLATE_RE, evaluate

MAX_MARKDOWN = 200_000
MAX_DEPTH = 6
IMAGE_RE = re.compile(r"^image:([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$")
SAFE_LINK = re.compile(r"^(https?://|mailto:)[^\s<>\"']+$", re.IGNORECASE)

# ---------------------------------------------------------------- valeurs insérées

_ESC = re.compile(r"([\\`*_\[\]()<>|~!#])")


def escape_value(value) -> str:
    """Une valeur insérée dans le Markdown est du texte : ses caractères spéciaux sont neutralisés, ses retours à la ligne aplatis."""
    text = "" if value is None else value if isinstance(value, str) else str(value)
    text = " ".join(text.replace("\r", "\n").split("\n")).strip() if "\n" in text or "\r" in text else text
    text = _ESC.sub(r"\\\1", text)
    if re.match(r"^[-+]\s|^\d+[.)]\s", text):  # début de ligne : ne devient pas une liste
        text = "\\" + text
    return text


def resolve_markdown(source: str, ctx: dict) -> str:
    return TEMPLATE_RE.sub(lambda m: escape_value(evaluate(m.group(1), ctx)), source)


# ---------------------------------------------------------------- inline

Inline = tuple


def _flat(nodes) -> str:
    out = []
    for n in nodes:
        out.append(
            n[1]
            if n[0] in ("text", "code")
            else _flat(n[-1])
            if n[0] in ("strong", "em", "del", "link")
            else n[1]
            if n[0] == "image"
            else "\n"
            if n[0] == "br"
            else ""
        )
    return "".join(out)


_TOKEN = re.compile(
    r"(?P<esc>\\[\\`*_{}\[\]()#+\-.!|~<>])"
    r"|(?P<code>(?<!`)(?P<ticks>`+)(?!`)(?P<codetxt>.+?)(?<!`)(?P=ticks)(?!`))"
    r"|(?P<image>!\[(?P<alt>[^\]\n]*)\]\((?P<isrc>(?:[^()\s]|\([^()\s]*\))*)(?:\s+\"[^\"]*\")?\))"
    r"|(?P<link>\[(?P<ltxt>[^\]\n]+)\]\((?P<lurl>(?:[^()\s]|\([^()\s]*\))*)(?:\s+\"[^\"]*\")?\))"
    r"|(?P<strong>\*\*(?=\S)(?P<s1>.+?)(?<=\S)\*\*|__(?=\S)(?P<s2>.+?)(?<=\S)__)"
    r"|(?P<del>~~(?=\S)(?P<dtxt>.+?)(?<=\S)~~)"
    r"|(?P<em>\*(?=[^\s*])(?P<e1>.+?)(?<=[^\s*])\*|(?<![\w])_(?=[^\s_])(?P<e2>.+?)(?<=[^\s_])_(?![\w]))"
    r"|(?P<br>(?: {2,}|\\)\n)",
    re.DOTALL,
)


def parse_inline(text: str, depth: int = 0) -> list[Inline]:
    out: list[Inline] = []
    pos = 0
    for m in _TOKEN.finditer(text):
        if m.start() < pos:
            continue
        if m.start() > pos:
            out.append(("text", text[pos : m.start()]))
        pos = m.end()
        kind = (
            m.lastgroup
            if m.lastgroup in ("esc", "br")
            else next(
                k for k in ("esc", "code", "image", "link", "strong", "del", "em", "br") if m.group(k) is not None
            )
        )
        if kind == "esc":
            out.append(("text", m.group("esc")[1]))
        elif kind == "code":
            out.append(("code", m.group("codetxt").strip()))
        elif kind == "image":
            out.append(("image", m.group("alt"), m.group("isrc")))
        elif kind == "link":
            inner = parse_inline(m.group("ltxt"), depth + 1) if depth < MAX_DEPTH else [("text", m.group("ltxt"))]
            out.append(("link", m.group("lurl"), inner))
        elif kind == "br":
            out.append(("br",))
        else:
            body = m.group({"strong": "s1", "del": "dtxt", "em": "e1"}[kind]) or m.group(
                {"strong": "s2", "del": "dtxt", "em": "e2"}[kind]
            )
            inner = parse_inline(body, depth + 1) if depth < MAX_DEPTH else [("text", body)]
            out.append((kind, inner))
    if pos < len(text):
        out.append(("text", text[pos:]))
    return out


# ---------------------------------------------------------------- blocs

Block = tuple
_HEADING = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.*?)[ \t]*#*[ \t]*$")
_RULE = re.compile(r"^ {0,3}([-*_])([ \t]*\1){2,}[ \t]*$")
_BULLET = re.compile(r"^( {0,3})([-*+])[ \t]+(.*)$")
_NUMBER = re.compile(r"^( {0,3})(\d{1,9})[.)][ \t]+(.*)$")
_FENCE = re.compile(r"^ {0,3}(```+|~~~+)[ \t]*([\w+-]*)")
_QUOTE = re.compile(r"^ {0,3}>[ ]?(.*)$")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)*\|?\s*$")


def _cells(line: str) -> list[str]:
    s = line.strip()
    s = s[1:] if s.startswith("|") else s
    s = s[:-1] if s.endswith("|") and not s.endswith("\\|") else s
    return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", s)]


def _aligns(sep: str) -> list[str]:
    out = []
    for c in _cells(sep):
        out.append(
            "center"
            if c.startswith(":") and c.endswith(":")
            else "right"
            if c.endswith(":")
            else "left"
            if c.startswith(":")
            else ""
        )
    return out


def _starts_block(line: str) -> bool:
    return bool(
        _HEADING.match(line)
        or _RULE.match(line)
        or _BULLET.match(line)
        or _NUMBER.match(line)
        or _FENCE.match(line)
        or _QUOTE.match(line)
    )


_INDENT = re.compile(r"^( {2,}|\t)")


def _item_body(lines: list[str], i: int, first: str) -> tuple[list[str], int]:
    """Les lignes d'un élément de liste : sa première ligne, puis tout ce qui est en retrait (ou la suite du même paragraphe)."""
    body = [first]
    while i < len(lines):
        ln = lines[i]
        if not ln.strip():
            nxt = next((x for x in lines[i + 1 :] if x.strip()), None)
            if nxt is None or not _INDENT.match(nxt):
                break
            body.append("")
        elif _INDENT.match(ln):
            body.append(re.sub(r"^( {1,4}|\t)", "", ln, count=1))
        elif not _starts_block(ln) and body[-1].strip():
            body.append(ln.strip())  # suite de ligne
        else:
            break
        i += 1
    return body, i


def _skip_blank_before(lines: list[str], i: int, pattern: re.Pattern) -> int:
    """Une ligne vide entre deux éléments du même type n'interrompt pas la liste."""
    j = i
    while j < len(lines) and not lines[j].strip():
        j += 1
    return j if j > i and j < len(lines) and pattern.match(lines[j]) else i


def _list(lines: list[str], i: int, depth: int) -> tuple[Block, int]:
    ordered = _NUMBER.match(lines[i]) is not None
    pattern = _NUMBER if ordered else _BULLET
    first = pattern.match(lines[i])
    if first is None:
        raise ValueError("pas une liste")
    start = int(first.group(2)) if ordered else 1
    items: list[list[Block]] = []
    while i < len(lines):
        m = pattern.match(lines[i])
        if not m:
            break
        body, i = _item_body(lines, i + 1, m.group(3))
        items.append(parse_blocks("\n".join(body), depth + 1))
        i = _skip_blank_before(lines, i, pattern)
    return ("ol" if ordered else "ul", start, items), i


def _fence(lines: list[str], i: int) -> tuple[Block, int] | None:
    m = _FENCE.match(lines[i])
    if not m:
        return None
    mark, code = m.group(1), []
    i += 1
    while i < len(lines) and not lines[i].strip().startswith(mark[:3]):
        code.append(lines[i])
        i += 1
    return ("code", m.group(2), "\n".join(code)), i + 1


def _quote(lines: list[str], i: int, depth: int) -> tuple[Block, int] | None:
    if not _QUOTE.match(lines[i]):
        return None
    quoted: list[str] = []
    while i < len(lines) and (_QUOTE.match(lines[i]) or (lines[i].strip() and quoted and not _starts_block(lines[i]))):
        m = _QUOTE.match(lines[i])
        quoted.append(m.group(1) if m else lines[i])
        i += 1
    return ("quote", parse_blocks("\n".join(quoted), depth + 1)), i


def _table(lines: list[str], i: int) -> tuple[Block, int] | None:
    line = lines[i]
    if not ("|" in line and i + 1 < len(lines) and _TABLE_SEP.match(lines[i + 1]) and "-" in lines[i + 1]):
        return None
    if len(_cells(line)) != len(_cells(lines[i + 1])):
        return None
    head, aligns, rows = _cells(line), _aligns(lines[i + 1]), []
    i += 2
    while i < len(lines) and lines[i].strip() and "|" in lines[i]:
        rows.append(([*_cells(lines[i]), *[""] * len(head)])[: len(head)])
        i += 1
    return ("table", [parse_inline(c) for c in head], aligns, [[parse_inline(c) for c in r] for r in rows]), i


def _one_block(lines: list[str], i: int, depth: int) -> tuple[Block, int] | None:
    """Le bloc qui commence à la ligne `i` (None : la ligne appartient à un paragraphe)."""
    line = lines[i]
    if heading := _HEADING.match(line):
        return ("heading", len(heading.group(1)), parse_inline(heading.group(2))), i + 1
    if _RULE.match(line):
        return ("hr",), i + 1
    if _BULLET.match(line) or _NUMBER.match(line):
        return _list(lines, i, depth)
    return _fence(lines, i) or _quote(lines, i, depth) or _table(lines, i)


def parse_blocks(source: str, depth: int = 0) -> list[Block]:
    if depth > MAX_DEPTH:
        return [("para", [("text", source)])]
    lines = source.replace("\r\n", "\n").replace("\r", "\n").replace("\t", "    ").split("\n")
    blocks: list[Block] = []
    para: list[str] = []
    i = 0
    while i < len(lines):
        found = _one_block(lines, i, depth) if lines[i].strip() else None
        if found is None:
            if lines[i].strip():
                text = lines[i].lstrip()
                para.append(
                    text if text.endswith("  ") else text.rstrip()
                )  # deux espaces en fin de ligne : saut de ligne forcé
            i += 1
            if lines[i - 1].strip() or not para:
                continue
        if para:
            blocks.append(("para", parse_inline("\n".join(para))))
            para.clear()
        if found is not None:
            blocks.append(found[0])
            i = found[1]
    if para:
        blocks.append(("para", parse_inline("\n".join(para))))
    return blocks


# ---------------------------------------------------------------- HTML

ImageUrl = Callable[[str], str | None]


def _inline_html(nodes, image_url: ImageUrl) -> str:
    out = []
    for n in nodes:
        k = n[0]
        if k == "text":
            out.append(escape(n[1], quote=False))
        elif k == "code":
            out.append(f"<code>{escape(n[1], quote=False)}</code>")
        elif k == "strong":
            out.append(f"<strong>{_inline_html(n[1], image_url)}</strong>")
        elif k == "em":
            out.append(f"<em>{_inline_html(n[1], image_url)}</em>")
        elif k == "del":
            out.append(f"<del>{_inline_html(n[1], image_url)}</del>")
        elif k == "br":
            out.append("<br>")
        elif k == "link":
            inner = _inline_html(n[2], image_url)
            out.append(
                f'<a href="{escape(n[1], quote=True)}" rel="noopener noreferrer">{inner}</a>'
                if SAFE_LINK.match(n[1])
                else inner
            )
        elif k == "image":
            m = IMAGE_RE.match(n[2])
            url = image_url(m.group(1)) if m else None
            out.append(
                f'<img src="{escape(url, quote=True)}" alt="{escape(n[1], quote=True)}">'
                if url
                else escape(n[1], quote=False)
            )
    return "".join(out)


def _block_html(b: Block, image_url: ImageUrl) -> str:
    k = b[0]
    if k == "heading":
        return f"<h{b[1]}>{_inline_html(b[2], image_url)}</h{b[1]}>"
    if k == "para":
        return f"<p>{_inline_html(b[1], image_url)}</p>"
    if k == "hr":
        return "<hr>"
    if k == "code":
        return f"<pre><code>{escape(b[2], quote=False)}</code></pre>"
    if k == "quote":
        return f"<blockquote>{blocks_html(b[1], image_url)}</blockquote>"
    if k in ("ul", "ol"):
        start = f' start="{b[1]}"' if k == "ol" and b[1] != 1 else ""
        items = "".join(f"<li>{_tight(item, image_url)}</li>" for item in b[2])
        return f"<{k}{start}>{items}</{k}>"
    if k == "table":

        def cell(tag, content, align):
            style = f' style="text-align:{align}"' if align else ""
            return f"<{tag}{style}>{_inline_html(content, image_url)}</{tag}>"

        head = "".join(cell("th", c, b[2][i] if i < len(b[2]) else "") for i, c in enumerate(b[1]))
        rows = "".join(
            "<tr>" + "".join(cell("td", c, b[2][i] if i < len(b[2]) else "") for i, c in enumerate(r)) + "</tr>"
            for r in b[3]
        )
        return f"<table><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table>"
    return ""


def _tight(item: list[Block], image_url: ImageUrl) -> str:
    """Un élément de liste d'un seul paragraphe n'est pas enveloppé dans <p>."""
    if len(item) == 1 and item[0][0] == "para":
        return _inline_html(item[0][1], image_url)
    return "".join(
        _inline_html(b[1], image_url) if b[0] == "para" and i == 0 else _block_html(b, image_url)
        for i, b in enumerate(item)
    )


def blocks_html(blocks: list[Block], image_url: ImageUrl) -> str:
    return "\n".join(_block_html(b, image_url) for b in blocks)


def to_html(source: str, image_url: ImageUrl | None = None) -> str:
    return blocks_html(parse_blocks(source), image_url or (lambda _id: None))


def placeholder_url(image_id: str) -> str:
    """Dans les sorties d'une étape, l'image garde sa référence : les consommateurs (fichier, e-mail) la remplacent."""
    return f"image:{image_id}"


# ---------------------------------------------------------------- texte brut


def inline_text(nodes) -> str:
    out = []
    for n in nodes:
        k = n[0]
        if k in ("text", "code"):
            out.append(n[1])
        elif k in ("strong", "em", "del"):
            out.append(inline_text(n[1]))
        elif k == "link":
            label = inline_text(n[2])
            out.append(f"{label} ({n[1]})" if SAFE_LINK.match(n[1]) and label != n[1] else label)
        elif k == "image":
            out.append(n[1])
        elif k == "br":
            out.append("\n")
    return "".join(out)


def _block_text(b: Block, indent: str = "") -> list[str]:
    k = b[0]
    if k == "heading":
        text = inline_text(b[2])
        return [text.upper() if b[1] == 1 else text, ""]
    if k == "para":
        return [*(indent + ln for ln in inline_text(b[1]).split("\n")), ""]
    if k == "hr":
        return ["-" * 40, ""]
    if k == "code":
        return [*(indent + "    " + ln for ln in b[2].split("\n")), ""]
    if k == "quote":
        return [*(indent + "> " + ln if ln else "" for blk in b[1] for ln in _block_text(blk)), ""]
    if k in ("ul", "ol"):
        out = []
        for n, item in enumerate(b[2], b[1]):
            mark = "• " if k == "ul" else f"{n}. "
            body = [ln for blk in item for ln in _block_text(blk, "") if ln.strip()]
            for j, ln in enumerate(body):
                out.append(indent + (mark if j == 0 else " " * len(mark)) + ln)
        return [*out, ""]
    if k == "table":
        rows = [[inline_text(c) for c in b[1]], *([inline_text(c) for c in r] for r in b[3])]
        return [*("  |  ".join(r) for r in rows), ""]
    return []


def to_text(source: str) -> str:
    lines = [ln for b in parse_blocks(source) for ln in _block_text(b)]
    return "\n".join(lines).strip() + "\n" if lines else ""


def _children(b: Block) -> tuple[list, list[Block]]:
    """(listes d'éléments en ligne, blocs enfants) d'un bloc : de quoi parcourir tout un texte."""
    k = b[0]
    if k == "heading":
        return [b[2]], []
    if k == "para":
        return [b[1]], []
    if k == "quote":
        return [], b[1]
    if k in ("ul", "ol"):
        return [], [blk for item in b[2] for blk in item]
    if k == "table":
        return [*b[1], *(c for row in b[3] for c in row)], []
    return [], []


def _inline_nodes(nodes):
    for n in nodes:
        yield n
        if n[0] in ("strong", "em", "del"):
            yield from _inline_nodes(n[1])
        elif n[0] == "link":
            yield from _inline_nodes(n[2])


def _all_inlines(blocks: list[Block]):
    for b in blocks:
        lists, subs = _children(b)
        for lst in lists:
            yield from _inline_nodes(lst)
        yield from _all_inlines(subs)


def image_ids(source: str) -> list[str]:
    """Les images de la plate-forme citées dans le texte, dans l'ordre, sans doublon."""
    found: list[str] = []
    for n in _all_inlines(parse_blocks(source)):
        m = IMAGE_RE.match(n[2]) if n[0] == "image" else None
        if m and m.group(1) not in found:
            found.append(m.group(1))
    return found


def is_image_id(value: str) -> bool:
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


# ---------------------------------------------------------------- aperçu de l'éditeur


def preview_html(source: str, image_url: ImageUrl) -> str:
    """Aperçu à la conception : les `{{ expressions }}` ne sont pas évaluées, elles apparaissent comme des pastilles repérables."""
    marked = TEMPLATE_RE.sub(lambda m: "\x00" + m.group(1).strip().replace("\x00", "") + "\x01", source)
    html = to_html(marked, image_url)
    return re.sub("\x00([^\x01]*)\x01", lambda m: f'<span class="md-var">{m.group(1)}</span>', html)
