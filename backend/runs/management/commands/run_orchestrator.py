from django.core.management.base import BaseCommand

from runs.orchestrator import run_forever


class Command(BaseCommand):
    help = "Run the workflow orchestrator (separate process)."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, once=False, **opts):
        run_forever(once=once)
