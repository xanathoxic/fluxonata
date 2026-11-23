from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("runs", "0018_runner_pin")]

    operations = [
        migrations.AlterField(
            model_name="workflowrun",
            name="status",
            field=models.CharField(
                choices=[
                    ("queued", "En attente"),
                    ("running", "En cours"),
                    ("compensating", "Compensation en cours"),
                    ("succeeded", "Réussie"),
                    ("failed", "Échouée"),
                    ("cancelled", "Annulée"),
                ],
                db_index=True,
                default="running",
                max_length=14,
            ),
        ),
    ]
