"""Image de marque de la plate-forme (ADR 0033) : nom, logos, couleur d'accent, page de connexion.

Tout ce module est sans effet de bord : validation d'une couleur (contraste WCAG 2.2 AA), validation d'une image (PNG, JPEG, WebP ou
SVG assaini). Une image téléversée n'est jamais exécutée : elle est servie en `<img>` avec un `Content-Security-Policy: sandbox`.
"""

from __future__ import annotations

import hashlib
import re
import struct
from dataclasses import dataclass

from lxml import etree

DEFAULT_NAME = "Fluxonata"
DEFAULT_ACCENT = "#0e7490"
MAX_IMAGE_BYTES = 256 * 1024
MAX_SIDE = 2048  # pixels, image matricielle
MIN_CONTRAST = 4.5  # texte normal, WCAG 2.2 AA
LOGIN_STYLES = ("plain", "tint", "gradient")
KINDS = ("logo", "logo_dark", "icon")
HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

# Fonds sur lesquels l'accent sert de couleur de texte (liens, onglet actif, élément de menu actif) : thème.css
WHITE = "#ffffff"
PAGE_BG = "#f6f8fb"
SOFT_WEIGHT = 0.12  # part d'accent mélangée au blanc pour le fond doux


class BrandingError(ValueError):
    """Message destiné à la personne qui règle l'image de marque."""


# ───────── Couleurs ─────────


def _rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _lin(c: int) -> float:
    v = c / 255
    return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4


def luminance(hex_color: str) -> float:
    r, g, b = _rgb(hex_color)
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def contrast(a: str, b: str) -> float:
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def mix(a: str, b: str, weight_b: float) -> str:
    ra, ga, ba = _rgb(a)
    rb, gb, bb = _rgb(b)
    return f"#{round(ra + (rb - ra) * weight_b):02x}{round(ga + (gb - ga) * weight_b):02x}{round(ba + (bb - ba) * weight_b):02x}"


def soft(accent: str) -> str:
    """Fond doux de l'accent (élément de menu actif, survol) : le même calcul existe côté interface (frontend/src/branding/palette.ts)."""
    return mix(WHITE, accent, SOFT_WEIGHT)


def contrast_report(accent: str) -> dict:
    """Contrastes de l'accent, utilisé en texte, sur les trois fonds de l'interface, et du texte blanc posé sur l'accent (boutons)."""
    rows = {
        "on_white": contrast(accent, WHITE),
        "on_page": contrast(accent, PAGE_BG),
        "on_soft": contrast(accent, soft(accent)),
        "button_text": contrast(accent, WHITE),
    }
    return {k: round(v, 2) for k, v in rows.items()} | {"minimum": MIN_CONTRAST}


def validate_accent(value: str) -> str:
    """`#rrggbb` en minuscules, dont le contraste respecte le seuil AA sur tous les fonds. Chaîne vide = couleur d'origine."""
    value = (value or "").strip()
    if not value:
        return ""
    if not HEX_RE.match(value):
        raise BrandingError("La couleur doit s'écrire #rrggbb (par exemple #0e7490).")
    value = value.lower()
    worst = min(contrast(value, WHITE), contrast(value, PAGE_BG), contrast(value, soft(value)))
    if worst < MIN_CONTRAST:
        raise BrandingError(
            f"Cette couleur est trop claire : son contraste est de {worst:.2f}:1 sur les fonds de l'interface, "
            f"le minimum est de {MIN_CONTRAST}:1 (texte lisible et boutons à texte blanc). Choisissez une teinte plus foncée."
        )
    return value


def validate_nav_color(value: str) -> str:
    """Fond d'une barre (haut ou gauche) : toute couleur `#rrggbb`. Le texte, les icônes et les états se déduisent du fond côté interface
    (blanc ou noir selon le fond, toujours à 4,5:1 au moins : frontend/src/branding/palette.ts, testé sur des milliers de fonds)."""
    value = (value or "").strip()
    if not value:
        return ""
    if not HEX_RE.match(value):
        raise BrandingError("La couleur doit s'écrire #rrggbb (par exemple #0f172a).")
    return value.lower()


# ───────── Images ─────────


