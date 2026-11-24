"""Documents PDF riches (ADR 0036) : mise en page d'un modèle de document, sans dépendance (PDF 1.4 écrit à la main).

Rend ce que rend la page HTML : titres, paragraphes, gras / italique / barré / code, liens cliquables, listes imbriquées, citations,
tableaux à bordures et en-têtes, filets, blocs de code et images (PNG et JPEG). Polices PDF standard (Helvetica, Courier), accents
français compris (Windows-1252) ; les caractères hors de ce jeu sont remplacés par « ? ». Une image GIF, entrelacée ou CMJN est
remplacée par son texte alternatif. Les largeurs de caractères sont celles des polices standard : les retours à la ligne sont exacts."""

import re
import struct
import zlib
from dataclasses import dataclass, field

from workflows import model_md

# Largeurs (millièmes d'em) des polices standard, codes Windows-1252 0..255 (Helvetica-Oblique = Helvetica, Helvetica-BoldOblique = Helvetica-Bold).
WIDTHS_REGULAR = (
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    278,
    278,
    355,
    556,
    556,
    889,
    667,
    191,
    333,
    333,
    389,
    584,
    278,
    333,
    278,
    278,
    556,
    556,
    556,
    556,
    556,
    556,
    556,
    556,
    556,
    556,
    278,
    278,
    584,
    584,
    584,
    556,
    1015,
    667,
    667,
    722,
    722,
    667,
    611,
    778,
    722,
    278,
    500,
    667,
    556,
    833,
    722,
    778,
    667,
    778,
    722,
    667,
    611,
    722,
    667,
    944,
    667,
    667,
    611,
    278,
    278,
    278,
    469,
    556,
    333,
    556,
    556,
    500,
    556,
    556,
    278,
    556,
    556,
    222,
    222,
    500,
    222,
    833,
    556,
    556,
    556,
    556,
    333,
    500,
    278,
    556,
    500,
    722,
    500,
    500,
    500,
    334,
    260,
    334,
    584,
    350,
    556,
    350,
    222,
    556,
    333,
    1000,
    556,
    556,
    333,
    1000,
    667,
    333,
    1000,
    350,
    611,
    350,
    350,
    222,
    222,
    333,
    333,
    350,
    556,
    1000,
    333,
    1000,
    500,
    333,
    944,
    350,
    500,
    667,
    278,
    333,
    556,
    556,
    556,
    556,
    260,
    556,
    333,
    737,
    370,
    556,
    584,
    333,
    737,
    333,
    400,
    584,
    333,
    333,
    333,
    556,
    537,
    278,
    333,
    333,
    365,
    556,
    834,
    834,
    834,
    611,
    667,
    667,
    667,
    667,
    667,
    667,
    1000,
    722,
    667,
    667,
    667,
    667,
    278,
    278,
    278,
    278,
    722,
    722,
    778,
    778,
    778,
    778,
    778,
    584,
    778,
    722,
    722,
    722,
    722,
    667,
    667,
    611,
    556,
    556,
    556,
    556,
    556,
    556,
    889,
    500,
    556,
    556,
    556,
    556,
    278,
    278,
    278,
    278,
    556,
    556,
    556,
    556,
    556,
    556,
    556,
    584,
    611,
    556,
    556,
    556,
    556,
    500,
    556,
    500,
)
WIDTHS_BOLD = (
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    278,
    333,
    474,
    556,
    556,
    889,
    722,
    238,
    333,
    333,
    389,
    584,
    278,
    333,
    278,
    278,
    556,
    556,
    556,
    556,
    556,
    556,
    556,
    556,
    556,
    556,
    333,
    333,
    584,
    584,
    584,
    611,
    975,
    722,
    722,
    722,
    722,
    667,
    611,
    778,
    722,
    278,
    556,
    722,
    611,
    833,
    722,
    778,
    667,
    778,
    722,
    667,
    611,
    722,
    667,
    944,
    667,
    667,
    611,
    333,
    278,
    333,
    584,
    556,
    333,
    556,
    611,
    556,
    611,
    556,
    333,
    611,
    611,
    278,
    278,
    556,
    278,
    889,
    611,
    611,
    611,
    611,
    389,
    556,
    333,
    611,
    556,
    778,
    556,
    556,
    500,
    389,
    280,
    389,
    584,
    350,
    556,
    350,
    278,
    556,
    500,
    1000,
    556,
    556,
    333,
    1000,
    667,
    333,
    1000,
    350,
    611,
    350,
    350,
    278,
    278,
    500,
    500,
    350,
    556,
    1000,
    333,
    1000,
    556,
    333,
    944,
    350,
    500,
    667,
    278,
    333,
    556,
    556,
    556,
    556,
    280,
    556,
    333,
    737,
    370,
    556,
    584,
    333,
    737,
    333,
    400,
    584,
    333,
    333,
    333,
    611,
    556,
    278,
    333,
    333,
    365,
    556,
    834,
    834,
    834,
    611,
    722,
    722,
    722,
    722,
    722,
    722,
    1000,
    722,
    667,
    667,
    667,
    667,
    278,
    278,
    278,
    278,
    722,
    722,
    778,
    778,
    778,
    778,
    778,
    584,
    778,
    722,
    722,
    722,
    722,
    667,
    667,
    611,
    556,
    556,
    556,
    556,
    556,
    556,
    889,
    556,
    556,
    556,
    556,
    556,
    278,
    278,
    278,
    278,
    611,
    611,
    611,
    611,
    611,
    611,
    611,
    584,
    611,
    611,
    611,
    611,
    611,
    556,
    611,
    556,
)
MONO_WIDTH = 600

