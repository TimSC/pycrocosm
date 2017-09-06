# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.test import TestCase
from django.test import Client
from django.urls import reverse
from django.contrib.auth.models import User

from .models import Changeset

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

		xml = """<osm>
		  <changeset>
			<tag k="created_by" v="JOSM 1.61"/>
			<tag k="comment" v="Just adding some streetnames"/>
		  </changeset>
		</osm>"""
		response = client.put(reverse('create'), xml, content_type='application/xml')

		self.assertEqual(response.status_code, 200)
		cid = int(response.content)
		
		cs = Changeset.objects.get(id = cid)
		self.assertEqual("created_by" in cs.tags, True)
		self.assertEqual("comment" in cs.tags, True)
		self.assertEqual(cs.tags["created_by"] == "JOSM 1.61", True)
		self.assertEqual(cs.tags["comment"] == "Just adding some streetnames", True)

	def tearDown(self):
		u = User.objects.get(username = self.username)
		u.delete()
