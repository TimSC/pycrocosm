# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from __future__ import print_function

from django.db import models
from django.contrib.auth.models import User
from django.db.models import signals
from django.dispatch import receiver
from django.db.models.signals import post_save

# https://docs.djangoproject.com/en/1.11/topics/auth/customizing/#extending-the-existing-user-model
class UserData(models.Model):
	user = models.OneToOneField(User, on_delete=models.CASCADE)
	home_lat = models.FloatField(default=0.0)
	home_lon = models.FloatField(default=0.0)
	home_zoom = models.IntegerField(default=-1)
	description = models.TextField(default="")
	languages = models.TextField(default="")

@receiver(post_save, sender=User)
def user_post_save(sender, instance, signal, *args, **kwargs):
	# Creates user profile
	profile, new = UserData.objects.get_or_create(user=instance)

class UserPreference(models.Model):
	user = models.ForeignKey(User, on_delete=models.CASCADE)
	key = models.CharField(max_length=255)
	value = models.CharField(max_length=255)