@dataclass(frozen=True)
class Image:
    data: bytes
    content_type: str
    sha256: str
    width: int | None = None
    height: int | None = None


def _png_size(b: bytes) -> tuple[int, int]:
    if len(b) < 24 or b[12:16] != b"IHDR":
        raise BrandingError("Fichier PNG illisible.")
    return struct.unpack(">II", b[16:24])


def _jpeg_size(b: bytes) -> tuple[int, int]:
    i = 2
    while i + 9 < len(b):
        if b[i] != 0xFF:
            i += 1
            continue
        marker = b[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        (length,) = struct.unpack(">H", b[i + 2 : i + 4])
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            h, w = struct.unpack(">HH", b[i + 5 : i + 9])
            return w, h
        i += 2 + length
    raise BrandingError("Fichier JPEG illisible.")


def _webp_size(b: bytes) -> tuple[int, int]:
    kind = b[12:16]
    if kind == b"VP8X" and len(b) >= 30:
        return 1 + int.from_bytes(b[24:27], "little"), 1 + int.from_bytes(b[27:30], "little")
    if kind == b"VP8 " and len(b) >= 30:
        w, h = struct.unpack("<HH", b[26:30])
        return w & 0x3FFF, h & 0x3FFF
    if kind == b"VP8L" and len(b) >= 25:
        bits = int.from_bytes(b[21:25], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    raise BrandingError("Fichier WebP illisible.")


# Éléments et attributs SVG admis : formes, dégradés, textes. Ni script, ni style, ni image incorporée, ni lien externe.
SVG_NS = "http://www.w3.org/2000/svg"
SVG_ELEMENTS = {
    "svg",
    "g",
    "defs",
    "path",
    "circle",
    "ellipse",
    "rect",
    "line",
    "polyline",
    "polygon",
    "linearGradient",
    "radialGradient",
    "stop",
    "clipPath",
    "title",
    "desc",
    "text",
    "tspan",
}
SVG_ATTRIBUTES = {
    "xmlns",
    "viewBox",
    "width",
    "height",
    "preserveAspectRatio",
    "version",
    "id",
    "class",
    "d",
    "cx",
    "cy",
    "r",
    "rx",
    "ry",
    "x",
    "y",
    "x1",
    "y1",
    "x2",
    "y2",
    "points",
    "transform",
    "fill",
    "fill-opacity",
    "fill-rule",
    "clip-rule",
    "stroke",
    "stroke-width",
    "stroke-linecap",
    "stroke-linejoin",
    "stroke-miterlimit",
    "stroke-dasharray",
    "stroke-dashoffset",
    "stroke-opacity",
    "opacity",
    "offset",
    "stop-color",
    "stop-opacity",
    "gradientUnits",
    "gradientTransform",
    "spreadMethod",
    "clip-path",
    "clipPathUnits",
    "font-family",
    "font-size",
    "font-weight",
    "text-anchor",
    "dominant-baseline",
    "letter-spacing",
    "role",
    "aria-label",
    "focusable",
}
_PAINT = ("fill", "stroke", "clip-path")


def _check_svg_value(name: str, value: str) -> None:
    low = value.lower()
    if "javascript:" in low or "data:" in low or "<" in value or "@import" in low:
        raise BrandingError("Le SVG contient une valeur interdite.")
    if "url(" in low and not (name in _PAINT and re.fullmatch(r"\s*url\(#[\w.-]+\)(\s+[#\w(),.%-]+)?\s*", value)):
        raise BrandingError("Le SVG ne peut référencer que ses propres dégradés (url(#…)).")


def _check_svg_element(el) -> None:
    if not isinstance(el.tag, str):  # commentaire, instruction de traitement
        raise BrandingError("Le SVG contient une instruction interdite.")
    q = etree.QName(el)
    if q.namespace != SVG_NS or q.localname not in SVG_ELEMENTS:
        raise BrandingError(f"Le SVG contient un élément interdit : « {q.localname} ».")
    for attr, value in el.attrib.items():
        name = etree.QName(attr).localname if attr.startswith("{") else attr
        if attr.startswith("{") or name not in SVG_ATTRIBUTES:
            raise BrandingError(f"Le SVG contient un attribut interdit : « {name} ».")
        _check_svg_value(name, value)
    if el.text and q.localname not in ("text", "tspan", "title", "desc") and el.text.strip():
        raise BrandingError("Le SVG contient du texte hors d'un élément de texte.")


def _svg_size(root) -> tuple[int, int]:
    width, height = _svg_len(root.get("width")), _svg_len(root.get("height"))
    box = (root.get("viewBox") or "").replace(",", " ").split()
    if len(box) == 4:
        try:
            width, height = width or round(float(box[2])), height or round(float(box[3]))
        except ValueError:
            raise BrandingError("L'attribut viewBox du SVG est invalide.") from None
    if not (width and height):
        raise BrandingError("Le SVG doit déclarer un viewBox (ou une largeur et une hauteur).")
    return width, height


def clean_svg(raw: bytes) -> tuple[bytes, int, int]:
    """Relit le SVG avec un analyseur sans entités ni réseau, refuse tout ce qui n'est pas une forme (script, style, image, lien,
    objet étranger…) et renvoie un SVG réécrit par nos soins : ce qui est servi n'est jamais l'octet reçu."""
    low = raw[:4096].lower()
    if b"<!doctype" in low or b"<!entity" in raw.lower():
        raise BrandingError("Le SVG ne doit contenir ni DOCTYPE ni entité.")
    parser = etree.XMLParser(
        resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False, remove_comments=True
    )
    try:
        root = etree.fromstring(raw, parser)
    except etree.XMLSyntaxError:
        raise BrandingError("Fichier SVG illisible.") from None
    if etree.QName(root).localname != "svg":
        raise BrandingError("Le fichier n'est pas un SVG.")
    for count, el in enumerate(root.iter(), start=1):
        if count > 2000:
            raise BrandingError("Ce SVG est trop complexe (plus de 2000 éléments).")
        _check_svg_element(el)
    width, height = _svg_size(root)
    out = etree.tostring(root, xml_declaration=False, encoding="utf-8")
    return out, width, height


def _svg_len(v: str | None) -> int | None:
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)(px)?\s*", v or "")
    return round(float(m.group(1))) if m else None


def inspect_image(raw: bytes) -> Image:
    """Type reconnu à son contenu (jamais au nom du fichier), taille bornée, dimensions bornées ; SVG assaini."""
    if not raw:
        raise BrandingError("Le fichier est vide.")
    if len(raw) > MAX_IMAGE_BYTES:
        raise BrandingError(f"Le fichier dépasse {MAX_IMAGE_BYTES // 1024} Ko.")
    w: int
    h: int
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        ctype, (w, h) = "image/png", _png_size(raw)
    elif raw.startswith(b"\xff\xd8\xff"):
        ctype, (w, h) = "image/jpeg", _jpeg_size(raw)
    elif raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        ctype, (w, h) = "image/webp", _webp_size(raw)
    elif raw.lstrip()[:1] == b"<":
        raw, w, h = clean_svg(raw)
        ctype = "image/svg+xml"
    else:
        raise BrandingError("Format non pris en charge : utilisez PNG, JPEG, WebP ou SVG.")
    if not (0 < w <= MAX_SIDE * 8 and 0 < h <= MAX_SIDE * 8) or (
        ctype != "image/svg+xml" and (w > MAX_SIDE or h > MAX_SIDE)
    ):
        raise BrandingError(f"L'image est trop grande : {MAX_SIDE} × {MAX_SIDE} pixels au plus.")
    return Image(raw, ctype, hashlib.sha256(raw).hexdigest(), w, h)


def validate_icon_shape(img: Image) -> None:
    """L'icône sert d'icône d'onglet et de pastille : elle doit être à peu près carrée."""
    if img.width and img.height and not (0.5 <= img.width / img.height <= 2):
        raise BrandingError("L'icône doit être à peu près carrée (rapport largeur/hauteur entre 1:2 et 2:1).")


def validate_https_url(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    if not re.fullmatch(r"(https://[^\s<>\"']{3,300}|mailto:[^\s<>\"',;]{3,200})", value):
        raise BrandingError("Le lien doit commencer par https:// ou mailto:.")
    return value