PAGE_W, PAGE_H, MARGIN = 595.0, 842.0, 56.0
MAX_PAGES = 200
INK, MUTED, LINK, RULE, SHADE, HEAD_BG = (
    (0.12, 0.16, 0.22),
    (0.29, 0.33, 0.39),
    (0.11, 0.31, 0.85),
    (0.61, 0.64, 0.69),
    (0.95, 0.96, 0.96),
    (0.9, 0.91, 0.92),
)
HEADING_SIZE = {1: 22, 2: 18, 3: 15, 4: 13, 5: 12, 6: 11}
FONTS: dict[object, str] = {
    (False, False): "F1",
    (True, False): "F2",
    (False, True): "F3",
    (True, True): "F4",
    "mono": "F5",
}
SUBSTITUTE = {
    "\u2192": "->",
    "\u2190": "<-",
    "\u2713": "v",
    "\u202f": " ",
    "\u2011": "-",
    "\u2212": "-",
    "\u2022": "\u2022",
}
MAX_RAW_IMAGE = 8 * 1024 * 1024
EPS = 0.05  # tolérance d'arrondi : un mot qui tient pile ne doit pas être coupé


# ------------------------------------------------------------------ images


@dataclass
class PdfImage:
    """Une image prête à être mise dans le PDF : dimensions, espace colorimétrique, flux, et son masque de transparence éventuel."""

    width: int
    height: int
    colorspace: str
    bits: int
    data: bytes
    filter: str
    parms: str = ""
    smask: "PdfImage | None" = None


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    return a if pa <= pb and pa <= pc else b if pb <= pc else c


def _unfilter_row(kind: int, row: bytearray, prev: bytearray, bpp: int) -> None:
    for i in range(len(row)):
        left = row[i - bpp] if i >= bpp else 0
        up = prev[i]
        if kind == 1:
            row[i] = (row[i] + left) & 255
        elif kind == 2:
            row[i] = (row[i] + up) & 255
        elif kind == 3:
            row[i] = (row[i] + ((left + up) >> 1)) & 255
        elif kind == 4:
            row[i] = (row[i] + _paeth(left, up, prev[i - bpp] if i >= bpp else 0)) & 255


def _unfilter(raw: bytes, height: int, stride: int, bpp: int) -> bytes:
    out, prev, pos = bytearray(), bytearray(stride), 0
    for _ in range(height):
        row = bytearray(raw[pos + 1 : pos + 1 + stride])
        _unfilter_row(raw[pos], row, prev, bpp)
        out += row
        prev, pos = row, pos + 1 + stride
    return bytes(out)


