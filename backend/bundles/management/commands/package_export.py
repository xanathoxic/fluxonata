import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from bundles import archive


class Command(BaseCommand):
    help = "Unpack a stored package version into a directory (same layout as the archive) for Git versioning."

    def add_arguments(self, parser):
        parser.add_argument("workspace")
        parser.add_argument("package")
        parser.add_argument("version")
        parser.add_argument("directory")

    def handle(self, workspace, package, version, directory, **opts):
        from bundles.models import PackageVersion
        from runs.artifacts import get_store

        pv = PackageVersion.objects.filter(
            package__workspace__slug=workspace, package__slug=package, version=version
        ).first()
        if pv is None:
            raise CommandError("Paquet introuvable.")
        manifest, files = archive.read(get_store().get(pv.archive_key))
        root = Path(directory).resolve()
        for name, content in {
            **files,
            "manifest.json": json.dumps(manifest, indent=2, sort_keys=True).encode(),
        }.items():
            target = (root / name).resolve()
            if root not in target.parents:
                raise CommandError(f"Chemin refusé : {name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        self.stdout.write(f"Paquet {package}@{version} écrit dans {root} ({len(files)} fichiers).")
