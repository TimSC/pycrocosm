from django.db import models

from django.contrib.auth.models import User

# Create your models here.
class Oauth2Application(models.Model):
	id = models.BigAutoField(primary_key=True)

	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	name = models.CharField(max_length=255)

	user = models.ForeignKey(User, on_delete=models.CASCADE)
	client_id = models.CharField(max_length=255)
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

