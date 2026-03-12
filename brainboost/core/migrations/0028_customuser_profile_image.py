from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0027_alter_studentprofile_options_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="customuser",
            name="profile_image",
            field=models.ImageField(blank=True, upload_to="profile_images/"),
        ),
    ]