def _png_chunks(data: bytes) -> dict[bytes, bytes] | None:
    chunks: dict[bytes, bytes] = {}
    pos = 8
    while pos + 8 <= len(data):
        size, kind = struct.unpack(">I4s", data[pos : pos + 8])
        chunks[kind] = (
            chunks.get(kind, b"") + data[pos + 8 : pos + 8 + size]
            if kind == b"IDAT"
            else data[pos + 8 : pos + 8 + size]
        )
        pos += 12 + size
    return chunks if b"IHDR" in chunks and b"IDAT" in chunks else None


CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}


def _png_direct(w: int, h: int, depth: int, ctype: int, chunks: dict[bytes, bytes]) -> PdfImage | None:
    """Gris, RVB ou palette sans transparence : le flux compressé du PNG est repris tel quel (prédicteur PNG de PDF)."""
    if ctype == 3:
        palette = chunks.get(b"PLTE", b"")
        if not palette or depth not in (1, 2, 4, 8):
            return None
        space = f"[/Indexed /DeviceRGB {len(palette) // 3 - 1} <{palette.hex()}>]"
    else:
        space = "/DeviceGray" if ctype == 0 else "/DeviceRGB"
    parms = f"/Predictor 15 /Colors {CHANNELS[ctype] if ctype != 3 else 1} /BitsPerComponent {depth} /Columns {w}"
    return PdfImage(w, h, space, depth, chunks[b"IDAT"], "/FlateDecode", parms)


def _samples(raw: bytes, w: int, h: int, depth: int, ctype: int) -> bytes | None:
    if depth not in (8, 16) or len(raw) > MAX_RAW_IMAGE:
        return None
    channels = CHANNELS[ctype]
    bpp = channels * depth // 8
    stride = w * bpp
    if len(raw) < h * (stride + 1):
        return None
    data = _unfilter(raw, h, stride, bpp)
    return data[0::2] if depth == 16 else data


def _flate(width: int, height: int, space: str, data: bytes) -> PdfImage:
    return PdfImage(width, height, space, 8, zlib.compress(data), "/FlateDecode")


def _png_decoded(w: int, h: int, depth: int, ctype: int, chunks: dict[bytes, bytes]) -> PdfImage | None:
    """Transparence (canal alpha, palette avec tRNS) ou 16 bits : les pixels sont décodés, le canal alpha devient un masque."""
    data = _samples(zlib.decompress(chunks[b"IDAT"]), w, h, depth, ctype)
    if data is None:
        return None
    alpha: bytes | None = None
    if ctype == 6:
        rgb = bytearray(w * h * 3)
        for i in range(3):
            rgb[i::3] = data[i::4]
        space, pixels, alpha = "/DeviceRGB", bytes(rgb), data[3::4]
    elif ctype == 4:
        space, pixels, alpha = "/DeviceGray", data[0::2], data[1::2]
    elif ctype == 3:
        palette, trns = chunks.get(b"PLTE", b""), chunks.get(b"tRNS", b"")
        if depth != 8 or not palette:
            return None
        look = [palette[i * 3 : i * 3 + 3].ljust(3, b"\0") for i in range(256)]
        pixels = b"".join(look[i] for i in data)
        alpha = bytes(trns[i] if i < len(trns) else 255 for i in data)
        space = "/DeviceRGB"
    else:
        space, pixels = ("/DeviceGray" if ctype == 0 else "/DeviceRGB"), data
    image = _flate(w, h, space, pixels)
    if alpha is not None and any(a != 255 for a in alpha):
        image.smask = _flate(w, h, "/DeviceGray", alpha)
    return image


def _png(data: bytes) -> PdfImage | None:
    chunks = _png_chunks(data)
    if chunks is None:
        return None
    w, h, depth, ctype, _, _, interlace = struct.unpack(">IIBBBBB", chunks[b"IHDR"][:13])
    if interlace or ctype not in CHANNELS:
        return None
    plain = ctype in (0, 2, 3) and b"tRNS" not in chunks
    return _png_direct(w, h, depth, ctype, chunks) if plain else _png_decoded(w, h, depth, ctype, chunks)


