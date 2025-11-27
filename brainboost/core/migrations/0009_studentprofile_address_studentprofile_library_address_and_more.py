from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0008_alter_lesson_status'),
    ]

    operations = [
        migrations.AddField(
            model_name='lesson',
            name='distance_km',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=6, null=True),
        ),
        migrations.AddField(
            model_name='lesson',
            name='location_address',
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name='studentprofile',
            name='address',
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name='studentprofile',
            name='latitude',
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='studentprofile',
            name='library_address',
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name='studentprofile',
            name='library_latitude',
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='studentprofile',
            name='library_longitude',
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='studentprofile',
            name='longitude',
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='tutorprofile',
            name='address',
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name='tutorprofile',
            name='latitude',
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='tutorprofile',
            name='longitude',
            field=models.FloatField(blank=True, null=True),
        ),
    ]
