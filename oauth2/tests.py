from django.test import TestCase
from django.test import Client
from django.test import override_settings
from django.urls import reverse
from django.contrib.auth.models import User
from django.utils import timezone
from django.core.cache import cache
import datetime
import json
import re
import base64
import hashlib
from urllib.parse import parse_qs, urlsplit

from .models import Oauth2Application, Oauth2Authorization, Oauth2AuthorizationCode
from .scopes import SCOPES
from .views import create_authorization

def approve(client, params):
	"""The code the authorize page gives when the signed in user allows the
	application: shown on the page for the out of band URI, or else in the
	query of the redirect back to the application."""
	response = client.post(reverse("oauth2:authorize"), dict(params, decision="approve"))
	if response.status_code == 302:
		return parse_qs(urlsplit(response["Location"]).query)["code"][0]
	assert response.status_code == 200, response.content
	return re.search(r'<code id="code">([^<]+)</code>', response.content.decode("utf-8")).group(1)

# Create your tests here.

class Oauth2ApplicationViewsTestCase(TestCase):
	def setUp(self):
		cache.clear()
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
			"permission_write_api": "1",
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
		code = approve(client, {
			"client_id": self.app.client_id,
			"redirect_uri": "https://example.com/callback",
		})

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
		code = approve(client, {
			"client_id": self.app.client_id,
		})

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

	@override_settings(OAUTH2_RATE_LIMIT_REQUESTS=1, OAUTH2_RATE_LIMIT_WINDOW_SECONDS=60)
	def test_authorize_is_rate_limited(self):
		client = Client()
		client.login(username=self.owner.username, password=self.password)

		first = client.get(reverse("oauth2:authorize"), {
			"client_id": self.app.client_id,
		})
		second = client.get(reverse("oauth2:authorize"), {
			"client_id": self.app.client_id,
		})

		self.assertEqual(first.status_code, 200)
		self.assertEqual(second.status_code, 429)

	@override_settings(OAUTH2_AUTH_CODE_SECONDS=-1)
	def test_token_exchange_rejects_expired_code(self):
		client = Client()
		client.login(username=self.owner.username, password=self.password)
		code = approve(client, {
			"client_id": self.app.client_id,
		})
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

class ScopeTestCase(TestCase):
	"""What an OAuth2 token may do follows the scopes its authorization grants."""

	def setUp(self):
		cache.clear()
		self.user = User.objects.create_user("mapper", "mapper@example.com", "glass onion")
		self.app = Oauth2Application.objects.create(name="editor", user=self.user, client_id="editor",
			client_secret="", redirect_uris="urn:ietf:wg:oauth:2.0:oob", confidential=False,
			disabled=False, **{"permission_" + scope: True for scope in SCOPES})

	def token(self, *scopes):
		"""A token whose authorization grants only these scopes."""
		token, _ = create_authorization(self.user, self.app)
		Oauth2Authorization.objects.filter(user=self.user).order_by("-id").update(
			**{"permission_" + scope: scope in scopes for scope in SCOPES})
		return token

	def call(self, method, path, token, data=None, content_type="text/xml"):
		# Tokens are accepted only over HTTPS
		return getattr(Client(), method)(path, data, content_type=content_type, secure=True,
			HTTP_AUTHORIZATION="Bearer " + token)

	def create_changeset(self, token):
		return self.call("put", "/api/0.6/changeset/create", token,
			"<osm><changeset><tag k='created_by' v='tests'/></changeset></osm>")

	def test_scopes_limit_what_a_token_may_do(self):
		reader = self.token("read_prefs")
		self.assertEqual(self.call("get", "/api/0.6/user/details", reader).status_code, 200)
		self.assertEqual(self.call("get", "/api/0.6/user/preferences", reader).status_code, 200)
		for response in (self.call("put", "/api/0.6/user/preferences/colour", reader, "blue", "text/plain"),
				self.create_changeset(reader)):
			self.assertEqual(response.status_code, 403)
		self.assertIn(b"write_api", self.create_changeset(reader).content)

		editor = self.token("write_api")
		created = self.create_changeset(editor)
		self.assertEqual(created.status_code, 200, created.content)
		self.assertEqual(self.call("get", "/api/0.6/user/details", editor).status_code, 403)
		# Reading the map needs no scope at all
		self.assertEqual(self.call("get", "/api/0.6/changeset/" + created.content.decode(), editor).status_code, 200)

		writer = self.token("write_prefs")
		self.assertEqual(self.call("put", "/api/0.6/user/preferences/colour", writer, "blue", "text/plain").status_code, 200)
		self.assertEqual(self.call("get", "/api/0.6/user/preferences", writer).status_code, 403)

	def test_the_application_and_authorization_decide_now(self):
		token = self.token("read_prefs", "write_api")
		self.assertEqual(self.create_changeset(token).status_code, 200)
		# Taking the permission from the application takes it from tokens already issued
		self.app.permission_write_api = False
		self.app.save()
		self.assertEqual(self.create_changeset(token).status_code, 403)
		self.assertEqual(self.call("get", "/api/0.6/user/details", token).status_code, 200)
		# as does disabling the authorization, for everything
		Oauth2Authorization.objects.filter(user=self.user).update(disabled=True)
		self.assertIn(self.call("get", "/api/0.6/user/details", token).status_code, (401, 403))

	def test_permissions_lists_the_granted_scopes(self):
		token = self.token("write_api", "read_prefs")
		response = self.call("get", "/api/0.6/permissions.json", token)
		self.assertEqual(json.loads(response.content)["permissions"], ["allow_read_prefs", "allow_write_api"])
		# Other credentials have them all, named as on openstreetmap.org
		client = Client()
		client.login(username="mapper", password="glass onion")
		self.assertEqual(json.loads(client.get("/api/0.6/permissions.json").content)["permissions"],
			["allow_" + scope for scope in SCOPES])

	def test_other_credentials_are_not_limited(self):
		client = Client()
		client.login(username="mapper", password="glass onion")
		response = client.put("/api/0.6/changeset/create",
			"<osm><changeset><tag k='created_by' v='tests'/></changeset></osm>", content_type="text/xml")
		self.assertEqual(response.status_code, 200, response.content)
		self.assertEqual(client.get("/api/0.6/user/details").status_code, 200)

