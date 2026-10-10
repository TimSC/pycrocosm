import os
import shutil
import tempfile
from unittest.mock import patch

from django.test import SimpleTestCase, Client, override_settings
from django.urls import reverse

class PlanetDumpListTestCase(SimpleTestCase):
	"""The public page listing the dumps made by "manage.py dumpplanet"."""

	def setUp(self):
		self.folder = tempfile.mkdtemp()
		self.addCleanup(shutil.rmtree, self.folder, True)
		self.url = reverse("replication:planet_dumps")
		overrides = override_settings(PLANET_DUMP_DIR=self.folder, PLANET_DUMP_URL="/static/planet/")
		overrides.enable()
		self.addCleanup(overrides.disable)

	def add(self, name, size, modified):
		path = os.path.join(self.folder, name)
		with open(path, "wb") as out:
			out.write(b"x" * size)
		os.utime(path, (modified, modified))
		return path

	def page(self):
		response = Client().get(self.url)
		return response, response.content.decode("utf-8")

	def test_empty(self):
		self.assertTrue(self.url.endswith("/replication/planet"))
		response, html = self.page()
		self.assertEqual(response.status_code, 200)
		self.assertIn("No dumps are available", html)
		# The same before the folder has ever been made
		with override_settings(PLANET_DUMP_DIR=os.path.join(self.folder, "not made yet")):
			response, html = self.page()
		self.assertEqual(response.status_code, 200)
		self.assertIn("No dumps are available", html)
		self.assertEqual(Client().post(self.url).status_code, 405)

	def test_lists_finished_dumps_newest_first(self):
		self.add("fosm-planet_20260401013701.o5m", 2048, 1775007421)
		self.add("fosm-planet_20260501013701.o5m", 5 * 1024 * 1024, 1777599421)
		self.add("fosm-planet_20260301000000.osm.gz", 10, 1772323200)
		self.add("odd name & more.pbf", 1, 1700000000)
		# Not dumps: one being written, hidden files, other files, and folders
		self.add("fosm-planet_20260601013701.o5m.tmp.Ab12Cd", 99, 1780000000)
		self.add(".hidden.o5m", 99, 1780000000)
		self.add("README.txt", 99, 1780000000)
		self.add("planet.o5m.bak", 99, 1780000000)
		os.mkdir(os.path.join(self.folder, "folder.o5m"))

		response, html = self.page()
		self.assertEqual(response.status_code, 200)
		dumps = response.context["dumps"]
		self.assertEqual([d["name"] for d in dumps], ["fosm-planet_20260501013701.o5m",
			"fosm-planet_20260401013701.o5m", "fosm-planet_20260301000000.osm.gz", "odd name & more.pbf"])
		self.assertEqual([d["size"] for d in dumps], [5 * 1024 * 1024, 2048, 10, 1])
		self.assertEqual(dumps[0]["url"], "/static/planet/fosm-planet_20260501013701.o5m")
		self.assertEqual(dumps[3]["url"], "/static/planet/odd%20name%20%26%20more.pbf")

		self.assertIn('<a href="/static/planet/fosm-planet_20260501013701.o5m">fosm-planet_20260501013701.o5m</a>', html)
		self.assertIn('<a href="/static/planet/odd%20name%20%26%20more.pbf">odd name &amp; more.pbf</a>', html)
		self.assertIn('title="5242880 bytes"', html)
		self.assertIn("5.0\xa0MB", html)
		self.assertIn("2026-05-01 01:37 UTC", html)
		self.assertLess(html.index("20260501013701"), html.index("20260401013701"))
		for absent in (".tmp.", ".hidden", "README", ".bak", "folder.o5m", self.folder):
			self.assertNotIn(absent, html)

	def test_download_address_follows_the_setting(self):
		self.add("planet.o5m", 1, 1777599421)
		for base in ("https://downloads.example.org/planet", "https://downloads.example.org/planet/"):
			with override_settings(PLANET_DUMP_URL=base):
				response, html = self.page()
			self.assertIn('href="https://downloads.example.org/planet/planet.o5m"', html)

	def test_front_page_links_to_it(self):
		front = Client().get(reverse("frontpage:index"))
		self.assertEqual(front.status_code, 200)
		self.assertIn('href="{}"'.format(self.url), front.content.decode("utf-8"))

	def test_failure_gives_a_plain_error_page(self):
		with patch("replicate.views.os.listdir", side_effect=PermissionError("secret path")):
			response, html = self.page()
		self.assertEqual(response.status_code, 500)
		self.assertIn("not available at the moment", html)
		self.assertNotIn("secret path", html)
