"""Nœud « Créer un fichier » (ADR 0035) : fabrique un texte, un CSV, un PDF ou une archive ZIP et le garde avec l'exécution.

S'exécute dans l'orchestrateur (calcul borné en mémoire, jamais de réseau ni de code utilisateur) ; le résultat est un `RunFile`
(10 Mo au plus, 50 Mo par exécution) que l'on télécharge depuis la page de l'exécution. Aucune dépendance ajoutée : le PDF est écrit
à la main (PDF mis en page avec polices standard Helvetica et Courier, accents français compris : `runs/pdfdoc.py`)."""

import csv
import io
import zipfile

from workflows import file_spec as spec
from workflows import model_md
from workflows.expressions import ExpressionError, resolve

from . import files, model_docs, ooxml


class FileStepError(Exception):
    def __init__(self, code, message, next_step="Corrigez la configuration puis relancez."):
        super().__init__(message)
        self.code, self.message, self.next_step = code, message, next_step


def _text(value, limit: int, what: str) -> str:
    text = "" if value is None else value if isinstance(value, str) else str(value)
    if len(text) > limit:
        raise FileStepError("FILE_CONTENT", f"{what} dépasse {limit:,} caractères.".replace(",", " "))
    return text


def _name(node, ctx) -> str:
    raw = str(resolve(node.get("filename", ""), ctx) or "").strip()
    if not spec.NAME_RE.match(raw):
        raise FileStepError(
            "FILE_NAME", "Nom de fichier vide ou invalide (pas de dossier ni de caractère de contrôle)."
        )
    name = files.safe_name(raw)
    ext = spec.EXTENSIONS[node["format"]]
    if node["format"] != "text" and not name.lower().endswith(ext):
        name += ext
    if node["format"] == "text" and "." not in name:
        name += ext
    return name


def _cell(v):
    if isinstance(v, (dict, list)):
        return str(v)
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
        try:
            float(v)
        except ValueError:
            return "'" + v  # un tableur n'exécute pas une cellule qui commence par une formule
    return "" if v is None else v


def _table(node, ctx) -> tuple[list, list[dict]]:
    rows = resolve(node.get("rows"), ctx)
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        raise FileStepError(
            "FILE_CONTENT", "Lignes du tableau : une liste d'objets est attendue (ex. une sortie « rows »)."
        )
    if len(rows) > spec.MAX_CSV_ROWS:
        raise FileStepError("FILE_CONTENT", f"Au plus {spec.MAX_CSV_ROWS} lignes.")
    cols = node.get("columns") or list(dict.fromkeys(k for r in rows for k in r))
    return cols, rows


def _csv(node, ctx) -> bytes:
    cols, rows = _table(node, ctx)
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow([_cell(str(c)) for c in cols])
    for r in rows:
        w.writerow([_cell(r.get(c)) for c in cols])
    return ("\ufeff" + buf.getvalue()).encode("utf-8")  # BOM : Excel lit correctement les accents


def _xlsx(node, ctx) -> bytes:
    cols, rows = _table(node, ctx)
    plain = [
        [v if isinstance(v, (str, int, float, bool)) or v is None else str(v) for v in (r.get(c) for c in cols)]
        for r in rows
    ]
    return ooxml.xlsx([str(c) for c in cols], plain, resolve(node.get("sheet") or "", ctx))


def _refs(node, ctx) -> list:
    out: list = []
    for item in node.get("sources") or []:
        value = resolve(item, ctx)
        out += value if isinstance(value, list) else [value]
    return out


def _zip(run, node, ctx) -> bytes:
    refs = _refs(node, ctx)
    if not refs:
        raise FileStepError("FILE_CONTENT", "Aucun fichier à mettre dans l'archive.")
    if len(refs) > spec.MAX_ZIP_FILES:
        raise FileStepError("FILE_CONTENT", f"Au plus {spec.MAX_ZIP_FILES} fichiers dans une archive.")
    buf, seen, total = io.BytesIO(), set(), 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for r in refs:
            f = files.load(run, r)
            total += f.size
            if total > files.MAX_FILE:
                raise FileStepError(
                    "FILE_TOO_BIG", f"Les fichiers à archiver dépassent {files.MAX_FILE // 1024 // 1024} Mo."
                )
            name, n = f.name, 1
            while name in seen:
                n += 1
                stem, dot, ext = f.name.rpartition(".")
                name = f"{stem}-{n}.{ext}" if dot else f"{f.name}-{n}"
            seen.add(name)
            z.writestr(name, bytes(f.data))
    return buf.getvalue()


