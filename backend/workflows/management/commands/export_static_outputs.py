"""Regenerates `frontend/src/workflow/staticOutputs.json` from the validator's `STATIC_OUTPUTS` (see `workflows/static_outputs.py`)."""

from django.core.management.base import BaseCommand

from workflows import static_outputs


class Command(BaseCommand):
    help = "Écrit frontend/src/workflow/staticOutputs.json (sorties des types d'étape) à partir du validateur."

    def handle(self, *args, **opts):
        static_outputs.TARGET.write_text(static_outputs.render(), encoding="utf-8")
        self.stdout.write(f"écrit : {static_outputs.TARGET}")
