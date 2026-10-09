# -*- coding: utf-8 -*-
from django.db import migrations, models


class Migration(migrations.Migration):

	initial = True

	dependencies = [
	]

	operations = [
		migrations.CreateModel(
			name='MapParameter',
			fields=[
				('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
			],
			options={
				'verbose_name': 'map parameter',
				'verbose_name_plural': 'map parameters',
				'managed': False,
				'default_permissions': ('change', 'view'),
			},
		),
	]
