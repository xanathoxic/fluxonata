from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from vault import crypto
from vault.models import Secret, VaultConfig, VaultLease


class Command(BaseCommand):
    help = "Generate a new encryption key, re-encrypt every secret with it. Use --retire-old once done."

    def add_arguments(self, parser):
        parser.add_argument("--retire-old", action="store_true", help="Delete the previous key files afterwards")

    @transaction.atomic
    def handle(self, *args, retire_old=False, **opts):
        new = crypto.new_key()
        n = 0
        for s in Secret.objects.select_for_update().filter(
            provider=Secret.Provider.INTERNAL
        ):  # external ones hold no ciphertext
            plain = crypto.decrypt(s.ciphertext, f"{s.PURPOSE}:{s.workspace_id}")
            s.key_id, s.ciphertext = crypto.encrypt(plain, f"{s.PURPOSE}:{s.workspace_id}")
            s.rotated_at = timezone.now()
            s.save()
            n += 1
        # other encrypted columns register here as they appear (webhook secrets)
        from triggers.models import Webhook  # noqa: PLC0415

        for w in Webhook.objects.select_for_update().filter(external_secret__isnull=True):
            w.set_secret(w.reveal_secret())
            w.save()
            n += 1
        # the AppRole secret_id of the external vault, and the short-lived dynamic credentials kept encrypted
        cfg = VaultConfig.objects.select_for_update().filter(pk=1).first()
        if cfg is not None and cfg.secret_id_ciphertext:
            cfg.set_secret_id(cfg.secret_id())
            cfg.save(update_fields=["key_id", "secret_id_ciphertext"])
            n += 1
        for lease in VaultLease.objects.select_for_update().select_related("secret"):
            purpose = f"{VaultLease.PURPOSE}:{lease.secret.workspace_id}"
            lease.key_id, lease.ciphertext = crypto.reencrypt(lease.ciphertext, purpose)
            lease.save(update_fields=["key_id", "ciphertext"])
            n += 1
        removed = crypto.retire_old_keys() if retire_old else []
        self.stdout.write(
            f"Nouvelle clé {new} ; {n} secret(s) rechiffré(s) ; anciennes clés supprimées : {len(removed)}"
        )
