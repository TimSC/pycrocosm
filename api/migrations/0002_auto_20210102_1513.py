# Generated by Django 3.1.4 on 2021-01-02 15:13

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0001_initial'),
    ]

    operations = [
        migrations.DeleteModel(
            name='Nodes',
        ),
        migrations.DeleteModel(
            name='RelationMemsN',
        ),
        migrations.DeleteModel(
            name='RelationMemsR',
        ),
        migrations.DeleteModel(
            name='RelationMemsW',
        ),
        migrations.DeleteModel(
            name='Relations',
        ),
        migrations.DeleteModel(
            name='WayMems',
        ),
        migrations.DeleteModel(
            name='Ways',
        ),
    ]
