"""Contrat du nœud « Créer un fichier » (ADR 0035) partagé par la validation (workflows) et l'exécution (runs.step_file).

Le nœud fabrique un fichier (texte, CSV, PDF, archive ZIP) et le range avec l'exécution : il se télécharge ensuite depuis la page
de l'exécution (onglet Fichiers) et se passe aux autres étapes par sa référence (`steps.<id>.output.file`)."""

import re

OPS = ("text", "csv", "xlsx", "pdf", "docx", "pptx", "html", "zip")
EXTENSIONS = {
    "text": ".txt",
    "csv": ".csv",
    "xlsx": ".xlsx",
    "pdf": ".pdf",
    "docx": ".docx",
    "pptx": ".pptx",
    "html": ".html",
    "zip": ".zip",
}
CONTENT_TYPES = {
    "csv": "text/csv",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "html": "text/html",
    "zip": "application/zip",
}
TABLE_FORMATS = ("csv", "xlsx")  # `rows` (+ `columns`)
TEXT_FORMATS = ("text", "pdf", "docx", "pptx", "html")  # `content`, ou `model` (un modèle de document)
MAX_SLIDES = 100
TEXT_TYPES = {
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".json": "application/json",
    ".html": "text/html",
    ".xml": "application/xml",
    ".csv": "text/csv",
    ".log": "text/plain",
}
MAX_TEXT_CHARS = 1_000_000
MAX_PDF_CHARS = 200_000
MAX_CSV_ROWS = 50_000
MAX_ZIP_FILES = 100
NAME_RE = re.compile(r"^[^\x00-\x1f/\\]{1,100}$")

STATIC_OUTPUTS = {"file": {"type": "object"}, "name": {"type": "string"}, "size": {"type": "integer"}}