def _jpeg(data: bytes) -> PdfImage | None:
    pos = 2
    while pos + 9 < len(data):
        if data[pos] != 0xFF:
            return None
        marker, size = data[pos + 1], struct.unpack(">H", data[pos + 2 : pos + 4])[0]
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            h, w, comps = struct.unpack(">HHB", data[pos + 5 : pos + 10])
            space = {1: "/DeviceGray", 3: "/DeviceRGB"}.get(comps)
            return PdfImage(w, h, space, 8, data, "/DCTDecode") if space else None
        pos += 2 + size
    return None


def prepare_image(content_type: str, data: bytes) -> PdfImage | None:
    """None quand le format n'est pas repris (GIF, PNG entrelacé, JPEG CMJN…) : le texte alternatif le remplace."""
    try:
        if content_type == "image/png":
            return _png(data)
        if content_type == "image/jpeg":
            return _jpeg(data)
    except (struct.error, zlib.error, IndexError, ValueError):
        return None
    return None


# ------------------------------------------------------------------ texte


class PdfError(Exception):
    """Document trop long pour être mis en page."""


@dataclass
class Span:
    text: str
    bold: bool = False
    italic: bool = False
    code: bool = False
    strike: bool = False
    url: str | None = None


@dataclass
class ImageRef:
    alt: str
    image_id: str | None


@dataclass
class Piece:
    text: str
    key: object
    size: float
    color: tuple
    url: str | None
    strike: bool
    width: float


def encode(text: str) -> bytes:
    for old, new in SUBSTITUTE.items():
        text = text.replace(old, new)
    return text.encode("cp1252", "replace")


def text_width(text: str, key, size: float) -> float:
    data = encode(text)
    if key == "mono":
        return len(data) * MONO_WIDTH * size / 1000
    table = WIDTHS_BOLD if key[0] else WIDTHS_REGULAR
    return sum(table[b] for b in data) * size / 1000


def spans_of(nodes, bold=False, italic=False, strike=False, url=None) -> list:
    out: list = []
    for n in nodes:
        kind = n[0]
        if kind in ("text", "code"):
            out.append(Span(n[1], bold, italic, kind == "code", strike, url))
        elif kind in ("strong", "em", "del"):
            out += spans_of(n[1], bold or kind == "strong", italic or kind == "em", strike or kind == "del", url)
        elif kind == "link":
            out += spans_of(n[2], bold, italic, strike, n[1] if model_md.SAFE_LINK.match(n[1]) else url)
        elif kind == "br":
            out.append(Span("\n"))
        elif kind == "image":
            match = model_md.IMAGE_RE.match(n[2])
            out.append(ImageRef(n[1], match.group(1) if match else None))
    return out


class _Wrapper:
    """Retours à la ligne au mot près, avec les largeurs exactes des polices ; un mot plus long qu'une ligne est coupé."""

    def __init__(self, width: float, size: float, bold: bool, color: tuple):
        self.width, self.size, self.bold, self.color = width, size, bold, color
        self.lines: list[list[Piece]] = [[]]
        self.used = 0.0

    def piece(self, text: str, span: Span) -> Piece:
        key = "mono" if span.code else (span.bold or self.bold, span.italic)
        color = LINK if span.url else INK if span.code and self.color == INK else self.color
        return Piece(text, key, self.size, color, span.url, span.strike, text_width(text, key, self.size))

    def newline(self) -> None:
        while self.lines[-1] and self.lines[-1][-1].text.isspace():
            self.lines[-1].pop()
        self.lines.append([])
        self.used = 0.0

    def push(self, p: Piece) -> None:
        self.lines[-1].append(p)
        self.used += p.width

    def word(self, text: str, span: Span) -> None:
        p = self.piece(text, span)
        if self.used + p.width > self.width + EPS and self.lines[-1]:
            self.newline()
        if p.width <= self.width + EPS:
            self.push(p)
            return
        for ch in text:  # mot plus large que la ligne
            c = self.piece(ch, span)
            if self.used + c.width > self.width + EPS and self.lines[-1]:
                self.newline()
            self.push(c)

    def add(self, span: Span) -> None:
        if span.text == "\n":
            self.newline()
            return
        for token in re.findall(r"\s+|\S+", span.text.replace("\n", " ")):
            if token.isspace():
                if self.lines[-1]:
                    self.push(self.piece(" ", span))
            else:
                self.word(token, span)


