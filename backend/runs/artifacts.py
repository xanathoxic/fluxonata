"""Artifact storage behind a small interface (filesystem for dev; an S3-compatible store can implement it)."""

import base64
import binascii
import hashlib
import re
import uuid
from pathlib import Path
from typing import Protocol

from django.conf import settings

MAX_FILES = 10
MAX_TOTAL_BYTES = 5 * 1024 * 1024
SAFE_INLINE_TYPES = {"text/plain", "application/json", "text/csv", "text/markdown"}


class ArtifactStore(Protocol):
    def put(self, key: str, data: bytes) -> None: ...
    def get(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...


class FileSystemStore:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if self.root not in p.parents:  # traversal guard
            raise ValueError("Clé de stockage invalide.")
        return p

    def put(self, key: str, data: bytes) -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(p)

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)


def get_store() -> ArtifactStore:
    return FileSystemStore(Path(settings.ORCH_ARTIFACTS_DIR))


def safe_name(name: str) -> str:
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", str(name).replace("\\", "/").split("/")[-1]).strip("._") or "artefact"
    return base[:100]


def extract_artifacts(result):
    """Pop the reserved `__artifacts__` key from an action result. Returns (clean_result, [(name, type, bytes)])."""
    if not isinstance(result, dict) or "__artifacts__" not in result:
        return result, []
    clean = {k: v for k, v in result.items() if k != "__artifacts__"}
    items = result["__artifacts__"]
    if not isinstance(items, list) or len(items) > MAX_FILES:
        raise ValueError(f"Au plus {MAX_FILES} artefacts par exécution.")
    out, total = [], 0
    for it in items:
        if not isinstance(it, dict) or "name" not in it:
            raise ValueError("Artefact mal formé (nom manquant).")
        if "text" in it:
            data = str(it["text"]).encode()
        elif "base64" in it:
            try:
                data = base64.b64decode(it["base64"], validate=True)
            except (binascii.Error, ValueError):
                raise ValueError("Artefact base64 invalide.") from None
        else:
            raise ValueError("Artefact sans contenu (text ou base64).")
        total += len(data)
        if total > MAX_TOTAL_BYTES:
            raise ValueError("Les artefacts dépassent la taille totale autorisée (5 Mo).")
        out.append((safe_name(it["name"]), str(it.get("content_type", "application/octet-stream"))[:100], data))
    return clean, out


def store_artifacts(execution, items):
    from .models import Artifact

    store = get_store()
    rows = []
    for name, ctype, data in items:
        key = f"{execution.workspace_id}/{execution.id}/{uuid.uuid4().hex}"
        store.put(key, data)
        rows.append(
            Artifact.objects.create(
                workspace=execution.workspace,
                execution=execution,
                name=name,
                content_type=ctype,
                size=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
                storage_key=key,
            )
        )
    return rows
