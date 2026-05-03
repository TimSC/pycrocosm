from django.test import TestCase
from django.test import Client
from django.test import override_settings
from django.urls import reverse
from django.contrib.auth.models import User
from django.utils import timezone
import datetime
import json

from .models import Oauth2Application, Oauth2AuthorizationCode

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
		self.assertNotContains(response, self.app.client_secret)

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

	def test_application_create_shows_secret_once_and_stores_hash(self):
		client = Client()
		client.login(username=self.owner.username, password=self.password)

		response = client.post(reverse("oauth2:applications"), {
			"name": "new app",
			"redirects": "urn:ietf:wg:oauth:2.0:oob https://example.com/callback",
		})

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, "New client secret")
		app = Oauth2Application.objects.get(name="new app")
		self.assertNotContains(response, app.client_secret)
		self.assertNotEqual(app.client_secret, "")
		self.assertTrue(app.client_secret.startswith("pbkdf2_"))

	def test_authorize_rejects_unregistered_redirect_uri(self):
		client = Client()
		client.login(username=self.owner.username, password=self.password)

		response = client.get(reverse("oauth2:authorize"), {
			"client_id": self.app.client_id,
			"redirect_uri": "https://evil.example/callback",
		})

		self.assertEqual(response.status_code, 400)
		self.assertEqual(Oauth2AuthorizationCode.objects.count(), 0)

	def test_token_rejects_redirect_uri_mismatch(self):
		self.app.redirect_uris = "urn:ietf:wg:oauth:2.0:oob https://example.com/callback"
		self.app.save()
		client = Client()
		client.login(username=self.owner.username, password=self.password)
		code = client.get(reverse("oauth2:authorize"), {
			"client_id": self.app.client_id,
			"redirect_uri": "https://example.com/callback",
		}).content.decode("utf-8")

		response = Client().post(reverse("oauth2:token"), {
			"client_id": self.app.client_id,
			"client_secret": self.app.client_secret,
			"code": code,
			"redirect_uri": "urn:ietf:wg:oauth:2.0:oob",
		})

		self.assertEqual(response.status_code, 400)

	def test_token_exchange_consumes_code_and_rejects_replay(self):
		client = Client()
		client.login(username=self.owner.username, password=self.password)
		code = client.get(reverse("oauth2:authorize"), {
			"client_id": self.app.client_id,
		}).content.decode("utf-8")

		token_response = Client().post(reverse("oauth2:token"), {
			"client_id": self.app.client_id,
			"client_secret": self.app.client_secret,
			"code": code,
			"redirect_uri": "urn:ietf:wg:oauth:2.0:oob",
		})

		self.assertEqual(token_response.status_code, 200)
		payload = json.loads(token_response.content.decode("utf-8"))
		self.assertIn("access_token", payload)
		self.assertEqual(payload["scope"], "read_prefs write_api")
		self.assertIsNotNone(Oauth2AuthorizationCode.objects.get().consumed_at)

		replay_response = Client().post(reverse("oauth2:token"), {
			"client_id": self.app.client_id,
			"client_secret": self.app.client_secret,
			"code": code,
			"redirect_uri": "urn:ietf:wg:oauth:2.0:oob",
		})

		self.assertEqual(replay_response.status_code, 400)

	@override_settings(OAUTH2_AUTH_CODE_SECONDS=-1)
	def test_token_exchange_rejects_expired_code(self):
		client = Client()
		client.login(username=self.owner.username, password=self.password)
		code = client.get(reverse("oauth2:authorize"), {
			"client_id": self.app.client_id,
		}).content.decode("utf-8")
		codeRecord = Oauth2AuthorizationCode.objects.get()
		codeRecord.expires_at = timezone.now() - datetime.timedelta(seconds=1)
		codeRecord.save()

		response = Client().post(reverse("oauth2:token"), {
			"client_id": self.app.client_id,
			"client_secret": self.app.client_secret,
			"code": code,
			"redirect_uri": "urn:ietf:wg:oauth:2.0:oob",
		})

		self.assertEqual(response.status_code, 400)
