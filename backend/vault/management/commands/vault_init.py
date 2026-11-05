from django.core.management.base import BaseCommand, CommandError

from vault import crypto


class Command(BaseCommand):
    help = "Crée la première clé de chiffrement du coffre si le dossier des clés n'en contient aucune (idempotent ; n'affiche jamais le matériel de la clé)."

    def handle(self, *args, **opts):
        try:
            kid, created = crypto.ensure_key()
        except crypto.VaultError as exc:
            raise CommandError(str(exc)) from None
        self.stdout.write(
            f"Clé de chiffrement créée (identifiant {kid}) dans {crypto.key_dir()}. Sauvegardez ce dossier À PART des données."
            if created
            else f"Clé déjà présente (identifiant {kid}) : rien à faire."
        )
