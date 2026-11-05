from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("runs", "0015_run_files")]

    operations = [
        migrations.AddField(
            model_name="workflowrun",
            name="next_check_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
    ]
