from django.test import TestCase
from django.test import Client
from django.urls import reverse
from django.contrib.auth.models import User

from .models import Oauth2Application

# Create your tests here.

class Oauth2ApplicationViewsTestCase(TestCase):
	def setUp(self):
		self.password = "glass onion"
		self.owner = User.objects.create_user("owner", "owner@example.com", self.password)
		self.other = User.objects.create_user("other", "other@example.com", self.password)
		self.app = Oauth2Application.objects.create(
			name="test app",
			user=self.owner,
			client_id="client-id-1",
			client_secret="secret-value",
			redirect_uris="urn:ietf:wg:oauth:2.0:oob",
			confidential=False,
			permission_read_prefs=True,
			permission_write_prefs=True,
			permission_write_diary=True,
			permission_write_api=True,
			permission_read_gpx=True,
			permission_write_gpx=True,
			permission_write_notes=True,
			permission_write_redactions=True,
			permission_consume_messages=True,
			permission_send_messages=True,
			permission_openid=True,
			disabled=False)

	def test_applications_requires_login(self):
		response = Client().get(reverse("oauth2:applications"))
		self.assertEqual(response.status_code, 302)
		self.assertIn("/accounts/login/", response["Location"])

	def test_application_detail_requires_login(self):
		response = Client().get(reverse("oauth2:applications_detail", args=[self.app.client_id]))
		self.assertEqual(response.status_code, 302)
		self.assertIn("/accounts/login/", response["Location"])

	def test_owner_can_view_application_detail(self):
		client = Client()
		client.login(username=self.owner.username, password=self.password)

		response = client.get(reverse("oauth2:applications_detail", args=[self.app.client_id]))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, self.app.client_id)
		self.assertContains(response, self.app.client_secret)

	def test_other_user_cannot_view_application_detail(self):
		client = Client()
		client.login(username=self.other.username, password=self.password)

		response = client.get(reverse("oauth2:applications_detail", args=[self.app.client_id]))

		self.assertEqual(response.status_code, 404)
		self.assertNotContains(response, self.app.client_secret, status_code=404)

	def test_applications_list_only_contains_current_user_apps(self):
		client = Client()
		client.login(username=self.other.username, password=self.password)

		response = client.get(reverse("oauth2:applications"))

		self.assertEqual(response.status_code, 200)
		self.assertNotContains(response, self.app.client_id)
		self.assertNotContains(response, self.app.client_secret)
