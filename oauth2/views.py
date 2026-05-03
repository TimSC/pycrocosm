from django.shortcuts import render
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseNotFound, HttpResponseServerError, HttpResponseForbidden, JsonResponse
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, IsAuthenticatedOrReadOnly
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
from django.utils.crypto import get_random_string
from django.utils import timezone
import jwt
import datetime
import time
import uuid

from .models import Oauth2Application, Oauth2Authorization, Oauth2AuthorizationCode

def oauth2_signing_secret():
	return getattr(settings, 'OAUTH2_JWT_SECRET', settings.SECRET_KEY)

def oauth2_auth_code_seconds():
	return getattr(settings, 'OAUTH2_AUTH_CODE_SECONDS', 300)

def oauth2_access_token_seconds():
	return getattr(settings, 'OAUTH2_ACCESS_TOKEN_SECONDS', 3600)

def get_redirect_uris(app):
	return [uri for uri in app.redirect_uris.split() if len(uri) > 0]

def get_request_redirect_uri(request, app):
	redirect_uri = request.GET.get('redirect_uri') or request.POST.get('redirect_uri') or "urn:ietf:wg:oauth:2.0:oob"
	if redirect_uri not in get_redirect_uris(app):
		return None
	return redirect_uri

# Create your views here.
@login_required
def authorize(request):

	userRecord = request.user
	app = Oauth2Application.objects.filter(client_id=request.GET.get('client_id')).first()
	if app is None:
		return HttpResponseNotFound("No such application")

	redirect_uri = get_request_redirect_uri(request, app)
	if redirect_uri is None:
		return HttpResponseBadRequest("redirect_uri incorrect")

	now = timezone.now()
	expires_at = now + datetime.timedelta(seconds=oauth2_auth_code_seconds())
	jti = uuid.uuid4().hex
	Oauth2AuthorizationCode.objects.create(
		jti=jti,
		parent_app=app,
		user=request.user,
		redirect_uri=redirect_uri,
		expires_at=expires_at)

	payload = jwt.encode({"type": "app", 
		"jti": jti,
		"user_id": request.user.id, 
		"client_id": app.client_id, 
		"redirect_uri": redirect_uri,
		"iat": now,
		"exp": expires_at,
		'created_at': now.isoformat()}, 
		oauth2_signing_secret(), algorithm="HS256")

	return HttpResponse(payload, content_type='text/plain')
	#return render(request, 'frontpage/index.html', {'db_status': dbStatus})

def create_authorization(user, app):

	scope = 'read_prefs write_api'
	now = timezone.now()
	expires_at = now + datetime.timedelta(seconds=oauth2_access_token_seconds())

	auth = Oauth2Authorization.objects.create(parent_app = app,
		user = user,
		permission_read_prefs = True,
		permission_write_prefs = False,
		permission_write_diary = False,
		permission_write_api = True,
		permission_read_gpx = False,
		permission_write_gpx = False,
		permission_write_notes = False,
		permission_write_redactions = False,
		permission_consume_messages = False,
		permission_send_messages = False,
		permission_openid = False,
		disabled = False)

	token = jwt.encode({"type": "token", 
		"auth_id": auth.id, 
		"user_id": user.id, 
		"client_id": app.client_id, 
		'scope': scope,
		"iat": now,
		"exp": expires_at,
		'created_at': now.isoformat()}, 
		oauth2_signing_secret(), algorithm="HS256")

	return token, scope

@csrf_exempt
@api_view(['POST'])
def token(request):
	
	userRecord = request.user
	app = Oauth2Application.objects.filter(client_id=request.POST.get('client_id')).first()
	if app is None:
		return HttpResponseNotFound("No such application")

	redirect_uri = get_request_redirect_uri(request, app)
	if redirect_uri is None:
		return HttpResponseBadRequest("redirect_uri incorrect")

	try:
		decoded = jwt.decode(request.POST.get('code'), oauth2_signing_secret(), algorithms=["HS256"])
	except jwt.exceptions.PyJWTError as err:
		return HttpResponseBadRequest("Code incorrect")

	if decoded['type'] != "app":
		return HttpResponseBadRequest("Code incorrect")	
	if decoded['client_id'] != app.client_id:
		return HttpResponseBadRequest("Code does not match client_id")
	if decoded.get('redirect_uri') != redirect_uri:
		return HttpResponseBadRequest("redirect_uri incorrect")

	codeRecord = Oauth2AuthorizationCode.objects.filter(jti=decoded.get('jti'), parent_app=app).first()
	if codeRecord is None:
		return HttpResponseBadRequest("Code incorrect")
	if codeRecord.consumed_at is not None:
		return HttpResponseBadRequest("Code already used")
	if codeRecord.expires_at <= timezone.now():
		return HttpResponseBadRequest("Code expired")
	if codeRecord.redirect_uri != redirect_uri:
		return HttpResponseBadRequest("redirect_uri incorrect")

	if not app.check_client_secret(request.POST.get('client_secret', '')):
		return HttpResponseBadRequest("client_secret incorrect")

	user = User.objects.filter(id=decoded['user_id']).first()
	if user is None:
		return HttpResponseNotFound("No such user")

	codeRecord.consumed_at = timezone.now()
	codeRecord.save()

	token, scope = create_authorization(user, app)

	out = {'access_token': token, 
		'token_type': 'Bearer', 
		'scope': scope,
		'created_at': int(time.time())}

	return JsonResponse(out)

@login_required
def applications(request):
	new_client_secret = None

	if request.method == "POST":
		new_client_secret = get_random_string(48)
		
		app = Oauth2Application.objects.create(

			name = request.POST.get("name"),

			user = request.user,
			client_id = get_random_string(32),
			client_secret = "",
			redirect_uris = request.POST.get("redirects", "urn:ietf:wg:oauth:2.0:oob"),

			confidential = False,

			permission_read_prefs = True,
			permission_write_prefs = True,
			permission_write_diary = True,
			permission_write_api = True,
			permission_read_gpx = True,
			permission_write_gpx = True,
			permission_write_notes = True,
			permission_write_redactions = True,
			permission_consume_messages = True,
			permission_send_messages = True,
			permission_openid = True,

			disabled = False)
		app.set_client_secret(new_client_secret)
		app.save()

	apps = Oauth2Application.objects.filter(user=request.user).all()

	return render(request, 'oauth2/applications.html', {'apps': apps, 'new_client_secret': new_client_secret})

@login_required
def application_detail(request, client_id):

	app = Oauth2Application.objects.filter(client_id=client_id, user=request.user).first()
	if app is None:
		return HttpResponseNotFound("No such application")
	
	token = None
	if request.method == "POST":
		action = request.POST.get("action")
		if action == "Create Authorization":
			token, scope = create_authorization(request.user, app)

	auths = Oauth2Authorization.objects.filter(parent_app=app).all()
	
	return render(request, 'oauth2/application_detail.html', 
		{'app': app, 'auths': auths, 'token': token})