def wrap(spans: list, width: float, size: float, bold: bool = False, color: tuple = INK) -> list[list[Piece]]:
    w = _Wrapper(width, size, bold, color)
    for s in spans:
        if isinstance(s, Span):
            w.add(s)
    w.newline()
    return [ln for ln in w.lines if ln] or [[]]


def natural_width(spans: list, size: float, bold: bool = False) -> tuple[float, float]:
    """(largeur sur une seule ligne, largeur du plus long mot) : de quoi dimensionner les colonnes d'un tableau."""
    total, longest = 0.0, 0.0
    for s in spans:
        if isinstance(s, Span):
            key = "mono" if s.code else (s.bold or bold, s.italic)
            total += text_width(s.text, key, size)
            longest = max([longest, *(text_width(t, key, size) for t in s.text.split())])
    return total, longest


# ------------------------------------------------------------------ mise en page


def _esc(data: bytes) -> bytes:
    return data.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def _rgb(c: tuple) -> bytes:
    return b"%.3f %.3f %.3f" % c


@dataclass
class _Page:
    ops: list[bytes] = field(default_factory=list)
    links: list[tuple[float, float, float, float, str]] = field(default_factory=list)
    images: list[tuple[str, float, float, float, float]] = field(default_factory=list)


class Layout:
    """Pose les blocs d'un modèle sur des pages A4. `self.y` est le haut de la prochaine ligne ; une page pleine en ouvre une autre."""

    def __init__(self, images: dict[str, PdfImage | None]):
        self.images = images
        self.pages = [_Page()]
        self.y = PAGE_H - MARGIN

    # -- pages et dessin
    @property
    def page(self) -> _Page:
        return self.pages[-1]

    def new_page(self) -> None:
        if len(self.pages) >= MAX_PAGES:
            raise PdfError(f"Le document dépasserait {MAX_PAGES} pages.")
        self.pages.append(_Page())
        self.y = PAGE_H - MARGIN

    def ensure(self, height: float) -> None:
        if self.y - height < MARGIN and self.y < PAGE_H - MARGIN:
            self.new_page()

    def rect(
        self,
        x: float,
        y: float,
        w: float,
        h: float,
        fill: tuple | None = None,
        stroke: tuple | None = None,
        page: _Page | None = None,
    ) -> None:
        ops = [b"q"]
        if fill:
            ops.append(_rgb(fill) + b" rg")
        if stroke:
            ops.append(_rgb(stroke) + b" RG 0.6 w")
        ops.append(b"%.2f %.2f %.2f %.2f re %s Q" % (x, y, w, h, b"B" if fill and stroke else b"f" if fill else b"S"))
        (page or self.page).ops.append(b" ".join(ops))

    def hline(
        self, x: float, y: float, w: float, color: tuple, thickness: float = 0.7, page: _Page | None = None
    ) -> None:
        (page or self.page).ops.append(
            b"q %s RG %.2f w %.2f %.2f m %.2f %.2f l S Q" % (_rgb(color), thickness, x, y, x + w, y)
        )

    @staticmethod
    def merged(line: list[Piece]) -> list[Piece]:
        """Les mots voisins de même style forment un seul morceau (espaces compris) : texte copiable, recherchable, fichier plus léger."""
        out: list[Piece] = []
        for p in line:
            last = out[-1] if out else None
            if last and (last.key, last.size, last.color, last.url, last.strike) == (
                p.key,
                p.size,
                p.color,
                p.url,
                p.strike,
            ):
                last.text, last.width = last.text + p.text, last.width + p.width
            else:
                out.append(Piece(p.text, p.key, p.size, p.color, p.url, p.strike, p.width))
        return out

    def draw_line(self, line: list[Piece], x: float, baseline: float, width: float, align: str = "left") -> None:
        total = sum(p.width for p in line)
        cursor = x + (width - total) / 2 if align == "center" else x + width - total if align == "right" else x
        for p in self.merged(line):
            if p.text.strip():
                font = FONTS[p.key]
                self.page.ops.append(
                    b"BT /%s %.2f Tf %s rg %.2f %.2f Td (%s) Tj ET"
                    % (font.encode(), p.size, _rgb(p.color), cursor, baseline, _esc(encode(p.text)))
                )
            if p.url:
                self.hline(cursor, baseline - 1.5, p.width, p.color, 0.5)
                self.page.links.append((cursor, baseline - 3, cursor + p.width, baseline + p.size, p.url))
            if p.strike:
                self.hline(cursor, baseline + p.size * 0.3, p.width, p.color, 0.6)
            cursor += p.width

    # -- texte
    def paragraph(
        self,
        spans: list,
        x: float,
        width: float,
        size: float = 11,
        color: tuple = INK,
        bold: bool = False,
        after: float = 6,
        leading: float | None = None,
    ) -> None:
        run: list = []
        for s in [*spans, None]:
            if isinstance(s, ImageRef) or s is None:
                self._text_run(run, x, width, size, color, bold, leading)
                run = []
                if s is not None:
                    self.image(s, x, width)
            else:
                run.append(s)
        self.y -= after

    def _text_run(
        self, run: list, x: float, width: float, size: float, color: tuple, bold: bool, leading: float | None
    ) -> None:
        if not any(isinstance(s, Span) and s.text.strip() for s in run):
            return
        step = leading or size * 1.45
        for line in wrap(run, width, size, bold, color):
            self.ensure(step)
            self.draw_line(line, x, self.y - size, width)
            self.y -= step

    def heading(self, level: int, spans: list, x: float, width: float) -> None:
        size = HEADING_SIZE.get(level, 11)
        if self.y < PAGE_H - MARGIN:
            self.y -= size * 0.7
        self.ensure(size * 3)
        self.paragraph(spans, x, width, size, (0.07, 0.09, 0.15), True, size * 0.3, size * 1.25)

    # -- blocs
    def block(self, b: tuple, x: float, width: float, color: tuple = INK) -> None:
        kind = b[0]
        if kind == "heading":
            self.heading(b[1], spans_of(b[2]), x, width)
        elif kind == "para":
            self.paragraph(spans_of(b[1]), x, width, color=color)
        elif kind == "hr":
            self.ensure(16)
            self.hline(x, self.y - 7, width, RULE)
            self.y -= 16
        elif kind == "code":
            self.code(b[2], x, width)
        elif kind == "quote":
            self.quote(b[1], x, width)
        elif kind in ("ul", "ol"):
            self.items(b, x, width, color)
        elif kind == "table":
            self.table(b, x, width)

    def blocks(self, blocks: list, x: float, width: float, color: tuple = INK) -> None:
        for b in blocks:
            self.block(b, x, width, color)

    def items(self, b: tuple, x: float, width: float, color: tuple) -> None:
        indent = 20.0
        for n, item in enumerate(b[2], b[1]):
            marker = "•" if b[0] == "ul" else f"{n}."
            for j, sub in enumerate(item):
                if j == 0 and sub[0] == "para":
                    self.ensure(16)
                    mark = Piece(marker, (False, False), 11, color, None, False, text_width(marker, (False, False), 11))
                    self.draw_line([mark], x + indent - 6 - mark.width, self.y - 11, mark.width)
                    self.paragraph(spans_of(sub[1]), x + indent, width - indent, color=color, after=2)
                else:
                    self.block(sub, x + indent, width - indent, color)
        self.y -= 4

    def quote(self, blocks: list, x: float, width: float) -> None:
        start_page, start_y = len(self.pages) - 1, self.y
        self.blocks(blocks, x + 16, width - 16, MUTED)
        for i in range(start_page, len(self.pages)):
            top = start_y if i == start_page else PAGE_H - MARGIN
            bottom = self.y if i == len(self.pages) - 1 else MARGIN
            self.rect(x + 2, bottom, 3, max(top - bottom, 0), fill=RULE, page=self.pages[i])

    def code(self, text: str, x: float, width: float) -> None:
        size, step = 9.0, 12.5
        per_line = max(int((width - 12) / (size * MONO_WIDTH / 1000)), 8)
        for raw in text.split("\n"):
            for start in range(0, max(len(raw), 1), per_line):
                self.ensure(step)
                self.rect(x, self.y - step, width, step, fill=SHADE)
                piece = Piece(raw[start : start + per_line], "mono", size, INK, None, False, 0)
                self.draw_line([piece], x + 6, self.y - 9.5, width)
                self.y -= step
        self.y -= 6

    def image(self, ref: ImageRef, x: float, width: float) -> None:
        img = self.images.get(ref.image_id or "")
        if img is None:  # format non repris, ou image absente : son texte alternatif
            self.paragraph([Span(f"[{ref.alt or 'image'}]", italic=True)], x, width, color=MUTED)
            return
        w, h = img.width * 0.75, img.height * 0.75
        scale = min(1.0, width / w, PAGE_H * 0.45 / h)
        w, h = w * scale, h * scale
        self.ensure(h + 8)
        self.page.images.append((ref.image_id or "", x, self.y - h, w, h))
        self.y -= h + 8

    # -- tableau
    def column_widths(self, cells: list[list[list]], width: float) -> list[float]:
        pad, size = 5.0, 10.0
        natural, longest = [], []
        for j in range(len(cells[0])):
            col = [natural_width(row[j], size, bold=(i == 0)) for i, row in enumerate(cells)]
            natural.append(max(c[0] for c in col) + 2 * pad)
            longest.append(max(c[1] for c in col) + 2 * pad)
        if sum(natural) <= width:
            return natural
        spare = max(width - sum(longest), 0)
        extra = [n - m for n, m in zip(natural, longest, strict=True)]
        return [m + spare * e / (sum(extra) or 1) for m, e in zip(longest, extra, strict=True)]

    def table(self, b: tuple, x: float, width: float) -> None:
        pad, size, step = 5.0, 10.0, 14.0
        aligns = b[2]
        cells = [[spans_of(c) for c in b[1]], *([spans_of(c) for c in row] for row in b[3])]
        cols = self.column_widths(cells, width)
        for i, row in enumerate(cells):
            wrapped = [wrap(cell, cols[j] - 2 * pad, size, bold=(i == 0)) for j, cell in enumerate(row)]
            height = max(len(w) for w in wrapped) * step + 2 * pad - (step - size) / 2
            self.ensure(height)
            left = x
            for j, lines in enumerate(wrapped):
                self.rect(left, self.y - height, cols[j], height, fill=HEAD_BG if i == 0 else None, stroke=RULE)
                for k, line in enumerate(lines):
                    self.draw_line(
                        line,
                        left + pad,
                        self.y - pad - size - k * step,
                        cols[j] - 2 * pad,
                        aligns[j] if j < len(aligns) and aligns[j] else "left",
                    )
                left += cols[j]
            self.y -= height
        self.y -= 8


