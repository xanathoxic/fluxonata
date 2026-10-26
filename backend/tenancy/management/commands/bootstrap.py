import os
import secrets

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from tenancy.models import Environment, Membership, Organization, Role, Workspace


class Command(BaseCommand):
    help = "Create the first organization, workspace and administrator (idempotent). No demo data."

    def add_arguments(self, parser):
        parser.add_argument("--org", default="Mon organisation")
        parser.add_argument("--workspace", default="principal", help="workspace slug")
        parser.add_argument("--admin-user", default="admin")
        parser.add_argument(
            "--no-platform-admin",
            action="store_true",
            help="do not give the first administrator the platform-administrator role",
        )
        parser.add_argument(
            "--password-env", help="read the admin password from this environment variable instead of generating one"
        )

    def handle(self, org, workspace, admin_user, password_env, no_platform_admin=False, **opts):
        User = get_user_model()
        o, _ = Organization.objects.get_or_create(slug="main", defaults={"name": org})
        ws, _ = Workspace.objects.get_or_create(
            organization=o, slug=workspace, defaults={"name": workspace.capitalize()}
        )
        for kind, name in (("dev", "Développement"), ("test", "Recette"), ("prod", "Production")):
            Environment.objects.get_or_create(workspace=ws, kind=kind, defaults={"name": name})
        user, created = User.objects.get_or_create(username=admin_user)
        if created:
            pw = os.environ.get(password_env, "") if password_env else secrets.token_urlsafe(16)
            if password_env and len(pw) < 12:
                raise CommandError(
                    f"Variable {password_env} absente ou mot de passe trop court (12 caractères minimum)."
                )
            user.set_password(pw)
            user.save()
            self.stdout.write(
                f"Administrateur « {admin_user} » créé. "
                + (
                    f"Mot de passe (affiché une seule fois) : {pw}"
                    if not password_env
                    else "Mot de passe pris dans l'environnement."
                )
            )
        if created and not no_platform_admin:
            user.is_superuser = (
                True  # platform administrator: manages spaces and workers, no implicit access to space data
            )
            user.save(update_fields=["is_superuser"])
        Membership.objects.get_or_create(user=user, workspace=ws, defaults={"role": Role.ADMIN})
        self.stdout.write(f"Espace « {workspace} » prêt (environnements dev/test/prod).")
