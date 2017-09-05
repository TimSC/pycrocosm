# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models
from django.contrib.auth.models import User
from django.contrib.postgres.fields import JSONField

# Create your models here.

class Changeset(models.Model):
	user = models.ForeignKey(User, on_delete=models.CASCADE)
	min_lat = models.FloatField(default=0.0)
	max_lat = models.FloatField(default=0.0)
	min_lon = models.FloatField(default=0.0)
	max_lon = models.FloatField(default=0.0)
	tags = JSONField(default={})
	open_datetime = models.DateTimeField(auto_now=True)
	close_datetime = models.DateTimeField()