# ------------------------------------------------------------------ assemblage


class _Writer:
    def __init__(self) -> None:
        self.objects: list[bytes] = []

    def add(self, body: bytes) -> int:
        self.objects.append(body)
        return len(self.objects)

    def reserve(self) -> int:
        return self.add(b"")

    def set(self, number: int, body: bytes) -> None:
        self.objects[number - 1] = body

    def stream(self, header: bytes, data: bytes) -> bytes:
        return b"<< %s /Length %d >>\nstream\n" % (header, len(data)) + data + b"\nendstream"

    def image(self, img: PdfImage) -> int:
        header = b"/Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace %s /BitsPerComponent %d /Filter %s" % (
            img.width, img.height, img.colorspace.encode(), img.bits, img.filter.encode(),
        )  # fmt: skip
        if img.parms:
            header += b" /DecodeParms << %s >>" % img.parms.encode()
        if img.smask is not None:
            header += b" /SMask %d 0 R" % self.image(img.smask)
        return self.add(self.stream(header, img.data))

    def finish(self, root: int, info: int) -> bytes:
        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = []
        for number, body in enumerate(self.objects, 1):
            offsets.append(len(out))
            out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
        start = len(out)
        out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(offsets) + 1)
        out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
        out += b"trailer\n<< /Size %d /Root %d 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
            len(offsets) + 1,
            root,
            info,
            start,
        )
        return bytes(out)


