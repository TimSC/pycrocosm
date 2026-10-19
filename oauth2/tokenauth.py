from django.conf import settings
from django.utils import timezone
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from .models import Oauth2Authorization
from .scopes import granted_scopes
from .secrets import hash_secret

class TokenAuthentication(BaseAuthentication):
	"""An OAuth2 bearer token: a random string whose hash an authorization
	keeps (see secrets.py). It is refused once the authorization or its
	application is disabled, or it has expired."""

	keyword = 'Authorization'

	def authenticate(self, request):

		# Only allow oauth2 on secure connections (or if debug is enabled)
		if not request.is_secure() and not settings.DEBUG:
			return None

		authHeader = request.headers.get(self.keyword)
		if authHeader is None:
			return None

		authHeaderSp = authHeader.split(" ", 1)
		if len(authHeaderSp) == 1:
			return None
		if authHeaderSp[0] != 'Bearer':
			return None

		authRecord = Oauth2Authorization.objects.select_related("parent_app", "user").filter(
			token_hash=hash_secret(authHeaderSp[1].strip())).first()
		if authRecord is None or authRecord.disabled or authRecord.parent_app.disabled:
			raise AuthenticationFailed()
		if authRecord.expires_at is not None and authRecord.expires_at <= timezone.now():
			raise AuthenticationFailed()

		# What the authorization and its application allow now
		return (authRecord.user, granted_scopes(authRecord))
