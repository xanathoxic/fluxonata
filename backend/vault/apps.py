from django.apps import AppConfig


class VaultConfig(AppConfig):
    name = "vault"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        # Les modules qui ne dépendent pas du coffre (`core.sealing`) chiffrent leurs secrets par lui.
        from core import sealing

        from . import crypto

        sealing.register(crypto.encrypt, crypto.decrypt)
