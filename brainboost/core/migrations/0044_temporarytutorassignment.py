from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0043_lesson_cancelled_at_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="TemporaryTutorAssignment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("end_mode", models.CharField(choices=[("lessons", "Nach Einheiten"), ("date", "Bis Datum")], max_length=20)),
                ("max_lessons", models.PositiveIntegerField(blank=True, null=True)),
                ("ends_on", models.DateField(blank=True, null=True)),
                ("target_was_preassigned", models.BooleanField(default=False)),
                ("is_active", models.BooleanField(default=True)),
                ("ended_at", models.DateTimeField(blank=True, null=True)),
                ("ended_reason", models.CharField(blank=True, choices=[("lessons_reached", "Einheiten erreicht"), ("date_reached", "Enddatum erreicht"), ("handover", "Abgabe"), ("superseded", "Überschrieben")], max_length=30)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_temporary_tutor_assignments",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "source_tutor",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="temporary_outgoing_assignments",
                        to="core.tutorprofile",
                    ),
                ),
                (
                    "student",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="temporary_tutor_assignments",
                        to="core.studentprofile",
                    ),
                ),
                (
                    "target_tutor",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="temporary_incoming_assignments",
                        to="core.tutorprofile",
                    ),
                ),
            ],
            options={
                "verbose_name": "Temporäre TutorInnen-Zuweisung",
                "verbose_name_plural": "Temporäre TutorInnen-Zuweisungen",
                "ordering": ["-created_at"],
            },
        ),
    ]
