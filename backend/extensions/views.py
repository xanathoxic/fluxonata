"""API des extensions : ce que l'interface doit afficher (menu, page) et les fichiers d'interface qu'une extension livre."""

import importlib
from pathlib import Path

from django.conf import settings
from django.http import FileResponse, Http404, HttpResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView

from tenancy.models import Membership
from tenancy.permissions import ADMINISTER, effective_permissions, get_workspace

from . import EXTENSION_API
from .loader import Extension

ASSET_TYPES = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".map": "application/json"}


def _held(user, ws) -> set[str]:
    """Droits de la personne dans l'espace (jeton de service compris) : la même règle que partout, côté serveur."""
    membership = Membership.objects.filter(user=user, workspace=ws).first()
    return effective_permissions(user, membership) if membership else set()


def _describe(ext: Extension, ws_slug: str, held: set[str]) -> dict | None:
    m = ext.manifest
    menu = [
        {"id": e.id, "label": e.label, "path": e.path, "group": e.group, "icon": e.icon}
        for e in m.menu
        if e.permission in held
    ]
    if m.menu and not menu:
        return None  # rien de ce que l'extension propose n'est permis à cette personne : elle n'apparaît pas
    ui = f"/api/v1/workspaces/{ws_slug}/extensions/{m.id}/ui/{m.ui_module}" if m.ui_module else None
    return {"id": m.id, "name": m.name, "version": m.version, "description": m.description, "ui": ui, "menu": menu}


class ExtensionsView(APIView):
    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request, workspace):
        ws = get_workspace(request.user, workspace)
        held = _held(request.user, ws)
        listed = [d for e in settings.EXTENSIONS if (d := _describe(e, ws.slug, held))]
        body: dict = {"api": EXTENSION_API, "extensions": listed}
        if ADMINISTER in held:  # les refus (et leur raison) ne regardent que ceux qui administrent
            body["refused"] = [{"name": r.name, "reason": r.reason} for r in settings.EXTENSIONS_REFUSED]
        return Response(body)


class ExtensionAssetView(APIView):
    """Un fichier du dossier `ui/` d'une extension. Jamais de sortie du dossier (liens symboliques compris), seulement .js, .css et .map."""

    @extend_schema(responses=OpenApiTypes.BINARY)
    def get(self, request, workspace, ext_id, file):
        get_workspace(request.user, workspace)
        ext = next((e for e in settings.EXTENSIONS if e.manifest.id == ext_id), None)
        if ext is None:
            raise Http404()
        root = (Path(importlib.import_module(ext.module).__file__ or "").resolve().parent / "ui").resolve()
        target = (root / file).resolve()
        if root not in target.parents or target.suffix not in ASSET_TYPES or not target.is_file():
            raise Http404()
        # Revalidation à chaque chargement (ETag) : une mise à jour de l'extension est vue tout de suite, dans tous les espaces,
        # sans retélécharger un fichier inchangé.
        st = target.stat()
        etag = f'"{st.st_mtime_ns:x}-{st.st_size:x}"'
        if request.headers.get("If-None-Match") == etag:
            response = HttpResponse(status=304)
        else:
            response = FileResponse(target.open("rb"), content_type=ASSET_TYPES[target.suffix])
        response["ETag"] = etag
        response["Cache-Control"] = "private, no-cache"
        return response
