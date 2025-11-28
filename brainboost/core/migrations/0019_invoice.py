from django.db import migrations, models
import core.models
import django.db.models.deletion
import django.core.validators


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0018_studentprofile_zumpad_link"),
    ]

    operations = [
        migrations.CreateModel(
            name="Invoice",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("file", models.FileField(upload_to=core.models.invoice_upload_path, validators=[django.core.validators.FileExtensionValidator(allowed_extensions=["pdf"])])),
                ("uploaded_at", models.DateTimeField(auto_now_add=True)),
                ("student", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="invoices", to="core.studentprofile")),
                ("uploaded_by", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="invoices", to="core.tutorprofile")),
            ],
            options={
                "verbose_name": "Rechnung",
                "verbose_name_plural": "Rechnungen",
                "ordering": ["-uploaded_at"],
            },
        ),
    ]