class ConsentTestCase(TestCase):
	"""The authorize page asks the user, and gives the application what they agreed to."""

	def setUp(self):
		cache.clear()
		self.user = User.objects.create_user("mapper", "mapper@example.com", "glass onion")
		permissions = {"permission_" + scope: scope != "write_redactions" for scope in SCOPES}
		self.app = Oauth2Application.objects.create(name="Fancy Editor", user=self.user, client_id="editor",
			client_secret="", redirect_uris="urn:ietf:wg:oauth:2.0:oob https://example.com/cb?from=osm",
			confidential=False, disabled=False, **permissions)
		self.app.set_client_secret("shh")
		self.app.save()
		self.client = Client()
		self.client.login(username="mapper", password="glass onion")
		self.params = {"client_id": "editor", "redirect_uri": "https://example.com/cb?from=osm",
			"response_type": "code", "scope": "write_api read_prefs", "state": "xyz"}

	def query(self, response):
		self.assertEqual(response.status_code, 302)
		location = urlsplit(response["Location"])
		self.assertEqual((location.scheme, location.netloc, location.path), ("https", "example.com", "/cb"))
		return {key: values[0] for key, values in parse_qs(location.query).items()}

	def test_the_user_is_asked_first(self):
		response = self.client.get(reverse("oauth2:authorize"), self.params)
		self.assertEqual(response.status_code, 200)
		page = response.content.decode("utf-8")
		for text in ("Fancy Editor", "Change the map", "Read your user preferences", "https://example.com/cb",
				'name="state" value="xyz"', 'name="scope" value="read_prefs write_api"'):
			self.assertIn(text, page)
		self.assertNotIn("Change your user preferences", page)
		self.assertEqual(Oauth2AuthorizationCode.objects.count(), 0)
		# Without a scope, what every application used to be given
		page = self.client.get(reverse("oauth2:authorize"), {"client_id": "editor"}).content.decode("utf-8")
		self.assertIn('name="scope" value="read_prefs write_api"', page)
		# Someone not signed in is asked to sign in, not sent to the application
		anonymous = Client().get(reverse("oauth2:authorize"), self.params)
		self.assertEqual(anonymous.status_code, 302)
		self.assertTrue(urlsplit(anonymous["Location"]).path.startswith("/accounts/login"), anonymous["Location"])
		self.assertEqual(urlsplit(anonymous["Location"]).netloc, "")

	def test_allowing_sends_a_code_for_what_was_agreed(self):
		params = dict(self.params, scope="write_api")
		answer = self.query(self.client.post(reverse("oauth2:authorize"), dict(params, decision="approve")))
		self.assertEqual((answer["from"], answer["state"]), ("osm", "xyz"))
		token = Client().post(reverse("oauth2:token"), {"client_id": "editor", "client_secret": "shh",
			"code": answer["code"], "redirect_uri": "https://example.com/cb?from=osm"})
		self.assertEqual(token.status_code, 200, token.content)
		self.assertEqual(json.loads(token.content)["scope"], "write_api")
		authorization = Oauth2Authorization.objects.get()
		self.assertEqual([scope for scope in SCOPES if getattr(authorization, "permission_" + scope)], ["write_api"])

	def test_denying_says_so(self):
		answer = self.query(self.client.post(reverse("oauth2:authorize"), dict(self.params, decision="deny")))
		self.assertEqual((answer["error"], answer["state"]), ("access_denied", "xyz"))
		self.assertNotIn("code", answer)
		self.assertEqual(Oauth2AuthorizationCode.objects.count(), 0)

	def test_errors_go_back_to_the_application(self):
		for changes, error in (({"scope": "write_redactions"}, "invalid_scope"), ({"scope": "fly"}, "invalid_scope"),
				({"response_type": "token"}, "unsupported_response_type")):
			for method in ("get", "post"):
				with self.subTest(changes=changes, method=method):
					answer = self.query(getattr(self.client, method)(reverse("oauth2:authorize"),
						dict(self.params, decision="approve", **changes)))
					self.assertEqual((answer["error"], answer["state"]), (error, "xyz"))
		self.assertEqual(Oauth2AuthorizationCode.objects.count(), 0)
		# Shown here for the out of band URI
		response = self.client.get(reverse("oauth2:authorize"), {"client_id": "editor", "scope": "fly"})
		self.assertEqual(response.status_code, 400)
		self.assertIn(b"invalid_scope", response.content)

	def test_unregistered_clients_and_uris_are_never_redirected_to(self):
		for changes in ({"redirect_uri": "https://evil.example/cb", "scope": "fly"}, {"client_id": "nobody"}):
			response = self.client.get(reverse("oauth2:authorize"), dict(self.params, **changes))
			self.assertIn(response.status_code, (400, 404))
		self.app.disabled = True
		self.app.save()
		self.assertEqual(self.client.get(reverse("oauth2:authorize"), self.params).status_code, 404)

	def test_out_of_band_shows_the_code(self):
		code = approve(self.client, {"client_id": "editor", "scope": "read_prefs"})
		token = Client().post(reverse("oauth2:token"), {"client_id": "editor", "client_secret": "shh", "code": code})
		self.assertEqual(json.loads(token.content)["scope"], "read_prefs")

