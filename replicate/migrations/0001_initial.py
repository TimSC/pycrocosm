# -*- coding: utf-8 -*-
from django.db import migrations, models


class Migration(migrations.Migration):

	initial = True

	dependencies = [
	]

	operations = [
		migrations.CreateModel(
			name='DbExtract',
			fields=[
				('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
			],
			options={
				'verbose_name': 'database extract',
				'verbose_name_plural': 'database extracts',
				'managed': False,
				'default_permissions': ('add', 'change', 'delete', 'view'),
			},
		),
	]
