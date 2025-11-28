from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0015_studentprofile_zoom_link"),
    ]

    operations = [
        migrations.AddField(
            model_name="lesson",
            name="cancellation_reason",
            field=models.TextField(blank=True, default=""),
        ),
    ]