class RegistrationAndRevocationTestCase(TestCase):
	"""Applications are registered with the permissions they may ask for, and
	users see and revoke those they have authorised."""

	def setUp(self):
		cache.clear()
		self.owner = User.objects.create_user("owner", "owner@example.com", "glass onion")
		self.mapper = User.objects.create_user("mapper", "mapper@example.com", "glass onion")
		self.client = Client()
		self.client.login(username="mapper", password="glass onion")

	def register(self, **fields):
		client = Client()
		client.login(username="owner", password="glass onion")
		return client.post(reverse("oauth2:applications"), dict({"name": "Fancy Editor",
			"redirects": "urn:ietf:wg:oauth:2.0:oob"}, **fields))

	def test_registering_chooses_permissions(self):
		page = self.register().content.decode("utf-8")
		for scope in SCOPES:
			self.assertIn('name="permission_{}"'.format(scope), page)
		# At least one is needed, and a name, and what was given is kept
		self.assertIn("Choose at least one permission", page)
		page = self.register(name="", permission_write_api="1").content.decode("utf-8")
		self.assertIn("Give the application a name", page)
		self.assertIn('name="permission_write_api" value="1" checked', page)
		self.assertEqual(Oauth2Application.objects.count(), 0)

		self.register(permission_write_api="1", permission_read_prefs="1")
		app = Oauth2Application.objects.get()
		self.assertEqual([scope for scope in SCOPES if getattr(app, "permission_" + scope)], ["read_prefs", "write_api"])
		# Its users can be asked for those, and no others
		self.assertEqual(self.client.get(reverse("oauth2:authorize"), {"client_id": app.client_id,
			"scope": "read_prefs"}).status_code, 200)
		self.assertEqual(self.client.get(reverse("oauth2:authorize"), {"client_id": app.client_id,
			"scope": "write_prefs"}).status_code, 400)
		# Its page says what it may ask for
		owner = Client()
		owner.login(username="owner", password="glass onion")
		page = owner.get(reverse("oauth2:applications_detail", args=[app.client_id])).content.decode("utf-8")
		self.assertIn("Change the map", page)
		self.assertNotIn("Send messages", page)

	def test_users_see_and_revoke_what_they_authorised(self):
		self.register(permission_write_api="1", permission_read_prefs="1")
		app = Oauth2Application.objects.get()
		app.set_client_secret("shh")
		app.save()
		other = Oauth2Application.objects.create(name="Other", user=self.owner, client_id="other", client_secret="",
			redirect_uris="urn:ietf:wg:oauth:2.0:oob", confidential=False, disabled=False,
			**{"permission_" + scope: True for scope in SCOPES})
		self.assertContains(self.client.get(reverse("oauth2:authorized_applications")), "not authorised any")

		code = approve(self.client, {"client_id": app.client_id, "scope": "read_prefs"})
		token = json.loads(Client().post(reverse("oauth2:token"), {"client_id": app.client_id,
			"client_secret": "shh", "code": code}).content)["access_token"]
		create_authorization(self.mapper, other, ["write_api"])
		create_authorization(self.owner, other, ["write_prefs"]) # Someone else's
		pending = approve(self.client, {"client_id": app.client_id, "scope": "write_api"})

		page = self.client.get(reverse("oauth2:authorized_applications")).content.decode("utf-8")
		for text in ("Fancy Editor", "Other", "Read your user preferences", "Change the map", "owner"):
			self.assertIn(text, page)
		self.assertNotIn("Change your user preferences", page)
		details = lambda: Client().get("/api/0.6/user/details", secure=True, HTTP_AUTHORIZATION="Bearer " + token)
		self.assertEqual(details().status_code, 200)

		# Revoked: its token stops working, and a code not yet exchanged cannot be
		response = self.client.post(reverse("oauth2:authorized_applications"), {"client_id": app.client_id})
		self.assertEqual(response.status_code, 302)
		self.assertIn(details().status_code, (401, 403))
		self.assertEqual(Client().post(reverse("oauth2:token"), {"client_id": app.client_id, "client_secret": "shh",
			"code": pending}).status_code, 400)
		page = self.client.get(reverse("oauth2:authorized_applications")).content.decode("utf-8")
		self.assertNotIn("Fancy Editor", page)
		self.assertIn("Other", page)
		# Only one's own are touched
		self.assertFalse(Oauth2Authorization.objects.get(user=self.owner).disabled)
		self.assertEqual(Client().get(reverse("oauth2:authorized_applications")).status_code, 302)

