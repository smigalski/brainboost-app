from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0014_merge_20251123_2114"),
    ]

    operations = [
        migrations.AddField(
            model_name="studentprofile",
            name="zoom_link",
            field=models.URLField(blank=True),
        ),
    ]
