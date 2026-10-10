import json
import xml.etree.ElementTree as ET

from django.conf import settings
from django.test import TestCase, override_settings
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
		from unittest.mock import patch
		import os
		# pgmap reads the file the environment names
		named = patch.dict(os.environ, {"PGMAP_CONFIG": self.path})
		named.start()
		self.addCleanup(named.stop)
		def get(name, *default):
			return pgmap.GetConfigValue(name, *default)
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
		with patch.dict(os.environ, {"PGMAP_CONFIG": self.path + ".missing"}):
			self.assertEqual(get("dbname", "fallback"), "fallback")
			self.assertEqual(get("dbname"), "")
		# With nothing named, it is config.cfg in the current directory, as for the tools
		with patch.dict(os.environ, {"PGMAP_CONFIG": ""}):
			here = os.getcwd()
			os.chdir(os.path.dirname(self.path))
			try:
				self.assertEqual(get("dbname", "no config.cfg here"), "no config.cfg here")
				os.symlink(self.path, "config.cfg")
				try:
					self.assertEqual(get("dbname", "fallback"), "db_map")
				finally:
					os.remove("config.cfg")
			finally:
				os.chdir(here)

	def test_settings_use_the_config_file(self):
		import os, pgmap
		# The settings module names the project's copy unless the environment named another
		self.assertTrue(os.environ["PGMAP_CONFIG"].endswith(".cfg"))
		for key in ("NAME", "USER", "PASSWORD", "HOST", "PORT", "PREFIX", "PREFIX_MOD", "PREFIX_TEST"):
			self.assertIn(key, settings.MAP_DATABASE)
		if os.path.exists(os.environ["PGMAP_CONFIG"]) and "DJANGO_MAP_DB_PREFIX" not in os.environ:
			self.assertEqual(settings.MAP_DATABASE["PREFIX"],
				pgmap.GetConfigValue("dbtableprefix", settings.MAP_DATABASE["PREFIX"]))

