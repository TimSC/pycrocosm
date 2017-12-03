# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from __future__ import print_function

from django.test import TestCase
from django.test import Client
from django.urls import reverse
from django.contrib.auth.models import User

import xml.etree.ElementTree as ET
from defusedxml.ElementTree import fromstring
from .models import UserData, UserPreference

# Create your tests here.

class UsersTestCase(TestCase):
	def setUp(self):
		self.username = "john"
		self.password = "glass onion"
		self.email = 'jlennon@beatles.com'
		self.user = User.objects.create_user(self.username, self.email, self.password)
		self.client = Client()
		self.client.login(username=self.username, password=self.password)

		self.testpref = UserPreference.objects.create(user=self.user, key="foo", value="bar")

		self.prefXml = """<osm version="0.6" generator="OpenStreetMap server">
			<preferences>
			   <preference k="somekey" v="somevalue" />
			   <preference k="bang" v="splat" />
			</preferences>
		  </osm>"""


	def test_get_details(self):
		response = self.client.get(reverse('details'))

		self.assertEqual(response.status_code, 200)
		
		xml = fromstring(response.content)
		self.assertEqual(xml.tag, "osm")
		userout = xml.find("user")
		self.assertEqual(int(userout.attrib["id"]) == self.user.id, True)
		self.assertEqual("account_created" in userout.attrib, True)
		self.assertEqual("display_name" in userout.attrib, True)

	def test_get_details_anon(self):
		anonClient = Client()
		response = anonClient.get(reverse('details'))
		self.assertEqual(response.status_code, 403)

	def test_get_preferences(self):
		response = self.client.get(reverse('preferences'))
		self.assertEqual(response.status_code, 200)

		xml = fromstring(response.content)
		self.assertEqual(xml.tag, "osm")
		preferencesout = xml.find("preferences")
		self.assertEqual(len(preferencesout.findall("preference")), 1)
		found = False
		for pref in preferencesout.findall("preference"):
			if pref.attrib["k"] == "foo":
				self.assertEqual(pref.attrib["v"], "bar")
				found = True
		self.assertEqual(found, True)

	def test_get_preferences_anon(self):
		anonClient = Client()
		response = anonClient.get(reverse('preferences'))
		self.assertEqual(response.status_code, 403)
	
	def test_put_preferences(self):

		response = self.client.put(reverse('preferences'), self.prefXml, content_type='application/xml')
		self.assertEqual(response.status_code, 200)

		prefs = UserPreference.objects.filter(user=self.user)
		self.assertEqual(len(prefs), 2)
		for pref in prefs:
			if pref.key == "somekey":
				self.assertEqual(pref.value, "somevalue")
			if pref.key == "bang":
				self.assertEqual(pref.value, "splat")

	def test_put_preferences_anon(self):
		anonClient = Client()
		response = anonClient.put(reverse('preferences'), self.prefXml, content_type='application/xml')
		self.assertEqual(response.status_code, 403)

	def test_put_preference_single(self):
		response = self.client.put(reverse('preferences_put', args=["agree"]), "details", content_type='text/plain')
		self.assertEqual(response.status_code, 200)

		prefs = UserPreference.objects.filter(user=self.user)
		self.assertEqual(len(prefs), 2)
		for pref in prefs:
			if pref.key == "agree":
				self.assertEqual(pref.value, "details")
			if pref.key == "foo":
				self.assertEqual(pref.value, "bar")

	def test_put_preference_single_anon(self):
		anonClient = Client()
		response = anonClient.put(reverse('preferences_put', args=["agree"]), "details", content_type='text/plain')
		self.assertEqual(response.status_code, 403)

	def tearDown(self):
		u = User.objects.get(username = self.username)
		u.delete()

		UserPreference.objects.all().delete()

