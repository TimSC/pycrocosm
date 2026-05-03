from django.db import models

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

	jti = models.CharField(max_length=64, unique=True)
	parent_app = models.ForeignKey(Oauth2Application, on_delete=models.CASCADE)
	user = models.ForeignKey(User, on_delete=models.CASCADE)
	redirect_uri = models.TextField()

	class Meta:
		indexes = [
			models.Index(fields=['jti'], name='oauth2_code_jti_idx'),
			models.Index(fields=['parent_app'], name='oauth2_code_parent_app_idx'),
			models.Index(fields=['user'], name='oauth2_code_user_idx'),
		]
