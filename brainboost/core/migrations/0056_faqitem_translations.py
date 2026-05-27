from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0055_alter_adminidea_title"),
    ]

    operations = [
        migrations.AddField(
            model_name="faqitem",
            name="answer_en",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="faqitem",
            name="answer_pl",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="faqitem",
            name="question_en",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="faqitem",
            name="question_pl",
            field=models.CharField(blank=True, max_length=255),
        ),
    ]