JOSM_REDIRECT = "http://127.0.0.1:8111/oauth_authorization"
JOSM_SCOPE = "read_gpx write_gpx read_prefs write_prefs write_api write_notes"

class JosmTestCase(TestCase):
	"""JOSM's fully automatic authorisation of a server it has no client ID for,
	as its source makes the requests: no client ID, PKCE in place of a secret,
	and endpoints next to the API when discovery fails."""

	def setUp(self):
		cache.clear()
		self.user = User.objects.create_user("mapper", "mapper@example.com", "glass onion")
		self.app = Oauth2Application.objects.create(name="JOSM", user=self.user, client_id="josm", client_secret="",
			redirect_uris=JOSM_REDIRECT, confidential=False, disabled=False, is_default=True,
			**{"permission_" + scope: scope in JOSM_SCOPE.split() for scope in SCOPES})
		self.app.set_client_secret("unused")
		self.app.save()
		self.client = Client()
		self.client.login(username="mapper", password="glass onion")
		# As JOSM makes it: a UUID, shorter than RFC 7636 asks for
		self.verifier = "7b8c2f0e-3a54-4e44-9a8e-2b1f00c5d6e7"
		self.challenge = base64.urlsafe_b64encode(hashlib.sha256(self.verifier.encode()).digest()).decode().rstrip("=")

	def authorize(self, **changes):
		params = dict({"response_type": "code", "client_id": "", "redirect_uri": JOSM_REDIRECT, "scope": JOSM_SCOPE,
			"state": "77e93817-33ce-4179-88b4-830d64a15866", "code_challenge_method": "S256",
			"code_challenge": self.challenge}, **changes)
		page = self.client.get("/api/authorize", params)
		self.assertEqual(page.status_code, 200, page.content)
		answer = self.client.post("/api/authorize", dict(params, decision="approve"))
		self.assertEqual(answer.status_code, 302, answer.content)
		self.assertTrue(answer["Location"].startswith(JOSM_REDIRECT + "?"))
		query = parse_qs(urlsplit(answer["Location"]).query)
		self.assertEqual(query["state"], ["77e93817-33ce-4179-88b4-830d64a15866"])
		return query["code"][0]

	def token(self, code, verifier=None, secret=None):
		# As JOSM builds it: concatenated, with only the secret encoded
		body = "grant_type=authorization_code&client_id=" + ("&client_secret=" + secret if secret else "") + \
			"&redirect_uri=" + JOSM_REDIRECT + "&code=" + code + ("&code_verifier=" + verifier if verifier else "")
		return Client().post("/api/token", body, content_type="application/x-www-form-urlencoded")

	def test_fully_automatic(self):
		code = self.authorize()
		response = self.token(code, self.verifier)
		self.assertEqual(response.status_code, 200, response.content)
		token = json.loads(response.content)
		self.assertEqual((token["token_type"], token["scope"]), ("Bearer", "read_prefs write_prefs write_api read_gpx write_gpx write_notes"))
		bearer = {"HTTP_AUTHORIZATION": "Bearer " + token["access_token"], "secure": True}
		self.assertEqual(Client().get("/api/0.6/user/details", **bearer).status_code, 200)
		self.assertIn("allow_write_api", json.loads(Client().get("/api/0.6/permissions.json", **bearer).content)["permissions"])

	def test_the_verifier_is_needed(self):
		for verifier in (None, "wrong-" + self.verifier):
			with self.subTest(verifier=verifier):
				self.assertEqual(self.token(self.authorize(), verifier).status_code, 400)
		# A code asked for without PKCE still needs the secret
		code = self.authorize(code_challenge="", code_challenge_method="")
		self.assertEqual(self.token(code).status_code, 400)
		self.assertEqual(self.token(code, secret="unused").status_code, 200)
		# Only S256
		answer = self.client.get("/api/authorize", {"client_id": "", "redirect_uri": JOSM_REDIRECT, "state": "s",
			"scope": "write_api", "code_challenge_method": "plain", "code_challenge": self.verifier})
		self.assertEqual(parse_qs(urlsplit(answer["Location"]).query)["error"], ["invalid_request"])

	def test_the_default_application(self):
		ask = lambda: self.client.get("/api/authorize", {"client_id": "", "redirect_uri": JOSM_REDIRECT}).status_code
		# Only one is the default: making another one so takes it from the first
		other = Oauth2Application.objects.create(name="Other", user=self.user, client_id="other", client_secret="",
			redirect_uris="urn:ietf:wg:oauth:2.0:oob", confidential=False, disabled=False, is_default=True,
			**{"permission_" + scope: True for scope in SCOPES})
		self.app.refresh_from_db()
		self.assertFalse(self.app.is_default)
		self.assertEqual(ask(), 400) # The other one, whose redirect URIs JOSM's is not among
		self.app.is_default = True
		self.app.save()
		self.assertEqual(Oauth2Application.objects.filter(is_default=True).get(), self.app)
		self.assertEqual(ask(), 200)
		# A disabled default is none, as is none at all
		self.app.disabled = True
		self.app.save()
		self.assertEqual(ask(), 404)
		Oauth2Application.objects.update(is_default=False, disabled=False)
		self.assertEqual(ask(), 404)
		# The database allows no more than one
		from django.db import IntegrityError, transaction
		with self.assertRaises(IntegrityError), transaction.atomic():
			Oauth2Application.objects.update(is_default=True)

	def test_discovery(self):
		with self.settings(PUBLIC_URL="https://map.example.org/"):
			metadata = json.loads(Client().get("/.well-known/oauth-authorization-server").content)
		self.assertEqual(metadata["authorization_endpoint"], "https://map.example.org/oauth2/authorize")
		self.assertEqual(metadata["token_endpoint"], "https://map.example.org/oauth2/token")
		self.assertEqual(metadata["code_challenge_methods_supported"], ["S256"])
		self.assertIn("write_api", metadata["scopes_supported"])

