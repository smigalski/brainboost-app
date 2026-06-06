from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0057_faqitem_more_translations"),
    ]

    operations = [
        migrations.AddField(
            model_name="faqitem",
            name="answer_es",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="faqitem",
            name="question_es",
            field=models.CharField(blank=True, max_length=255),
        ),
    ]
