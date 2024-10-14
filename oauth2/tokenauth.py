import jwt
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from django.conf import settings
from .models import Oauth2Application, Oauth2Authorization

class TokenAuthentication(BaseAuthentication):

	keyword = 'Authorization'

	def authenticate(self, request):
		
		authHeader = request.headers.get(self.keyword)
		if authHeader is None:
			return None

		authHeaderSp = authHeader.split(" ", 1)
		if len(authHeaderSp) == 1:
			return None
		if authHeaderSp[0] != 'Bearer':
			return None

		try:
			decoded = jwt.decode(authHeaderSp[1], settings.SECRET_KEY, algorithms=["HS256"])
		except jwt.exceptions.PyJWTError as err:
			raise AuthenticationFailed()
		
		if decoded['type'] != 'token':
			raise AuthenticationFailed()

		authRecord = Oauth2Authorization.objects.filter(id=decoded['auth_id']).first()
		if authRecord is None:
			raise AuthenticationFailed()

		appRecord = authRecord.parent_app
		if appRecord is None:
			raise AuthenticationFailed()

		scope = ['read_prefs', 'write_api']
		return (authRecord.user, scope)

	def authenticate_header(self, request):
		return self.keyword

