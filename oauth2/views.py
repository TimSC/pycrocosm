from django.shortcuts import render
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseNotFound, HttpResponseServerError, HttpResponseForbidden, JsonResponse
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, IsAuthenticatedOrReadOnly
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from django.contrib.auth.models import User
from django.utils.crypto import get_random_string
import jwt
import datetime
import time

from .models import Oauth2Application, Oauth2Authorization

# Create your views here.
@permission_classes((IsAuthenticated, ))
def authorize(request):

	userRecord = request.user
	app = Oauth2Application.objects.filter(client_id=request.GET.get('client_id')).first()
	if app is None:
		return HttpResponseNotFound("No such application")

	payload = jwt.encode({"type": "app", "user_id": request.user.id, "client_id": app.client_id, 'created_at': datetime.datetime.now().isoformat()}, 
		settings.SECRET_KEY, algorithm="HS256")

	return HttpResponse(payload, content_type='text/plain')
	#return render(request, 'frontpage/index.html', {'db_status': dbStatus})

def create_authorization(user, app):

	scope = 'read_prefs write_api'

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
		'created_at': datetime.datetime.now().isoformat()}, 
		settings.SECRET_KEY, algorithm="HS256")

	return token

@csrf_exempt
@api_view(['POST'])
def token(request):
	
	userRecord = request.user
	app = Oauth2Application.objects.filter(client_id=request.POST.get('client_id')).first()
	if app is None:
		return HttpResponseNotFound("No such application")

	try:
		decoded = jwt.decode(request.POST.get('code'), settings.SECRET_KEY, algorithms=["HS256"])
	except jwt.exceptions.PyJWTError as err:
		return HttpResponseBadRequest("Code incorrect")

	if decoded['type'] != "app":
		return HttpResponseBadRequest("Code incorrect")	
	if decoded['client_id'] != app.client_id:
		return HttpResponseBadRequest("Code does not match client_id")

	if request.POST.get('client_secret') != app.client_secret:
		return HttpResponseBadRequest("client_secret incorrect")

	user = User.objects.filter(id=decoded['user_id']).first()
	if user is None:
		return HttpResponseNotFound("No such user")

	token = create_authorization(user, app)

	out = {'access_token': token, 
		'token_type': 'Bearer', 
		'scope': scope,
		'created_at': int(time.time())}

	return JsonResponse(out)

@permission_classes((IsAuthenticated, ))
def applications(request):

	if request.method == "POST":
		
		app = Oauth2Application.objects.create(

			name = request.POST.get("name"),

			user = request.user,
			client_id = get_random_string(32),
			client_secret = get_random_string(32),
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

	apps = Oauth2Application.objects.filter(user=request.user).all()

	return render(request, 'oauth2/applications.html', {'apps': apps})

@permission_classes((IsAuthenticated, ))
def application_detail(request, client_id):

	app = Oauth2Application.objects.filter(client_id=client_id).first()
	if app is None:
		return HttpResponseNotFound("No such application")
	
	token = None
	if request.method == "POST":
		action = request.POST.get("action")
		if action == "Create Authorization":
			token = create_authorization(request.user, app)

	auths = Oauth2Authorization.objects.filter(parent_app=app).all()
	
	return render(request, 'oauth2/application_detail.html', 
		{'app': app, 'auths': auths, 'token': token})

