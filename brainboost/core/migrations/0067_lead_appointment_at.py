from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0066_lead_family_conversion"),
    ]

    operations = [
        migrations.AddField(
            model_name="lead",
            name="appointment_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
