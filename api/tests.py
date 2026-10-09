import json
import xml.etree.ElementTree as ET

from django.conf import settings
from django.test import TestCase
from django.test import Client

# Create your tests here.

class ApiFormatTestCase(TestCase):
	def setUp(self):
		self.client = Client()

	def test_versions(self):
		response = self.client.get("/api/versions")
		self.assertEqual(response.status_code, 200)
		self.assertEqual(response["Content-Type"], "text/xml")
		root = ET.fromstring(response.content)
		self.assertEqual(root.tag, "osm")
		self.assertEqual([v.text for v in root.findall("api/version")], [str(settings.API_VERSION)])
		self.assertEqual(root.attrib["generator"], settings.GENERATOR)

		response = self.client.get("/api/versions.json")
		self.assertEqual(response.status_code, 200)
		self.assertEqual(response["Content-Type"], "application/json")
		doc = json.loads(response.content)
		self.assertEqual(doc["api"], {"versions": [str(settings.API_VERSION)]})
		self.assertEqual(doc["version"], str(settings.API_VERSION))
		self.assertEqual(doc["generator"], settings.GENERATOR)

		self.assertEqual(self.client.post("/api/versions").status_code, 405)

	def test_capabilities_json(self):
		xml = ET.fromstring(self.client.get("/api/0.6/capabilities").content)
		for path in ("/api/0.6/capabilities.json", "/api/capabilities.json"):
			response = self.client.get(path)
			self.assertEqual(response.status_code, 200)
			self.assertEqual(response["Content-Type"], "application/json")
			doc = json.loads(response.content)
			api = doc["api"]
			# The same values as the XML document
			self.assertEqual(api["version"], dict(xml.find("api/version").attrib))
			self.assertEqual(str(api["area"]["maximum"]), xml.find("api/area").attrib["maximum"])
			self.assertEqual(str(api["waynodes"]["maximum"]), xml.find("api/waynodes").attrib["maximum"])
			self.assertEqual(str(api["changesets"]["maximum_elements"]),
				xml.find("api/changesets").attrib["maximum_elements"])
			self.assertEqual(api["status"], dict(xml.find("api/status").attrib))
			self.assertEqual([b["regex"] for b in doc["policy"]["imagery"]["blacklist"]],
				[b.attrib["regex"] for b in xml.findall("policy/imagery/blacklist")])
			self.assertEqual(doc["version"], str(settings.API_VERSION))

	def test_format_selection(self):
		# No suffix and no Accept header gives XML, as it always has
		response = self.client.get("/api/0.6/capabilities")
		self.assertEqual(response["Content-Type"], "text/xml")
		ET.fromstring(response.content)

		# The Accept header asks for JSON
		response = self.client.get("/api/0.6/capabilities", HTTP_ACCEPT="application/json")
		self.assertEqual(response["Content-Type"], "application/json")
		json.loads(response.content)

		# A suffix overrides the header, in both directions
		response = self.client.get("/api/0.6/capabilities.xml", HTTP_ACCEPT="application/json")
		self.assertEqual(response["Content-Type"], "text/xml")
		ET.fromstring(response.content)
		response = self.client.get("/api/0.6/capabilities.json", HTTP_ACCEPT="application/xml")
		self.assertEqual(response["Content-Type"], "application/json")

		# A browser's Accept header does not name JSON
		response = self.client.get("/api/0.6/capabilities",
			HTTP_ACCEPT="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8")
		self.assertEqual(response["Content-Type"], "text/xml")

		# A suffix on something that does not exist is still not found
		self.assertEqual(self.client.get("/api/0.6/nonsense.json").status_code, 404)
		self.assertEqual(self.client.get("/api/0.6/capabilities.yaml").status_code, 404)

	def test_permissions_json(self):
		response = self.client.get("/api/0.6/permissions.json")
		self.assertEqual(response.status_code, 200)
		doc = json.loads(response.content)
		self.assertEqual(doc["permissions"], [])
		self.assertEqual(doc["version"], str(settings.API_VERSION))

	def test_capabilities_limits(self):
		xml = ET.fromstring(self.client.get("/api/0.6/capabilities").content)
		api = json.loads(self.client.get("/api/0.6/capabilities.json").content)["api"]
		self.assertEqual(xml.find("api/relationmembers").attrib, {"maximum": str(settings.RELATION_MEMBERS_MAXIMUM)})
		self.assertEqual(api["relationmembers"], {"maximum": settings.RELATION_MEMBERS_MAXIMUM})
		for name in ("changesets", "notes"):
			for limit in ("default_query_limit", "maximum_query_limit"):
				self.assertGreater(api[name][limit], 0)
				self.assertEqual(xml.find("api/" + name).attrib[limit], str(api[name][limit]))
			self.assertLessEqual(api[name]["default_query_limit"], api[name]["maximum_query_limit"])
		self.assertEqual(xml.find("api/changesets").attrib["maximum_elements"], str(settings.CHANGESETS_MAXIMUM_ELEMENTS))

