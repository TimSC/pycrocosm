from django.contrib.auth.models import User, Permission
from django.test import TestCase, Client
from django.urls import reverse

import pgmap
from pycrocosm.mapdb import get_pgmap
from . import mapmeta

class MapParameterAdminTestCase(TestCase):
	"""The admin page for the metadata parameters pgmap keeps in the map database."""

	def setUp(self):
		self.url = reverse("admin:querymap_mapparameter_changelist")
		self.set_url = reverse("admin:querymap_mapparameter_set")
		self.remove_url = reverse("admin:querymap_mapparameter_remove")
		self.admin = Client()
		User.objects.create_superuser("root", "root@example.com", "password")
		self.admin.login(username="root", password="password")
		self.addCleanup(self.forget, "test.parameter", "useBboxInQuery", "readonly")

	def forget(self, *keys):
		t = get_pgmap().GetTransaction("EXCLUSIVE")
		for key in keys:
			t.DeleteMetaValue(key)
		t.Commit()

	def stored(self):
		t = get_pgmap().GetTransaction("ACCESS SHARE")
		try:
			values = pgmap.mapstringstring()
			t.GetMetaValues(values)
			return dict(values)
		finally:
			t.Abort()

	def post(self, url, client=None, **data):
		response = (client or self.admin).post(url, data, follow=True)
		return response, response.content.decode("utf-8")

	def test_page_lists_parameters(self):
		response = self.admin.get(self.url)
		self.assertEqual(response.status_code, 200)
		html = response.content.decode("utf-8")
		parameters = {p["key"]: p for p in response.context_data["parameters"]}
		# Known parameters are listed whether or not they are set
		self.assertEqual(parameters["useBboxInQuery"]["value"], None)
		self.assertEqual(parameters["useBboxInQuery"]["default"], "0")
		self.assertTrue(parameters["useBboxInQuery"]["switch"])
		self.assertEqual(parameters["readonly"]["value"], None)
		self.assertTrue(parameters["schema_version"]["protected"])
		self.assertEqual(parameters["schema_version"]["value"], self.stored()["schema_version"])
		self.assertIn("not set; 0 applies", html)
		self.assertIn("Update way/relation bboxes", html)
		# The layout version is shown but offers nothing to change it with
		self.assertNotIn('name="key" value="schema_version"', html)
		self.assertIn('name="key" value="useBboxInQuery"', html)
		self.assertIn('id="add_parameter"', html)
		self.assertIn('id="static_parameters"', html)
		self.assertEqual(response.context_data["static_parameters"],
			[p for p in response.context_data["static_parameters"] if p["value"] is not None])
		# It is reached from the admin index
		self.assertIn(self.url, self.admin.get(reverse("admin:index")).content.decode("utf-8"))

	def test_set_and_remove(self):
		response, html = self.post(self.set_url, key="test.parameter", value=" first ")
		self.assertEqual(response.status_code, 200)
		self.assertIn("Set test.parameter to first.", html)
		self.assertEqual(self.stored()["test.parameter"], "first")
		self.assertIn('name="key" value="test.parameter"', html)
		self.assertIn('name="value" value="first"', html)

		response, html = self.post(self.set_url, key="test.parameter", value="second <&>")
		self.assertEqual(self.stored()["test.parameter"], "second <&>")
		self.assertIn('value="second &lt;&amp;&gt;"', html)
		response, html = self.post(self.set_url, key="test.parameter", value="")
		self.assertEqual(self.stored()["test.parameter"], "")

		response, html = self.post(self.remove_url, key="test.parameter")
		self.assertIn("Removed test.parameter.", html)
		self.assertNotIn("test.parameter", self.stored())
		response, html = self.post(self.remove_url, key="test.parameter")
		self.assertIn("The parameter test.parameter was not set.", html)

	def test_switches_change_how_the_map_behaves(self):
		def uses_bboxes():
			t = get_pgmap().GetTransaction("ACCESS SHARE")
			try:
				return t.UseBboxInQuery()
			finally:
				t.Abort()
		self.assertFalse(uses_bboxes())
		response, html = self.post(self.set_url, key="useBboxInQuery", value="1")
		self.assertIn("Set useBboxInQuery to 1.", html)
		self.assertTrue(uses_bboxes())
		self.assertEqual({p["key"]: p["value"] for p in response.context_data["parameters"]}["useBboxInQuery"], "1")
		response, html = self.post(self.set_url, key="useBboxInQuery", value="0")
		self.assertFalse(uses_bboxes())
		response, html = self.post(self.set_url, key="useBboxInQuery", value="1")
		response, html = self.post(self.remove_url, key="useBboxInQuery")
		self.assertFalse(uses_bboxes())
		self.assertNotIn("useBboxInQuery", self.stored())

		for value in ("2", "yes", "", "01"):
			response, html = self.post(self.set_url, key="useBboxInQuery", value=value)
			self.assertIn("The parameter useBboxInQuery must be 0 or 1.", html)
			response, html = self.post(self.set_url, key="readonly", value=value)
			self.assertIn("The parameter readonly must be 0 or 1.", html)
		self.assertNotIn("useBboxInQuery", self.stored())
		self.assertNotIn("readonly", self.stored())

	def test_ways_without_boxes_are_warned_about(self):
		from unittest.mock import patch
		with patch.object(pgmap.PgTransaction, "CountWaysWithoutBbox", return_value=[36489, 36000]):
			response, html = self.post(self.set_url, key="useBboxInQuery", value="1")
		self.assertIn("36000 of the map&#x27;s 36489 ways have no bounding box stored", html)
		self.assertEqual(self.stored()["useBboxInQuery"], "1")
		with patch.object(pgmap.PgTransaction, "CountWaysWithoutBbox", return_value=[36489, 0]):
			response, html = self.post(self.set_url, key="useBboxInQuery", value="1")
		self.assertNotIn("no bounding box stored", html)
		# The count itself: every way of the map, and those without a box
		t = get_pgmap().GetTransaction("ACCESS SHARE")
		try:
			total, missing = list(t.CountWaysWithoutBbox())
		finally:
			t.Abort()
		self.assertGreaterEqual(total, missing)
		self.assertGreaterEqual(missing, 0)

	def test_refused_changes(self):
		version = self.stored()["schema_version"]
		for url, data in ((self.set_url, {"key": "schema_version", "value": "99"}),
			(self.remove_url, {"key": "schema_version"})):
			response, html = self.post(url, **data)
			self.assertIn("The parameter schema_version cannot be changed here.", html)
		self.assertEqual(self.stored()["schema_version"], version)

		before = self.stored()
		for key in ("", "has space", "semi;colon", "quote'", "x" * 101):
			response, html = self.post(self.set_url, key=key, value="1")
			self.assertIn("A parameter name is up to 100 letters", html)
		response, html = self.post(self.set_url, key="test.parameter", value="v" * 1001)
		self.assertIn("A value is at most 1000 characters.", html)
		self.assertEqual(self.stored(), before)
		self.assertEqual(self.admin.get(self.set_url).status_code, 405)
		self.assertEqual(self.admin.get(self.remove_url).status_code, 405)

	def test_permissions(self):
		anonymous = Client()
		self.assertEqual(anonymous.get(self.url).status_code, 302)
		self.assertEqual(anonymous.post(self.set_url, {"key": "test.parameter", "value": "1"}).status_code, 302)

		staff_user = User.objects.create_user("staff", "staff@example.com", "password", is_staff=True)
		staff = Client()
		staff.login(username="staff", password="password")
		response = staff.get(self.url)
		self.assertEqual(response.status_code, 200)
		html = response.content.decode("utf-8")
		# Staff may look, but are offered nothing to change
		self.assertIn("useBboxInQuery", html)
		self.assertNotIn("<form", html.split('id="content-main"')[1])
		self.assertEqual(staff.post(self.set_url, {"key": "test.parameter", "value": "1"}).status_code, 403)
		self.assertEqual(staff.post(self.remove_url, {"key": "schema_version"}).status_code, 403)
		self.assertNotIn("test.parameter", self.stored())

		ordinary = Client()
		User.objects.create_user("mapper", "mapper@example.com", "password")
		ordinary.login(username="mapper", password="password")
		self.assertEqual(ordinary.get(self.url).status_code, 302)

		# The change permission lets a staff user edit
		staff_user.user_permissions.add(Permission.objects.get(
			content_type__app_label="querymap", codename="change_mapparameter"))
		response = staff.post(self.set_url, {"key": "test.parameter", "value": "granted"}, follow=True)
		self.assertEqual(response.status_code, 200)
		self.assertEqual(self.stored()["test.parameter"], "granted")

	def test_parameter_functions(self):
		with self.assertRaises(mapmeta.ParameterError):
			mapmeta.set_parameter("schema_version", "1")
		with self.assertRaises(mapmeta.ParameterError):
			mapmeta.remove_parameter("bad key")
		self.assertIsNone(mapmeta.set_parameter("test.parameter", "x"))
		active, static = mapmeta.list_parameters()
		self.assertEqual([p["value"] for p in active if p["key"] == "test.parameter"], ["x"])
		self.assertEqual([p["key"] for p in active], sorted(p["key"] for p in active))
		self.assertTrue(mapmeta.remove_parameter("test.parameter"))
		self.assertFalse(mapmeta.remove_parameter("test.parameter"))
