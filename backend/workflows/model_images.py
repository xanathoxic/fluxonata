"""Images des modèles de document (ADR 0036) : type reconnu à son contenu, taille et dimensions bornées. PNG, JPEG et GIF seulement
(les formats que Word, PowerPoint et les messageries affichent tous) : ni SVG (script possible), ni WebP."""

import hashlib
import struct
from dataclasses import dataclass

from tenancy.branding import BrandingError, _jpeg_size, _png_size

MAX_BYTES = 1024 * 1024
MAX_SIDE = 4000
MAX_PER_WORKSPACE = 200


@dataclass(frozen=True)
class Checked:
    data: bytes
    content_type: str
    sha256: str
    width: int
    height: int


def inspect(raw: bytes) -> Checked:
    if not raw:
        raise BrandingError("Le fichier est vide.")
    if len(raw) > MAX_BYTES:
        raise BrandingError(f"L'image dépasse {MAX_BYTES // 1024} Ko : réduisez-la avant de l'ajouter.")
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        ctype, (w, h) = "image/png", _png_size(raw)
    elif raw.startswith(b"\xff\xd8\xff"):
        ctype, (w, h) = "image/jpeg", _jpeg_size(raw)
    elif raw[:6] in (b"GIF87a", b"GIF89a") and len(raw) >= 10:
        ctype, (w, h) = "image/gif", struct.unpack("<HH", raw[6:10])
    else:
        raise BrandingError("Format non pris en charge : utilisez PNG, JPEG ou GIF.")
    if not (0 < w <= MAX_SIDE and 0 < h <= MAX_SIDE):
        raise BrandingError(f"L'image est trop grande : {MAX_SIDE} × {MAX_SIDE} pixels au plus.")
    return Checked(raw, ctype, hashlib.sha256(raw).hexdigest(), w, h)
