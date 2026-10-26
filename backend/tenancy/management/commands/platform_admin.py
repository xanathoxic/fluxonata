from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from audit.models import record


class Command(BaseCommand):
    help = "Grant (default) or revoke the platform-administrator role of an existing account."

    def add_arguments(self, parser):
        parser.add_argument("username")
        parser.add_argument("--revoke", action="store_true")

    def handle(self, username, revoke, **opts):
        User = get_user_model()
        user = User.objects.filter(username=username, is_active=True).first()
        if user is None:
            raise CommandError(f"Compte « {username} » introuvable.")
        if hasattr(user, "service_account"):
            raise CommandError("Un compte de service ne peut pas être administrateur de la plate-forme.")
        if revoke and not User.objects.filter(is_superuser=True, is_active=True).exclude(pk=user.pk).exists():
            raise CommandError("Il doit rester au moins un administrateur de la plate-forme.")
        user.is_superuser = not revoke
        user.save(update_fields=["is_superuser"])
        record("platform.admin_revoke" if revoke else "platform.admin_grant", user=user.username, via="cli")
        self.stdout.write(
            f"« {username} » {'n est plus' if revoke else 'est maintenant'} administrateur de la plate-forme."
        )
