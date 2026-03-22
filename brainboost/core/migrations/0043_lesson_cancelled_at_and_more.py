from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0042_brainboostfeedback_monthlyfeedbackreminderlog_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="lesson",
            name="cancelled_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="lesson",
            name="cancellation_chargeable",
            field=models.BooleanField(default=False),
        ),
    ]
