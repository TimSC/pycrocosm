# Generated by Django 5.1.2 on 2024-10-14 17:32

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('oauth2', '0004_oauth2application_disabled_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='oauth2application',
            name='client_id',
            field=models.CharField(max_length=255, unique=True),
        ),
    ]
