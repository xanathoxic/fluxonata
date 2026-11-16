from django.apps import AppConfig


class TriggersConfig(AppConfig):
    name = "triggers"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        # Webhook secrets are encrypted with the vault's keys: when the keys rotate, the vault asks for them to be re-encrypted.
        from vault import registry

        from .models import Webhook

        def rotate() -> int:
            n = 0
            for w in Webhook.objects.select_for_update().filter(external_secret__isnull=True):
                w.set_secret(w.reveal_secret())
                w.save()
                n += 1
            return n

        registry.register_rotation(rotate)