def _footer(layout: Layout) -> None:
    total = len(layout.pages)
    for n, page in enumerate(layout.pages, 1):
        text = f"Page {n} / {total}"
        piece = Piece(text, (False, False), 8, MUTED, None, False, text_width(text, (False, False), 8))
        saved = layout.pages
        layout.pages = [page]
        layout.draw_line([piece], MARGIN, 28, PAGE_W - 2 * MARGIN, "center")
        layout.pages = saved


def assemble(layout: Layout, title: str) -> bytes:
    _footer(layout)
    w = _Writer()
    catalog, pages = w.reserve(), w.reserve()
    fonts = {}
    for key, name in (
        ("F1", "Helvetica"),
        ("F2", "Helvetica-Bold"),
        ("F3", "Helvetica-Oblique"),
        ("F4", "Helvetica-BoldOblique"),
        ("F5", "Courier"),
    ):
        fonts[key] = w.add(
            b"<< /Type /Font /Subtype /Type1 /BaseFont /%s /Encoding /WinAnsiEncoding >>" % name.encode()
        )
    image_objects: dict[str, int] = {}
    kids = []
    for page in layout.pages:
        ops = list(page.ops)
        names: dict[str, str] = {}
        for image_id, x, y, iw, ih in page.images:
            if image_id not in image_objects:
                image_objects[image_id] = w.image(layout.images[image_id])  # type: ignore[arg-type]
            number = list(image_objects).index(image_id) + 1
            names[image_id] = f"Im{number}"
            ops.append(b"q %.2f 0 0 %.2f %.2f %.2f cm /%s Do Q" % (iw, ih, x, y, names[image_id].encode()))
        content = w.add(w.stream(b"/Filter /FlateDecode", zlib.compress(b"\n".join(ops))))
        annots = [
            w.add(
                b"<< /Type /Annot /Subtype /Link /Rect [%.2f %.2f %.2f %.2f] /Border [0 0 0] /A << /S /URI /URI (%s) >> >>"
                % (x1, y1, x2, y2, _esc(url.encode("ascii", "ignore")))
            )
            for x1, y1, x2, y2, url in page.links
        ]
        font_res = b" ".join(b"/%s %d 0 R" % (k.encode(), v) for k, v in fonts.items())
        xobj = b" ".join(b"/%s %d 0 R" % (n.encode(), image_objects[i]) for i, n in names.items())
        resources = b"<< /Font << %s >> /XObject << %s >> >>" % (font_res, xobj)
        arr = b" ".join(b"%d 0 R" % a for a in annots)
        kids.append(
            w.add(
                b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %d %d] /Contents %d 0 R /Resources %s /Annots [%s] >>"
                % (pages, PAGE_W, PAGE_H, content, resources, arr)
            )
        )
    w.set(catalog, b"<< /Type /Catalog /Pages %d 0 R >>" % pages)
    w.set(pages, b"<< /Type /Pages /Kids [%s] /Count %d >>" % (b" ".join(b"%d 0 R" % k for k in kids), len(kids)))
    info = w.add(b"<< /Title (%s) /Producer (Fluxonata) >>" % _esc(encode(title or "Document")))
    return w.finish(catalog, info)


def render(title: str, blocks: list, images: dict[str, tuple[str, bytes]]) -> bytes:
    """Le PDF d'un modèle : `images` associe l'identifiant d'une image de l'espace à (type, octets)."""
    ready = {i: prepare_image(ctype, bytes(data)) for i, (ctype, data) in images.items()}
    layout = Layout(ready)
    width = PAGE_W - 2 * MARGIN
    if title:
        layout.heading(1, [Span(title, bold=True)], MARGIN, width)
    layout.blocks(blocks, MARGIN, width)
    return assemble(layout, title)
