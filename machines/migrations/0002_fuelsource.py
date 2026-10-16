from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('machines', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='FuelSource',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=100, unique=True, verbose_name='název')),
                (
                    'is_active',
                    models.BooleanField(
                        default=True,
                        help_text='Odškrtnutím zdroj vyřadíte místo smazání — zmizí z nabídky ve formuláři '
                        'Tankování, ale historie zůstane zachována. Smazání je zablokováno, jakmile '
                        'zdroj nese nějaké tankování, právě proto, aby historie nemohla zmizet.',
                        verbose_name='aktivní',
                    ),
                ),
            ],
            options={
                'verbose_name': 'zdroj paliva',
                'verbose_name_plural': 'zdroje paliva',
                'ordering': ['name'],
            },
        ),
    ]
