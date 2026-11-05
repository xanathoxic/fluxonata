import hashlib
import json

import django.db.models.deletion
from django.db import migrations, models
from django.utils.text import slugify


def definitions_to_library(apps, schema_editor):
    """A public form that carried its own definition becomes a form of the library (its draft), and, when it was
    published, version 1.0.0 of that form, which the public form then references."""
    PublicForm = apps.get_model("triggers", "PublicForm")
    Form = apps.get_model("formbuilder", "Form")
    FormVersion = apps.get_model("formbuilder", "FormVersion")
    for pf in PublicForm.objects.exclude(definition__isnull=True, published_definition__isnull=True).order_by("id"):
        base = (slugify(pf.name)[:40] or "formulaire") + f"-{pf.pk}"
        form = Form.objects.create(
            workspace_id=pf.workspace_id,
            slug=base,
            name=pf.name[:160],
            description=pf.description[:2000],
            owner_id=pf.created_by_id,
            draft_definition=pf.definition or pf.published_definition,
        )
        if pf.published_definition is not None:
            digest = "sha256:" + hashlib.sha256(json.dumps(pf.published_definition, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            version = FormVersion.objects.create(
                form=form,
                version="1.0.0",
                definition=pf.published_definition,
                digest=digest,
                changelog="Repris du formulaire public",
                created_by_id=pf.created_by_id,
            )
            pf.library_version_id = version.pk
            pf.save(update_fields=["library_version"])


class Migration(migrations.Migration):
    dependencies = [
        ("formbuilder", "0001_initial"),
        ("triggers", "0010_lookup_for_workflows"),
    ]

    operations = [
        migrations.AddField(
            model_name="publicform",
            name="library_version",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="+", to="formbuilder.formversion"),
        ),
        migrations.RunPython(definitions_to_library, migrations.RunPython.noop),
        migrations.RemoveField(model_name="publicform", name="definition"),
        migrations.RemoveField(model_name="publicform", name="definition_version"),
    ]
