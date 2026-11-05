"""Copie les fichiers du volume local vers le stockage S3 (AR1.4) : artefacts d'exécution et archives de paquets.

    python manage.py artifacts_migrate [--source-dir DIR] [--create-bucket] [--dry-run] [--delete-source]

Chaque fichier est copié, **relu depuis le service et comparé** (empreinte SHA-256 enregistrée pour les artefacts, somme de l'archive pour les
paquets) avant d'être éventuellement supprimé de la source. Rejouable : un fichier déjà copié est recopié à l'identique. Un fichier absent de la
source est signalé, jamais supprimé de la base. Le service se configure par les variables ORCH_S3_* (voir docs/INSTALL.md) ; `ORCH_ARTIFACTS_STORE`
peut rester à `fs` pendant la copie et ne passer à `s3` qu'après.

Cette commande est dans `bundles` parce qu'elle lit les deux familles de clés (artefacts de `runs`, archives de `bundles`) sans créer de
nouvelle dépendance entre modules (voir tests/test_architecture.py).
"""

import hashlib
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from bundles.models import PackageVersion
from runs.artifacts import FileSystemStore
from runs.models import Artifact
from runs.s3store import ArtifactStoreError, S3Store


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Command(BaseCommand):
    help = "Copie (puis, au choix, retire) les artefacts et archives du volume local vers le stockage S3."

    def add_arguments(self, parser):
        parser.add_argument(
            "--source-dir",
            default=settings.ORCH_ARTIFACTS_DIR,
            help="volume local à lire (défaut : ORCH_ARTIFACTS_DIR)",
        )
        parser.add_argument("--create-bucket", action="store_true", help="crée le bucket s'il n'existe pas")
        parser.add_argument(
            "--dry-run", action="store_true", help="n'écrit rien : compte et vérifie la présence dans la source"
        )
        parser.add_argument(
            "--delete-source", action="store_true", help="supprime chaque fichier source APRÈS vérification de la copie"
        )

    def handle(self, *args, source_dir, create_bucket, dry_run, delete_source, **opts):
        if not Path(source_dir).is_dir():
            raise CommandError(f"Volume source introuvable : {source_dir}")
        src = FileSystemStore(Path(source_dir))
        try:
            dst = S3Store.from_settings()
            if not dry_run:
                if create_bucket:
                    self.stdout.write("Bucket créé." if dst.create_bucket() else "Bucket déjà présent.")
                dst.ping()
        except (ArtifactStoreError, Exception) as exc:  # noqa: BLE001 - configuration, réseau ou droits : on le dit clairement et on s'arrête
            raise CommandError(f"Stockage S3 inutilisable : {exc}") from None

        todo = [
            (a.storage_key, a.sha256, f"artefact {a.pk}")
            for a in Artifact.objects.order_by("pk").iterator(chunk_size=200)
        ]
        todo += [
            (p.archive_key, p.checksum.removeprefix("sha256:"), f"paquet {p.package_id}/{p.version}")
            for p in PackageVersion.objects.order_by("pk").iterator(chunk_size=200)
        ]
        copied = missing = bad = removed = 0
        for key, digest, label in todo:
            try:
                data = src.get(key)
            except FileNotFoundError:
                missing += 1
                self.stderr.write(f"absent de la source : {label} ({key})")
                continue
            if digest and sha256(data) != digest:
                bad += 1
                self.stderr.write(f"source ALTÉRÉE (empreinte différente) : {label} ({key}) — non copié")
                continue
            if dry_run:
                copied += 1
                continue
            dst.put(key, data)
            if sha256(dst.get(key)) != sha256(data):
                bad += 1
                self.stderr.write(f"copie NON conforme à la relecture : {label} ({key}) — source conservée")
                continue
            copied += 1
            if delete_source:
                src.delete(key)
                removed += 1
        verb = "à copier" if dry_run else "copiés et relus"
        self.stdout.write(
            f"{copied} fichier(s) {verb} ; {missing} absent(s) de la source ; {bad} en anomalie ; {removed} supprimé(s) de la source."
        )
        if bad:
            raise CommandError("Des fichiers n'ont pas pu être copiés fidèlement : voir ci-dessus.")
