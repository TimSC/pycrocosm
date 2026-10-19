from django.db import models, transaction

from django.contrib.auth.models import User
from django.contrib.auth.hashers import check_password, identify_hasher, make_password
from django.utils.crypto import constant_time_compare

# Create your models here.
class Oauth2Application(models.Model):
	id = models.BigAutoField(primary_key=True)

	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	name = models.CharField(max_length=255)

	user = models.ForeignKey(User, on_delete=models.CASCADE)
	client_id = models.CharField(max_length=255, unique=True)
	client_secret = models.CharField(max_length=255)
	redirect_uris = models.TextField()

	confidential = models.BooleanField()

	permission_read_prefs = models.BooleanField()
	permission_write_prefs = models.BooleanField()
	permission_write_diary = models.BooleanField()
	permission_write_api = models.BooleanField()
	permission_read_gpx = models.BooleanField()
	permission_write_gpx = models.BooleanField()
	permission_write_notes = models.BooleanField()
	permission_write_redactions = models.BooleanField()
	permission_consume_messages = models.BooleanField()
	permission_send_messages = models.BooleanField()
	permission_openid = models.BooleanField()

	disabled = models.BooleanField()

	# The application used for an OAuth2 request that names none, as JOSM's do
	# for a server it has no client ID for. At most one is: saving one as the
	# default makes it the only one.
	is_default = models.BooleanField(default=False,
		help_text="Use this application for OAuth2 requests that give no client ID, as JOSM's do "
			"for a server it has no client ID for. Only one application can be the default.")

	def save(self, *args, **kwargs):
		with transaction.atomic():
			if self.is_default:
				Oauth2Application.objects.filter(is_default=True).exclude(pk=self.pk).update(is_default=False)
			super().save(*args, **kwargs)

	def set_client_secret(self, raw_secret):
		self.client_secret = make_password(raw_secret)

	def check_client_secret(self, raw_secret):
		try:
			identify_hasher(self.client_secret)
			return check_password(raw_secret, self.client_secret)
		except ValueError:
			return constant_time_compare(raw_secret, self.client_secret)

	class Meta:
		indexes = [
			models.Index(fields=['user'], name='oauth2_app_client_id_idx'),
			models.Index(fields=['user'], name='oauth2_app_user_idx'),
		]
		constraints = [
			models.UniqueConstraint(fields=['is_default'], condition=models.Q(is_default=True),
				name='oauth2_app_one_default'),
		]

class Oauth2Authorization(models.Model):
	id = models.BigAutoField(primary_key=True)

	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	parent_app = models.ForeignKey(Oauth2Application, on_delete=models.CASCADE)
	user = models.ForeignKey(User, on_delete=models.CASCADE)
	
	permission_read_prefs = models.BooleanField()
	permission_write_prefs = models.BooleanField()
	permission_write_diary = models.BooleanField()
	permission_write_api = models.BooleanField()
	permission_read_gpx = models.BooleanField()
	permission_write_gpx = models.BooleanField()
	permission_write_notes = models.BooleanField()
	permission_write_redactions = models.BooleanField()
	permission_consume_messages = models.BooleanField()
	permission_send_messages = models.BooleanField()
	permission_openid = models.BooleanField()

	disabled = models.BooleanField()

	# The SHA-256 (in hex) of the authorization's access token, which is itself
	# kept nowhere, and when it stops working (None for when revoked)
	token_hash = models.CharField(max_length=64, unique=True, null=True, blank=True)
	expires_at = models.DateTimeField(null=True, blank=True)

	class Meta:
		indexes = [
			models.Index(fields=['parent_app'], name='oauth2_auth_parent_app_idx'),
			models.Index(fields=['user'], name='oauth2_auth_user_idx'),
		]

class Oauth2AuthorizationCode(models.Model):
	id = models.BigAutoField(primary_key=True)

	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)
	expires_at = models.DateTimeField()
	consumed_at = models.DateTimeField(null=True, blank=True)

	code_hash = models.CharField(max_length=64, unique=True) # The SHA-256 (in hex) of the code given out
	parent_app = models.ForeignKey(Oauth2Application, on_delete=models.CASCADE)
	user = models.ForeignKey(User, on_delete=models.CASCADE)
	redirect_uri = models.TextField()
	scope = models.TextField(default="") # The scopes the user agreed to, separated by spaces
	# PKCE (RFC 7636): the challenge the application gave when asking, which
	# the verifier it gives for the token must match; empty without
	code_challenge = models.CharField(max_length=128, default="", blank=True)
	code_challenge_method = models.CharField(max_length=8, default="", blank=True)

	class Meta:
		indexes = [
			models.Index(fields=['parent_app'], name='oauth2_code_parent_app_idx'),
			models.Index(fields=['user'], name='oauth2_code_user_idx'),
		]
