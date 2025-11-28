from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0016_lesson_cancellation_reason"),
    ]

    operations = [
        migrations.AddField(
            model_name="lesson",
            name="reschedule_requested",
            field=models.BooleanField(default=False),
        ),
    ]
