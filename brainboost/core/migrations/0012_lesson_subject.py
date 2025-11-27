from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0011_remove_studentprofile_library_address_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='lesson',
            name='subject',
            field=models.CharField(choices=[('mathe', 'Mathe'), ('deutsch', 'Deutsch'), ('englisch', 'Englisch'), ('naturwissenschaften', 'Naturwissenschaften'), ('franzoesisch', 'Französisch'), ('spanisch', 'Spanisch'), ('musik', 'Musik')], default='mathe', max_length=50),
        ),
    ]
