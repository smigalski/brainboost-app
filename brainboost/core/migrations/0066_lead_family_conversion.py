from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0065_customuser_pending_email_and_more"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql=[
                        (
                            "ALTER TABLE core_parentprofile ADD COLUMN IF NOT EXISTS address varchar(255) NOT NULL DEFAULT ''",
                            None,
                        ),
                        (
                            "ALTER TABLE core_parentprofile ALTER COLUMN address DROP DEFAULT",
                            None,
                        ),
                        (
                            "ALTER TABLE core_studentprofile ADD COLUMN IF NOT EXISTS birth_date date NULL",
                            None,
                        ),
                        (
                            "ALTER TABLE core_studentprofile ADD COLUMN IF NOT EXISTS grade_level varchar(120) NOT NULL DEFAULT ''",
                            None,
                        ),
                        (
                            "ALTER TABLE core_studentprofile ALTER COLUMN grade_level DROP DEFAULT",
                            None,
                        ),
                        (
                            "ALTER TABLE core_studentprofile ADD COLUMN IF NOT EXISTS school varchar(255) NOT NULL DEFAULT ''",
                            None,
                        ),
                        (
                            "ALTER TABLE core_studentprofile ALTER COLUMN school DROP DEFAULT",
                            None,
                        ),
                    ],
                    reverse_sql=migrations.RunSQL.noop,
                ),
            ],
            state_operations=[
                migrations.AddField(
                    model_name="parentprofile",
                    name="address",
                    field=models.CharField(blank=True, max_length=255),
                ),
                migrations.AddField(
                    model_name="studentprofile",
                    name="birth_date",
                    field=models.DateField(blank=True, null=True),
                ),
                migrations.AddField(
                    model_name="studentprofile",
                    name="grade_level",
                    field=models.CharField(blank=True, max_length=120),
                ),
                migrations.AddField(
                    model_name="studentprofile",
                    name="school",
                    field=models.CharField(blank=True, max_length=255),
                ),
            ],
        ),
        migrations.AddField(
            model_name="lead",
            name="converted_parent",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="converted_leads",
                to="core.parentprofile",
            ),
        ),
        migrations.AddField(
            model_name="lead",
            name="converted_student",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="source_lead",
                to="core.studentprofile",
            ),
        ),
    ]
