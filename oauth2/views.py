from django.shortcuts import render
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseNotFound, HttpResponseServerError, HttpResponseForbidden, JsonResponse
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, IsAuthenticatedOrReadOnly
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings#
from django.contrib.auth.models import User
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

@csrf_exempt
@api_view(['POST'])
def token(request):
	
	userRecord = request.user
	app = Oauth2Application.objects.filter(client_id=request.POST.get('client_id')).first()
	if app is None:
		return HttpResponseNotFound("No such application")

	decoded = jwt.decode(request.POST.get('code'), settings.SECRET_KEY, algorithms=["HS256"])

	if decoded['type'] != "app":
		return HttpResponseBadRequest("Code incorrect")	
	if decoded['client_id'] != app.client_id:
		return HttpResponseBadRequest("Code does not match client_id")

	if request.POST.get('client_secret') != app.client_secret:
		return HttpResponseBadRequest("client_secret incorrect")

	user = User.objects.filter(id=decoded['user_id']).first()
	if user is None:
		return HttpResponseNotFound("No such user")

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
		permission_openid = False)

	token = jwt.encode({"type": "token", 
		"auth_id": auth.id, 
		"user_id": user.id, 
		"client_id": app.client_id, 
		'scope': scope,
		'created_at': datetime.datetime.now().isoformat()}, 
		settings.SECRET_KEY, algorithm="HS256")

	out = {'access_token': token, 
		'token_type': 'Bearer', 
		'scope': scope,
		'created_at': int(time.time())}

	return JsonResponse(out)

