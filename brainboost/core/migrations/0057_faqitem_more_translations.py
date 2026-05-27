from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0056_faqitem_translations"),
    ]

    operations = [
        migrations.AddField(
            model_name="faqitem",
            name="answer_ar",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="faqitem",
            name="answer_ru",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="faqitem",
            name="answer_tr",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="faqitem",
            name="question_ar",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="faqitem",
            name="question_ru",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="faqitem",
            name="question_tr",
            field=models.CharField(blank=True, max_length=255),
        ),
    ]
