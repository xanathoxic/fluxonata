from django.core.management.base import BaseCommand

from runs import lifecycle
from runs import orchestrator as orchestrator_module
from runs.orchestrator import run_forever


class Command(BaseCommand):
    help = (
        "Run the workflow orchestrator (separate process). SIGTERM : finit le tour en cours puis s'arrête proprement."
    )

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, once=False, **opts):
        if not once:
            lifecycle.install(orchestrator_module.STOP, orchestrator_module.log)
        run_forever(once=once)
