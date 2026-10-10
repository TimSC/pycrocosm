import re
import xml.etree.ElementTree as ET
from html import unescape
from urllib.parse import urlparse, parse_qs

from django.test import TestCase, Client, override_settings
from django.urls import reverse

from overpass import ql

class FrontPageTestCase(TestCase):
	@override_settings(OVERPASS_RATE_LIMIT_REQUESTS=0)
	def test_overpass_example_is_a_working_query(self):
		response = Client().get(reverse("frontpage:index"))
		self.assertEqual(response.status_code, 200)
		html = response.content.decode("utf-8")
		link = unescape(re.search(r'<a id="overpass_example" href="([^"]*)"', html).group(1))
		self.assertTrue(link.startswith("/api/interpreter?data="))
		# Nothing in the address needs a browser to tidy it up
		self.assertRegex(link, r"^[A-Za-z0-9/?=%._-]+$")

		# The query in the link is the one shown beside it, and is one this server accepts
		query = parse_qs(urlparse(link).query)["data"][0]
		self.assertIn("<code>{}</code>".format(query), unescape(html))
		ql.parse(query)

		# Following the link gives a map document, not an error
		answer = Client().get(link)
		self.assertEqual(answer.status_code, 200, answer.content)
		self.assertEqual(ET.fromstring(answer.content).tag, "osm")

	def test_xapi_example_is_a_working_request(self):
		from querymap.tests import DecodeOsmdataResponse
		html = Client().get(reverse("frontpage:index")).content.decode("utf-8")
		link = unescape(re.search(r'<a id="xapi_example" href="([^"]*)"', html).group(1))
		self.assertEqual(link, "/overpass/xapi_meta?*[bbox=-6.8115234,52.9751082,-5.625,53.7097136][amenity=hospital]")
		self.assertIn("<code>{}</code>".format(link.split("?", 1)[1]), html)
		# The two examples sit together, asking for the same thing
		self.assertLess(html.index('id="overpass_example"'), html.index('id="xapi_example"'))

		answer = Client().get(link)
		self.assertEqual(answer.status_code, 200, answer.content)
		self.assertEqual(answer["Content-Type"], "text/xml")
		DecodeOsmdataResponse([answer.content]) # An OSM document this server can read back