class TokenLifetimeAndRoutesTestCase(TestCase):

	def setUp(self):
		cache.clear()
		self.user = User.objects.create_user("mapper", "mapper@example.com", "glass onion")
		self.app = Oauth2Application.objects.create(name="Editor", user=self.user, client_id="editor", client_secret="",
			redirect_uris="urn:ietf:wg:oauth:2.0:oob", confidential=False, disabled=False,
			**{"permission_" + scope: True for scope in SCOPES})
		self.app.set_client_secret("shh")
		self.app.save()
		self.client.login(username="mapper", password="glass onion")

	def exchange(self):
		code = approve(self.client, {"client_id": "editor"})
		return json.loads(Client().post(reverse("oauth2:token"), {"client_id": "editor", "client_secret": "shh",
			"code": code}).content)

	def authorization(self, token):
		from .secrets import hash_secret
		return Oauth2Authorization.objects.get(token_hash=hash_secret(token))

	def test_tokens_last_until_revoked_by_default(self):
		from django.conf import settings
		from .views import oauth2_access_token_seconds
		# Without the setting, tokens do not expire
		with self.settings():
			del settings.OAUTH2_ACCESS_TOKEN_SECONDS
			self.assertEqual(oauth2_access_token_seconds(), 0)
		with self.settings(OAUTH2_ACCESS_TOKEN_SECONDS=0):
			answer = self.exchange()
		self.assertIsNone(self.authorization(answer["access_token"]).expires_at)
		self.assertNotIn("expires_in", answer)
		with self.settings(OAUTH2_ACCESS_TOKEN_SECONDS=600):
			answer = self.exchange()
		self.assertEqual(answer["expires_in"], 600)
		left = self.authorization(answer["access_token"]).expires_at - timezone.now()
		self.assertTrue(590 < left.total_seconds() <= 600, left)
		# and once expired it is refused
		details = lambda: Client().get("/api/0.6/user/details", secure=True,
			HTTP_AUTHORIZATION="Bearer " + answer["access_token"]).status_code
		self.assertEqual(details(), 200)
		Oauth2Authorization.objects.filter(token_hash=self.authorization(answer["access_token"]).token_hash).update(
			expires_at=timezone.now() - datetime.timedelta(seconds=1))
		self.assertIn(details(), (401, 403))

	def test_only_hashes_are_kept(self):
		from .secrets import hash_secret
		answer = self.exchange()
		token = answer["access_token"]
		self.assertGreaterEqual(len(token), 43)
		auth = self.authorization(token)
		self.assertEqual(auth.token_hash, hash_secret(token))
		self.assertNotIn(token, str(Oauth2Authorization.objects.values().get(id=auth.id)))
		code = approve(self.client, {"client_id": "editor"})
		record = Oauth2AuthorizationCode.objects.get(consumed_at=None)
		self.assertEqual(record.code_hash, hash_secret(code))
		# A made-up token or code is no use
		self.assertIn(Client().get("/api/0.6/user/details", secure=True, HTTP_AUTHORIZATION="Bearer " + token + "x"
			).status_code, (401, 403))
		self.assertEqual(Client().post(reverse("oauth2:token"), {"client_id": "editor", "client_secret": "shh",
			"code": code + "x"}).status_code, 400)

	def test_routes_are_exact(self):
		from django.urls import resolve, Resolver404
		from . import views
		for path, view in (("/oauth2/authorize", views.authorize), ("/oauth2/token", views.token),
				("/oauth2/applications", views.applications), ("/oauth2/authorized_applications", views.authorized_applications),
				("/oauth2/application/abc", views.application_detail)):
			self.assertIs(resolve(path).func, view, path)
		for path in ("/oauth2/authorize/", "/oauth2/authorizeX", "/oauth2/tokens", "/oauth2/applicationsX",
				"/xoauth2/token", "/oauth2/application/abc/x"):
			with self.subTest(path=path):
				with self.assertRaises(Resolver404):
					found = resolve(path)
					# Anything else these may reach is not one of OAuth2's views
					if found.func.__module__.startswith("oauth2"):
						raise AssertionError("{} reached {}".format(path, found.func.__name__))
					raise Resolver404()

