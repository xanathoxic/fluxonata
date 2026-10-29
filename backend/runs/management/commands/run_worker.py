from django.core.management.base import BaseCommand

from runs.worker import run_forever


class Command(BaseCommand):
    help = "Run an execution worker (separate process; also reconciles expired leases)."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Drain the queue then exit")

    def handle(self, *args, once=False, **opts):
        run_forever(once=once)