def _content(node, ctx, limit=spec.MAX_TEXT_CHARS) -> str:
    return _text(resolve(node.get("content", ""), ctx), limit, "Le texte")


def _title(node, ctx) -> str:
    return _text(resolve(node.get("title", ""), ctx), 200, "Le titre")


def _pptx(node, ctx) -> bytes:
    slides = ooxml.split_slides(_content(node, ctx, spec.MAX_PDF_CHARS), _title(node, ctx))
    if not slides:
        raise FileStepError(
            "FILE_CONTENT", "Aucune diapositive : écrivez un titre (séparez les diapositives par une ligne ---)."
        )
    if len(slides) > spec.MAX_SLIDES:
        raise FileStepError("FILE_CONTENT", f"Au plus {spec.MAX_SLIDES} diapositives.")
    return ooxml.pptx(slides)


def _model(node, ctx) -> tuple[str, str] | None:
    """(texte Markdown, titre du modèle) quand le fichier est fait d'un modèle de document."""
    if not node.get("model"):
        return None
    value = resolve(node["model"], ctx)
    return model_docs.markdown_of(value), str(value.get("title") or "") if isinstance(value, dict) else ""


def plain_as_markdown(text: str) -> str:
    """Un texte saisi sans mise en forme garde ses retours à la ligne (saut forcé en fin de ligne) ; le Markdown qu'il contient est rendu."""
    return "\n".join(line + "  " if line.strip() else line for line in text.replace("\r\n", "\n").split("\n"))


def _from_model(run, node, ctx, kind, source: str, model_title: str) -> bytes:
    title = _title(node, ctx) or model_title
    images = model_docs.load_images(run.workspace_id, source)
    if kind == "html":
        return model_docs.html_document(title, source, images).encode("utf-8")
    if kind == "docx":
        blocks = model_md.parse_blocks(source)
        return ooxml.docx_model(model_docs.show_title(title, blocks), blocks, images)
    if kind == "pdf":
        return model_docs.pdf_document(title, source, images)
    if kind == "pptx":
        blocks = model_md.parse_blocks(source)
        if not blocks:
            raise FileStepError("FILE_CONTENT", "Le modèle est vide : aucune diapositive.")
        return ooxml.pptx_model(model_docs.show_title(title, blocks), blocks, images)
    return source.encode("utf-8")


def _build(run, node, ctx, name) -> bytes:
    kind = node["format"]
    model = _model(node, ctx) if kind in spec.TEXT_FORMATS else None
    if model is not None:
        return _from_model(run, node, ctx, kind, *model)
    if kind == "html":
        source = _content(node, ctx)
        return model_docs.html_document(
            _title(node, ctx), source, model_docs.load_images(run.workspace_id, source)
        ).encode("utf-8")
    if kind == "zip":
        return _zip(run, node, ctx)
    if kind == "csv":
        return _csv(node, ctx)
    if kind == "xlsx":
        return _xlsx(node, ctx)
    if kind == "pdf":
        return model_docs.pdf_document(
            _title(node, ctx), plain_as_markdown(_content(node, ctx, spec.MAX_PDF_CHARS)), {}
        )
    if kind == "docx":
        return ooxml.docx(_title(node, ctx), _content(node, ctx))
    if kind == "pptx":
        return _pptx(node, ctx)
    return _content(node, ctx).encode("utf-8")


def apply(run, node, ctx) -> dict:
    try:
        name = _name(node, ctx)
        data = _build(run, node, ctx, name)
        ctype = spec.CONTENT_TYPES.get(node["format"]) or spec.TEXT_TYPES.get(
            name[name.rfind(".") :].lower(), "text/plain"
        )
        ref = files.save(run, node["id"], name, ctype, data)
    except ExpressionError as exc:
        raise FileStepError("FILE_CONTENT", str(exc)) from None
    except files.FileError as exc:
        raise FileStepError(exc.code, exc.message, exc.next_step) from None
    except model_docs.ModelError as exc:
        raise FileStepError(exc.code, exc.message, exc.next_step) from None
    return {"file": ref, "name": ref["name"], "size": ref["size"]}
