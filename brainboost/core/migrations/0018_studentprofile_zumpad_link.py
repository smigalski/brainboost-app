from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0017_lesson_reschedule_requested"),
    ]

    operations = [
        migrations.AddField(
            model_name="studentprofile",
            name="zumpad_link",
            field=models.URLField(blank=True),
        ),
    ]
