from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("runs", "0016_run_next_check_at")]

    operations = [
        migrations.AddField(
            model_name="approval",
            name="subject",
            field=models.CharField(blank=True, default="", max_length=200),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="approval",
            name="body",
            field=models.TextField(blank=True, default=""),
            preserve_default=False,
        ),
    ]
