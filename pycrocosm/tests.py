from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client, TestCase, override_settings


class LoginRateLimitTestCase(TestCase):
	def setUp(self):
		cache.clear()
		self.password = "glass onion"
		self.user = User.objects.create_user("john", "john@example.com", self.password)

	@override_settings(LOGIN_RATE_LIMIT_REQUESTS=1, LOGIN_RATE_LIMIT_WINDOW_SECONDS=60)
	def test_login_post_is_rate_limited(self):
		client = Client()

		first = client.post("/accounts/login/", {
			"username": self.user.username,
			"password": "wrong password",
		})
		second = client.post("/accounts/login/", {
			"username": self.user.username,
			"password": "wrong password",
		})

		self.assertNotEqual(first.status_code, 429)
		self.assertEqual(second.status_code, 429)
