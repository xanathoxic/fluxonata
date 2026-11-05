"""Lists (and, with --yes, deletes) what the end-to-end tests leave behind in a workspace, so that the demonstration data stay readable.

What counts as a test artefact: library forms, controls and workflows whose identifier ends with a number of 4 to 8 digits (the tests
create them as `nom-123456`), and connections named « Mot 1234567 ». The data of `seed_demo` never match. Nothing is deleted without
`--yes`: a person may have adopted one of these objects, so look at the list first."""

import re

from django.core.management.base import BaseCommand, CommandError

from formbuilder.models import Control, Form
from tenancy.models import Workspace
from vault.models import Connection
from workflows.models import Workflow

SLUG = re.compile(r"-\d{4,8}$")
CONNECTION = re.compile(r"^[A-Za-zÀ-ÿ]+\s?\d{4,8}$")


class Command(BaseCommand):
    help = "Liste (puis supprime avec --yes) les objets créés par les tests de bout en bout dans un espace."

    def add_arguments(self, parser):
        parser.add_argument("--workspace", default="demo")
        parser.add_argument("--yes", action="store_true", help="supprimer réellement (sinon : simple liste)")

    def handle(self, *args, **options):
        ws = Workspace.objects.filter(slug=options["workspace"]).first()
        if ws is None:
            raise CommandError(f"Espace « {options['workspace']} » introuvable.")
        found = [
            ("workflows", [w for w in Workflow.objects.filter(workspace=ws) if SLUG.search(w.slug)]),
            ("formulaires", [f for f in Form.objects.filter(workspace=ws) if SLUG.search(f.slug)]),
            ("contrôles", [c for c in Control.objects.filter(workspace=ws) if SLUG.search(c.slug)]),
            ("connexions", [c for c in Connection.objects.filter(workspace=ws) if CONNECTION.match(c.name)]),
        ]
        for label, objects in found:
            self.stdout.write(f"{label} : {len(objects)}")
        if not options["yes"]:
            self.stdout.write("Aucune suppression (ajoutez --yes pour supprimer).")
            return
        deleted = skipped = 0
        for _, objects in found:
            for obj in objects:
                try:
                    obj.delete()
                    deleted += 1
                except Exception as exc:  # noqa: BLE001 - a protected object (published version in use) stays
                    skipped += 1
                    self.stderr.write(f"conservé : {obj} ({type(exc).__name__})")
        self.stdout.write(f"supprimés : {deleted} · conservés : {skipped}")