class SingleRunTestCase(TestCase):
	"""Scheduled commands do not start while their previous run is still going."""

	def other_session(self):
		"""A second connection to the settings database, as another process would have."""
		import psycopg2
		from django.db import connection
		other = psycopg2.connect(**connection.get_connection_params())
		other.autocommit = True
		self.addCleanup(other.close)
		return other

	def hold(self, session, name):
		from pycrocosm.singlerun import lock_number
		with session.cursor() as cursor:
			cursor.execute("SELECT pg_try_advisory_lock(%s)", [lock_number(name)])
			return cursor.fetchone()[0]

	def release(self, session, name):
		from pycrocosm.singlerun import lock_number
		with session.cursor() as cursor:
			cursor.execute("SELECT pg_advisory_unlock(%s)", [lock_number(name)])

	def test_lock_is_held_for_the_block_only(self):
		from pycrocosm.singlerun import single_run, lock_number
		other = self.other_session()
		with single_run("test task") as free:
			self.assertIs(free, True)
			# Another process cannot take it, but can take a different one
			self.assertFalse(self.hold(other, "test task"))
			self.assertTrue(self.hold(other, "another task"))
		self.assertTrue(self.hold(other, "test task"))
		# Now the other process has it, and this one is told so without waiting
		with single_run("test task") as free:
			self.assertIs(free, False)
		self.assertFalse(self.hold(self.other_session(), "test task"))
		self.release(other, "test task")
		with single_run("test task") as free:
			self.assertIs(free, True)
		# It is released even when the work fails
		with self.assertRaises(ValueError):
			with single_run("test task"):
				raise ValueError("failed")
		self.assertTrue(self.hold(other, "test task"))
		self.assertNotEqual(lock_number("a"), lock_number("b"))
		self.assertEqual(lock_number("a"), lock_number("a"))

	def test_lock_dies_with_its_process(self):
		from pycrocosm.singlerun import single_run
		crashed = self.other_session()
		self.assertTrue(self.hold(crashed, "test task"))
		with single_run("test task") as free:
			self.assertIs(free, False)
		crashed.close() # As when a command is killed: nothing is left to clear up
		with single_run("test task") as free:
			self.assertIs(free, True)

	def test_file_lock_without_postgresql(self):
		import fcntl, os, tempfile
		from pycrocosm.singlerun import single_run
		name = "test task {}".format(os.getpid())
		path = os.path.join(tempfile.gettempdir(), "pycrocosm-{}.lock".format(name))
		self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
		with single_run(name, use_database=False) as free:
			self.assertIs(free, True)
			with open(path, "w") as rival:
				with self.assertRaises(OSError):
					fcntl.flock(rival, fcntl.LOCK_EX | fcntl.LOCK_NB)
			with single_run(name, use_database=False) as second:
				self.assertIs(second, False)
		with single_run(name, use_database=False) as free:
			self.assertIs(free, True)

	def test_commands_do_nothing_while_an_earlier_run_goes_on(self):
		import io
		from unittest.mock import patch
		from django.core.management import call_command
		# Loaded before anything is patched, so that they keep the real functions
		import changeset.management.commands.closeoldchangesets
		import querymap.management.commands.dumpplanet
		import replicate.management.commands.updateextracts
		other = self.other_session()
		for command, target in (("closeoldchangesets", "changeset.management.commands.closeoldchangesets.get_pgmap"),
			("updateextracts", "replicate.extracts.get_pgmap"),
			("dumpplanet", "querymap.management.commands.dumpplanet.get_pgmap")):
			self.assertTrue(self.hold(other, command))
			out = io.StringIO()
			# The map is not touched: reaching it would fail the test
			with patch(target, side_effect=AssertionError("the command ran")):
				call_command(command, stdout=out, no_color=True)
			self.assertEqual(out.getvalue(),
				"An earlier {} is still running, so this run does nothing\n".format(command))
			self.release(other, command)

		# With the lock free the command runs: here, far enough to ask for the map
		out = io.StringIO()
		with patch("replicate.extracts.get_pgmap", side_effect=RuntimeError("asked for the map")):
			with self.assertRaisesRegex(RuntimeError, "asked for the map"):
				call_command("updateextracts", stdout=out, no_color=True)
		# and has let go of the lock afterwards, though it failed
		self.assertTrue(self.hold(other, "updateextracts"))

class BehindHttpsProxyTestCase(TestCase):
	"""What the application needs to be told when a reverse proxy provides HTTPS."""

	def login(self):
		from django.contrib.auth.models import User
		User.objects.create_user("proxied", "proxied@example.com", "password")
		client = Client(enforce_csrf_checks=True)
		# As the proxy passes requests on: plain HTTP, saying what the visitor used
		through_proxy = {"HTTP_HOST": "maps.example.org", "HTTP_X_FORWARDED_PROTO": "https"}
		page = client.get("/accounts/login/", **through_proxy)
		self.assertEqual(page.status_code, 200)
		token = page.cookies["csrftoken"].value
		return client.post("/accounts/login/", {"username": "proxied", "password": "password",
			"csrfmiddlewaretoken": token}, HTTP_ORIGIN="https://maps.example.org", **through_proxy)

	@override_settings(ALLOWED_HOSTS=["maps.example.org"], LOGIN_RATE_LIMIT_REQUESTS=0)
	def test_login_is_refused_until_the_proxy_is_declared(self):
		# The form came from https://maps.example.org but arrives as plain HTTP
		self.assertEqual(self.login().status_code, 403)

	@override_settings(ALLOWED_HOSTS=["maps.example.org"], LOGIN_RATE_LIMIT_REQUESTS=0,
		CSRF_TRUSTED_ORIGINS=["https://maps.example.org"],
		SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"))
	def test_login_works_with_the_proxy_settings(self):
		response = self.login()
		self.assertEqual(response.status_code, 302, response.content[:300])

	def test_settings_are_off_unless_asked_for(self):
		import os
		if not os.environ.get("DJANGO_BEHIND_HTTPS_PROXY"):
			self.assertIsNone(settings.SECURE_PROXY_SSL_HEADER)
		if not os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS"):
			self.assertEqual(settings.CSRF_TRUSTED_ORIGINS, [])
