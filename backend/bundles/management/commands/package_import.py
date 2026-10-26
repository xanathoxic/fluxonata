import io
import zipfile
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from bundles import service
from tenancy.models import Workspace


class Command(BaseCommand):
    help = "Import a package from an archive file or an unpacked directory (e.g. a Git checkout). Dry run by default."

    def add_arguments(self, parser):
        parser.add_argument("workspace")
        parser.add_argument("source", help="archive .zip or directory")
        parser.add_argument("--user", required=True)
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--skip-conflicts", action="store_true")

    def handle(self, workspace, source, user, apply, skip_conflicts, **opts):
        from django.contrib.auth import get_user_model

        ws = Workspace.objects.filter(slug=workspace).first()
        u = get_user_model().objects.filter(username=user).first()
        if ws is None or u is None:
            raise CommandError("Espace ou utilisateur inconnu.")
        path = Path(source)
        if path.is_dir():
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                for f in sorted(p for p in path.rglob("*") if p.is_file() and ".git" not in p.parts):
                    z.write(f, f.relative_to(path).as_posix())
            data = buf.getvalue()
        else:
            data = path.read_bytes()
        report = service.import_package(ws, u, data, apply=apply, strategy="skip" if skip_conflicts else "abort")
        for item in report["items"]:
            self.stdout.write(
                f"[{item['status']}] {item['kind']} {item['slug']}@{item['version']} {' '.join(item['messages'])}"
            )
        for err in report["errors"]:
            self.stdout.write(f"[erreur] {err}")
        self.stdout.write(
            "Appliqué."
            if report["applied"]
            else ("Aperçu : applicable." if report["can_apply"] else "Aperçu : bloqué.")
        )
