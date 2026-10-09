import json

from django.test import TestCase
from django.test import Client
from django.urls import reverse
from django.contrib.auth.models import User
from django.conf import settings

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
		response = self.client.get(reverse('users:details'))

		self.assertEqual(response.status_code, 200)
		
		xml = fromstring(response.content)
		self.assertEqual(xml.tag, "osm")
		userout = xml.find("user")
		self.assertEqual(int(userout.attrib["id"]) == self.user.id, True)
		self.assertEqual("account_created" in userout.attrib, True)
		self.assertEqual("display_name" in userout.attrib, True)

	def test_get_details_json(self):
		response = self.client.get("/api/0.6/user/details.json")
		self.assertEqual(response.status_code, 200)
		self.assertEqual(response["Content-Type"], "application/json")
		doc = json.loads(response.content)
		self.assertEqual(doc["version"], "0.6")
		user = doc["user"]
		self.assertEqual(user["id"], self.user.id)
		self.assertEqual(user["display_name"], self.user.username)
		self.assertIn("account_created", user)

		# The same details as the XML form
		xml = fromstring(self.client.get(reverse('users:details')).content).find("user")
		self.assertEqual(str(user["id"]), xml.attrib["id"])
		self.assertEqual(user["display_name"], xml.attrib["display_name"])
		self.assertEqual(user["account_created"], xml.attrib["account_created"])

		self.assertIn(Client().get("/api/0.6/user/details.json").status_code, (401, 403))

	def test_get_preferences_json(self):
		response = self.client.get("/api/0.6/user/preferences.json")
		self.assertEqual(response.status_code, 200)
		self.assertEqual(response["Content-Type"], "application/json")
		self.assertEqual(json.loads(response.content)["preferences"], {"foo": "bar"})

		byHeader = self.client.get(reverse('users:preferences'), HTTP_ACCEPT="application/json")
		self.assertEqual(json.loads(byHeader.content)["preferences"], {"foo": "bar"})

	def test_preference_key_may_end_in_json(self):
		# A key is free text, so a trailing .json is part of the key, not a format
		response = self.client.put("/api/0.6/user/preferences/layout.json", "wide", content_type='text/plain')
		self.assertEqual(response.status_code, 200)
		prefs = json.loads(self.client.get("/api/0.6/user/preferences.json").content)["preferences"]
		self.assertEqual(prefs.get("layout.json"), "wide")

	def test_get_details_anon(self):
		anonClient = Client()
		response = anonClient.get(reverse('users:details'))
		self.assertEqual(response.status_code, 403)

	def test_get_preferences(self):
		response = self.client.get(reverse('users:preferences'))
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
		response = anonClient.get(reverse('users:preferences'))
		self.assertEqual(response.status_code, 403)
	
	def test_put_preferences(self):

		response = self.client.put(reverse('users:preferences'), self.prefXml, content_type='application/xml')
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
		response = anonClient.put(reverse('users:preferences'), self.prefXml, content_type='application/xml')
		self.assertEqual(response.status_code, 403)

	def test_put_preference_single(self):
		response = self.client.put(reverse('users:preferences_put', args=["agree"]), "details", content_type='text/plain')
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
		response = anonClient.put(reverse('users:preferences_put', args=["agree"]), "details", content_type='text/plain')
		self.assertEqual(response.status_code, 403)

	def test_get_public_user(self):
		self.user.userdata.description = "Plays guitar"
		self.user.userdata.home_lat, self.user.userdata.home_zoom = 53.4, 12
		self.user.userdata.save()
		created = self.user.date_joined.strftime("%Y-%m-%dT%H:%M:%SZ")

		# Anyone may ask, and nothing private is included
		anonClient = Client()
		response = anonClient.get("/api/0.6/user/{}".format(self.user.id))
		self.assertEqual(response.status_code, 200)
		self.assertEqual(response["Content-Type"], "text/xml")
		xml = fromstring(response.content)
		self.assertEqual(xml.tag, "osm")
		self.assertEqual(len(xml), 1)
		user = xml.find("user")
		self.assertEqual(user.attrib, {"id": str(self.user.id), "display_name": self.username,
			"account_created": created})
		self.assertEqual(user.find("description").text, "Plays guitar")
		self.assertEqual(user.find("changesets").attrib["count"], "0")
		self.assertEqual(user.find("traces").attrib["count"], "0")
		self.assertEqual(user.find("blocks/received").attrib, {"count": "0", "active": "0"})
		self.assertIsNotNone(user.find("roles"))
		self.assertIsNotNone(user.find("contributor-terms"))
		for private in ("home", "languages", "messages"):
			self.assertIsNone(user.find(private))

		for response in (anonClient.get("/api/0.6/user/{}.json".format(self.user.id)),
			anonClient.get("/api/0.6/user/{}".format(self.user.id), HTTP_ACCEPT="application/json")):
			self.assertEqual(response.status_code, 200)
			self.assertEqual(response["Content-Type"], "application/json")
			doc = json.loads(response.content)
			self.assertEqual(doc["user"], {"id": self.user.id, "display_name": self.username,
				"account_created": created, "description": "Plays guitar",
				"contributor_terms": {"agreed": False}, "roles": [], "changesets": {"count": 0},
				"traces": {"count": 0}, "blocks": {"received": {"count": 0, "active": 0}}})

		self.assertEqual(anonClient.get("/api/0.6/user/999999999").status_code, 404)
		self.assertEqual(anonClient.get("/api/0.6/user/99999999999999999999").status_code, 404)
		self.assertEqual(anonClient.post("/api/0.6/user/{}".format(self.user.id)).status_code, 405)
		# The existing calls under the same path still work
		self.assertEqual(self.client.get(reverse('users:details')).status_code, 200)

		self.user.is_active = False
		self.user.save()
		self.assertEqual(anonClient.get("/api/0.6/user/{}".format(self.user.id)).status_code, 410)

	def test_public_user_changeset_count(self):
		from changeset.tests import CreateTestChangeset
		from pycrocosm.mapdb import get_pgmap
		import pgmap
		other = User.objects.create_user("paul", "pmccartney@beatles.com", "blackbird")
		try:
			for owner in (self.user, self.user, other):
				CreateTestChangeset(owner)
			counts = {}
			for u in fromstring(Client().get("/api/0.6/users?users={},{}".format(other.id, self.user.id)).content):
				counts[int(u.attrib["id"])] = int(u.find("changesets").attrib["count"])
			self.assertEqual(counts, {self.user.id: 2, other.id: 1})
		finally:
			other.delete()
			t = get_pgmap().GetTransaction("EXCLUSIVE")
			t.ResetActiveTables(pgmap.PgMapError())
			t.Commit()

	def test_get_public_users(self):
		others = [User.objects.create_user(name, name + "@beatles.com", "password") for name in ("paul", "ringo")]
		gone = User.objects.create_user("pete", "pete@beatles.com", "password")
		gone.is_active = False
		gone.save()
		try:
			anonClient = Client()
			wanted = [others[1].id, self.user.id, others[0].id]
			query = ",".join(str(i) for i in wanted + [gone.id, 999999999, wanted[0]])
			response = anonClient.get("/api/0.6/users?users=" + query)
			self.assertEqual(response.status_code, 200)
			xml = fromstring(response.content)
			# In ID order, without unknown or deleted users, each listed once
			self.assertEqual([int(u.attrib["id"]) for u in xml.findall("user")], sorted(wanted))
			self.assertEqual(len(xml), 3)

			response = anonClient.get("/api/0.6/users.json?users=" + query)
			self.assertEqual(response["Content-Type"], "application/json")
			doc = json.loads(response.content)
			self.assertEqual([u["user"]["id"] for u in doc["users"]], sorted(wanted))
			self.assertEqual(doc["users"][0]["user"]["display_name"],
				User.objects.get(id=sorted(wanted)[0]).username)

			self.assertEqual(len(fromstring(anonClient.get("/api/0.6/users?users=999999999").content)), 0)
			for bad in ("/api/0.6/users", "/api/0.6/users?users=", "/api/0.6/users?users=1,x",
				"/api/0.6/users?users=" + ",".join(str(i) for i in range(1, settings.MULTIFETCH_MAXIMUM_IDS + 2))):
				self.assertEqual(anonClient.get(bad).status_code, 400, bad[:40])
		finally:
			for u in others + [gone]:
				u.delete()

	def tearDown(self):
		u = User.objects.get(username = self.username)
		u.delete()

		UserPreference.objects.all().delete()

