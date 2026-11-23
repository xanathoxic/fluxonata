from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("workflows", "0015_runner_pin")]

    operations = [
        migrations.AddField(
            model_name="workflow",
            name="run_mode",
            field=models.CharField(
                choices=[("concurrent", "Concurrent"), ("sequential", "Séquentiel")],
                default="concurrent",
                max_length=10,
            ),
        ),
    ]
