# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.test import TestCase
from django.test import Client
from django.urls import reverse
from django.contrib.auth.models import User

# Create your tests here.
# alter user microcosm with createdb;
# python manage.py test changeset --keep

class ChangesetTestCase(TestCase):
	def setUp(self):
		self.username = "john"
		self.password = "glass onion"
		self.email = 'jlennon@beatles.com'
		self.user = User.objects.create_user(self.username, self.email, self.password)

	def test_create_changeset(self):
		client = Client()
		client.login(username=self.username, password=self.password)
		response = client.put(reverse('create'))
		self.assertEqual(response.status_code, 200)
		cid = int(response.content)
		
	def tearDown(self):
		#self.up1.delete()
		#self.u1.delete()
		u = User.objects.get(username = self.username)
		u.delete()
