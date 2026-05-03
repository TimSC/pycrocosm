import jwt
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from django.conf import settings
from .models import Oauth2Application, Oauth2Authorization

def oauth2_signing_secret():
	return getattr(settings, 'OAUTH2_JWT_SECRET', settings.SECRET_KEY)

class TokenAuthentication(BaseAuthentication):

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

		try:
			decoded = jwt.decode(authHeaderSp[1], oauth2_signing_secret(), algorithms=["HS256"])
		except jwt.exceptions.PyJWTError as err:
			raise AuthenticationFailed()

		if decoded['type'] != 'token':
			raise AuthenticationFailed()

		authRecord = Oauth2Authorization.objects.filter(id=decoded['auth_id']).first()
		if authRecord is None:
			raise AuthenticationFailed()
		if authRecord.disabled:
			raise AuthenticationFailed()

		appRecord = authRecord.parent_app
		if appRecord is None:
			raise AuthenticationFailed()
		if appRecord.disabled:
			raise AuthenticationFailed()			

		scope = ['read_prefs', 'write_api']
		return (authRecord.user, scope)
