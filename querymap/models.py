# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from __future__ import print_function

from django.db import models

class MapParameter(models.Model):
	"""Placeholder so the map's metadata parameters appear in the admin.

	They are kept in the pgmap map database, not Django's, so this model has
	no table and is never queried; the admin reads them through pgmap instead.
	"""
	class Meta:
		managed = False
		default_permissions = ('change', 'view')
		verbose_name = "map parameter"
		verbose_name_plural = "map parameters"
