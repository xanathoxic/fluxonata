"""Ce que l'on fait d'un modèle de document (ADR 0036) une fois rendu : un fichier HTML autonome, un document Word, un e-mail HTML.

Les consommateurs repartent toujours du `markdown` du modèle (jamais de son `html`, qu'une étape intermédiaire aurait pu fabriquer) :
le moteur `workflows.model_md` est donc le seul à produire du HTML, et il n'accepte que les images de l'espace de travail."""

import base64
from html import escape

from workflows import model_md

MAX_MAIL_IMAGES = 10
MAX_MAIL_IMAGE_BYTES = 5 * 1024 * 1024
MAX_MAIL_HTML = 300 * 1024

CSS = (
    "body{font-family:Calibri,Arial,sans-serif;line-height:1.5;color:#1f2937;max-width:780px;margin:24px auto;padding:0 16px}"
    "h1,h2,h3{line-height:1.25}table{border-collapse:collapse}th,td{border:1px solid #9ca3af;padding:4px 8px}th{background:#e5e7eb}"
    "blockquote{border-left:4px solid #9ca3af;margin:8px 0;padding:0 12px;color:#4b5563}pre{background:#f3f4f6;padding:8px;overflow:auto}"
    "code{font-family:Consolas,monospace}img{max-width:100%;height:auto}a{color:#1d4ed8}"
)


class ModelError(Exception):
    def __init__(self, code: str, message: str, next_step: str = "Corrigez le modèle puis relancez."):
        super().__init__(message)
        self.code, self.message, self.next_step = code, message, next_step


def markdown_of(value) -> str:
    """Le texte d'un modèle reçu en entrée d'une étape (la sortie d'une étape « Modèle » : `{{ steps.m.output }}`) ou un texte brut."""
    if isinstance(value, dict) and isinstance(value.get("markdown"), str):
        text = value["markdown"]
    elif isinstance(value, str):
        text = value
    else:
        raise ModelError(
            "MODEL_REF",
            "Le modèle attendu est la sortie d'une étape « Modèle de document » (ex. {{ steps.modele.output }}).",
        )
    if len(text) > model_md.MAX_MARKDOWN:
        raise ModelError("MODEL_CONTENT", "Le modèle est trop long.")
    return text


def load_images(workspace_id, source: str) -> dict:
    """Les images citées par le texte, lues dans l'espace de travail ; une image absente fait échouer l'étape (jamais un trou silencieux)."""
    from workflows.models import DocumentImage

    ids = model_md.image_ids(source)
    if not ids:
        return {}
    rows = {str(i.pk): i for i in DocumentImage.objects.filter(workspace_id=workspace_id, pk__in=ids)}
    missing = [i for i in ids if i not in rows]
    if missing:
        raise ModelError(
            "MODEL_IMAGE",
            f"{len(missing)} image(s) du modèle introuvable(s) dans cet espace : elles ont peut-être été supprimées.",
        )
    return rows


def data_uri(img) -> str:
    return f"data:{img.content_type};base64,{base64.b64encode(bytes(img.data)).decode()}"


def show_title(title: str, blocks: list) -> str:
    """Le titre du document, sauf si le texte commence déjà par ce même titre (un titre `# …` identique) : il ne s'affiche qu'une fois."""
    first = next(
        (
            b
            for b in blocks
            if not (b[0] == "para" and all(n[0] == "image" or (n[0] == "text" and not n[1].strip()) for n in b[1]))
        ),
        None,
    )
    if (
        title
        and first is not None
        and first[0] == "heading"
        and model_md.inline_text(first[2]).strip().lower() == title.strip().lower()
    ):
        return ""
    return title


def pdf_document(title: str, source: str, images: dict) -> bytes:
    """Le même rendu que la page HTML, en PDF (mise en page, tableaux, liens, images PNG et JPEG)."""
    from . import pdfdoc

    blocks = model_md.parse_blocks(source)
    try:
        return pdfdoc.render(
            show_title(title, blocks), blocks, {i: (img.content_type, bytes(img.data)) for i, img in images.items()}
        )
    except pdfdoc.PdfError as exc:
        raise ModelError("MODEL_CONTENT", str(exc)) from None


def html_document(title: str, source: str, images: dict) -> str:
    blocks = model_md.parse_blocks(source)
    shown = show_title(title, blocks)  # le titre de la page (onglet) reste ; le <h1> n'est écrit qu'une fois
    body = model_md.blocks_html(blocks, lambda i: data_uri(images[i]) if i in images else None)
    return (
        f'<!doctype html>\n<html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{escape(title or 'Document')}</title><style>{CSS}</style></head><body>{f'<h1>{escape(shown)}</h1>' if shown else ''}{body}</body></html>\n"
    )


def mail_parts(source: str, images: dict) -> dict:
    """Corps d'un e-mail : texte brut + HTML, les images en pièces jointes intégrées (`cid:`), jamais d'adresse distante."""
    if len(images) > MAX_MAIL_IMAGES:
        raise ModelError("MODEL_IMAGE", f"Un e-mail porte {MAX_MAIL_IMAGES} images au plus.")
    if sum(len(i.data) for i in images.values()) > MAX_MAIL_IMAGE_BYTES:
        raise ModelError("MODEL_IMAGE", f"Les images d'un e-mail dépassent {MAX_MAIL_IMAGE_BYTES // 1024 // 1024} Mo.")
    cids = {i: f"img{n}.{i[:8]}@fluxonata" for n, i in enumerate(images, 1)}
    body = model_md.to_html(source, lambda i: f"cid:{cids[i]}" if i in cids else None)
    html = f'<!doctype html><html><head><meta charset="utf-8"></head><body style="font-family:Calibri,Arial,sans-serif;line-height:1.5;color:#1f2937">{body}</body></html>'
    if len(html.encode()) > MAX_MAIL_HTML:
        raise ModelError("MODEL_CONTENT", "Le message dépasse 300 Ko.")
    return {
        "text": model_md.to_text(source),
        "html": html,
        "images": [
            {"cid": cids[i], "content_type": img.content_type, "data": base64.b64encode(bytes(img.data)).decode()}
            for i, img in images.items()
        ],
    }
