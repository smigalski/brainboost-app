from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0025_learningmaterial_related_task"),
    ]

    operations = [
        migrations.AddField(
            model_name="parentprofile",
            name="phone_number",
            field=models.CharField(blank=True, max_length=50),
        ),
        migrations.AddField(
            model_name="studentprofile",
            name="phone_number",
            field=models.CharField(blank=True, max_length=50),
        ),
        migrations.AddField(
            model_name="tutorprofile",
            name="phone_number",
            field=models.CharField(blank=True, max_length=50),
        ),
    ]
