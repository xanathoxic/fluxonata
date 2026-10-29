"""Files exchanged by steps (binary content): stored with the run in PostgreSQL (`RunFile`), passed between steps as a small
reference `{"file_id", "name", "size", "content_type", "sha256"}` — the bytes never enter step outputs, the journal or the
audit trail.

Safety: 10 MB per file and 50 MB per run at most; a reference can only be used by the run that owns the file or by a run of
the same family (parent / sub-workflows), always inside one workspace; downloads force `application/octet-stream`, `nosniff`
and a sandboxing CSP; names are reduced to a safe base name."""

import hashlib
import re

from django.db.models import Sum

MAX_FILE = 10 * 1024 * 1024
MAX_RUN = 50 * 1024 * 1024


class FileError(Exception):
    def __init__(self, code: str, message: str, next_step: str = "Corrigez la configuration de l'étape."):
        super().__init__(message)
        self.code, self.message, self.next_step = code, message, next_step


def safe_name(name, default: str = "fichier") -> str:
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", str(name or "").replace("\\", "/").split("/")[-1]).strip("._")
    return (base or default)[:100]


def ref(f) -> dict:
    return {"file_id": str(f.pk), "name": f.name, "size": f.size, "content_type": f.content_type, "sha256": f.sha256}


def file_id_of(value) -> str | None:
    """The id in a reference (`{file_id: …}`) or a bare id string; None when it is neither."""
    if isinstance(value, dict):
        value = value.get("file_id")
    return value.strip() if isinstance(value, str) and value.strip() else None


def _root(run):
    while run.parent_step_id:
        run = run.parent_step.run
    return run


def save(run, step_id: str, name, content_type: str, data: bytes) -> dict:
    from .models import RunFile

    if len(data) > MAX_FILE:
        raise FileError("FILE_TOO_BIG", f"Fichier trop volumineux ({MAX_FILE // 1024 // 1024} Mo au plus).")
    used = RunFile.objects.filter(run=run).aggregate(n=Sum("size"))["n"] or 0
    if used + len(data) > MAX_RUN:
        raise FileError("FILE_QUOTA", f"Les fichiers de cette exécution dépassent {MAX_RUN // 1024 // 1024} Mo.")
    f = RunFile.objects.create(
        workspace=run.workspace,
        run=run,
        step_id=str(step_id)[:100],
        name=safe_name(name),
        content_type=(
            str(content_type or "application/octet-stream").split(";")[0].strip() or "application/octet-stream"
        )[:100],
        size=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        data=data,
    )
    return ref(f)


def load(run, value):
    """The RunFile a reference points to, if it belongs to this run's family; raises FileError otherwise."""
    import uuid

    from .models import RunFile

    raw = file_id_of(value)
    try:
        pk = uuid.UUID(raw) if raw else None
    except ValueError:
        pk = None
    f = RunFile.objects.filter(pk=pk, workspace_id=run.workspace_id).select_related("run").first() if pk else None
    if f is None or _root(f.run).pk != _root(run).pk:
        raise FileError(
            "FILE_MISSING",
            "Fichier introuvable : il doit provenir d'une étape de cette exécution.",
            "Utilisez la sortie « file » d'une étape précédente.",
        )
    return f
