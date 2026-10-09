from django.db import models

class DbExtract(models.Model):
	"""Placeholder so stored extracts appear in the admin.

	Extracts live in the pgmap map database, not Django's, so this model has no
	table and is never queried; the admin reads them through pgmap instead.
	"""
	class Meta:
		managed = False
		default_permissions = ('add', 'change', 'delete', 'view')
		verbose_name = "database extract"
		verbose_name_plural = "database extracts"
