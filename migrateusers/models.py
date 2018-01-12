# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models

class LegacyAccount(models.Model):
	uid = models.BigIntegerField(primary_key=True)
	username = models.CharField(max_length=255, )
	email = models.CharField(max_length=255, )
	hashed_password = models.CharField(max_length=255, )
	created_at = models.BigIntegerField()
	lat = models.FloatField()
	lon = models.FloatField()
	zoom = models.IntegerField()