class PgmapConfigTestCase(TestCase):
	"""Settings shared with pgmap's command line tools are read from its config file."""

	def setUp(self):
		import tempfile, os
		handle, self.path = tempfile.mkstemp(suffix=".cfg")
		with os.fdopen(handle, "w") as out:
			out.write("dbname:db_map\n"
				"dbhost:  127.0.0.1  \n"
				"dbpass:a:b c:\n"
				"empty:\n"
				"spaced key:x\n"
				"#dump_path:/old/place\n"
				"dump_path:/first\n"
				"\n"
				"a line without a colon\n"
				"dump_path:/second\r\n"
				"last:no newline")
		self.addCleanup(os.remove, self.path)

	def test_values_by_name(self):
		import pgmap
		def get(name, *default):
			return pgmap.GetConfigValue(self.path, name, *default)
		self.assertEqual(get("dbname"), "db_map")
		# Spaces around a value are dropped; colons and spaces inside it are kept
		self.assertEqual(get("dbhost"), "127.0.0.1")
		self.assertEqual(get("dbpass"), "a:b c:")
		self.assertEqual(get("last"), "no newline")
		# A later line replaces an earlier one, and a commented line is another name
		self.assertEqual(get("dump_path"), "/second")
		self.assertEqual(get("#dump_path"), "/old/place")
		self.assertEqual(get("spaced key"), "x")
		# A name that is there with nothing after it is empty, not the default
		self.assertEqual(get("empty", "fallback"), "")
		# Without a line for the name, or without the file, the default applies
		self.assertEqual(get("dbuser"), "")
		self.assertEqual(get("dbuser", "pycrocosm"), "pycrocosm")
		self.assertEqual(get("DBNAME", "other"), "other")
		self.assertEqual(get("a line without a colon", "none"), "none")
		self.assertEqual(pgmap.GetConfigValue(self.path + ".missing", "dbname", "fallback"), "fallback")
		self.assertEqual(pgmap.GetConfigValue(self.path + ".missing", "dbname"), "")

	def test_settings_use_the_config_file(self):
		import os, pgmap
		# What the settings module does: values from the file it names, else its defaults
		self.assertTrue(settings.PGMAP_CONFIG.endswith(".cfg"))
		for key in ("NAME", "USER", "PASSWORD", "HOST", "PORT", "PREFIX", "PREFIX_MOD", "PREFIX_TEST"):
			self.assertIn(key, settings.MAP_DATABASE)
		if os.path.exists(settings.PGMAP_CONFIG) and "DJANGO_MAP_DB_PREFIX" not in os.environ:
			self.assertEqual(settings.MAP_DATABASE["PREFIX"],
				pgmap.GetConfigValue(settings.PGMAP_CONFIG, "dbtableprefix", settings.MAP_DATABASE["PREFIX"]))
