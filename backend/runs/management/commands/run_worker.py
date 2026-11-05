from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from runs import lifecycle
from runs import worker as worker_module
from runs.worker import RoleError, parse_roles, run_forever


class Command(BaseCommand):
    help = "Run an execution worker (separate process; also reconciles expired leases). ORCH_ROLES / --roles : exec, aux, ai (défaut : tous)."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Drain the queue then exit")
        parser.add_argument(
            "--roles", default=None, help="exec,aux,ai (ou worker-exec,…) ; remplace ORCH_ROLES ; défaut : tous"
        )

    def handle(self, *args, once=False, roles=None, **opts):
        try:
            parse_roles(
                settings.ORCH_ROLES if roles is None else roles
            )  # échec franc au lancement, avant toute ouverture de connexion
        except RoleError as exc:
            raise CommandError(str(exc)) from None
        if not once:
            lifecycle.install(worker_module.STOP, worker_module.log)
        run_forever(once=once, roles=roles)
