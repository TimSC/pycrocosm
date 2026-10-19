from django.shortcuts import render
from django.http import HttpResponseRedirect, HttpResponse, HttpResponseBadRequest, HttpResponseNotFound, HttpResponseServerError, HttpResponseForbidden, JsonResponse
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, IsAuthenticatedOrReadOnly
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from django.urls import reverse
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
from django.utils.crypto import get_random_string
from django.utils import timezone
import jwt
import datetime
import time
import uuid
import base64
import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from django.utils.crypto import constant_time_compare

from .models import Oauth2Application, Oauth2Authorization, Oauth2AuthorizationCode
from .scopes import DEFAULT_SCOPES, SCOPE_DESCRIPTIONS, SCOPES, granted_scopes, parse_scope
from pycrocosm.ratelimit import rate_limit

def oauth2_signing_secret():
	return getattr(settings, 'OAUTH2_JWT_SECRET', settings.SECRET_KEY)

def oauth2_auth_code_seconds():
	return getattr(settings, 'OAUTH2_AUTH_CODE_SECONDS', 300)

def oauth2_access_token_seconds():
	"""How long an access token lasts; 0, the default, for as long as its
	authorization does, which the user, the application's owner or an
	administrator ends, as on openstreetmap.org. Editors such as JOSM keep a
	token and do not refresh it."""
	return getattr(settings, 'OAUTH2_ACCESS_TOKEN_SECONDS', 0)

def get_redirect_uris(app):
	return [uri for uri in app.redirect_uris.split() if len(uri) > 0]

def get_request_redirect_uri(request, app):
	redirect_uri = request.GET.get('redirect_uri') or request.POST.get('redirect_uri') or "urn:ietf:wg:oauth:2.0:oob"
	if redirect_uri not in get_redirect_uris(app):
		return None
	return redirect_uri

OUT_OF_BAND = "urn:ietf:wg:oauth:2.0:oob"

def find_application(client_id):
	"""The application a request names, or for a request that names none, as
	JOSM's do for a server it has no client ID for, the default one (set in
	the admin panel); None if there is none, or it is disabled."""
	if client_id:
		app = Oauth2Application.objects.filter(client_id=client_id).first()
	else:
		app = Oauth2Application.objects.filter(is_default=True).first()
	return None if app is None or app.disabled else app

# A PKCE code challenge: the base64url SHA-256 of its verifier. RFC 7636 asks
# for a verifier of 43 to 128 characters; JOSM's is a UUID, of 36, which
# openstreetmap.org accepts, and so does this, from 32 (a UUID is 122 random bits).
CODE_CHALLENGE = re.compile(r'^[A-Za-z0-9_-]{43}$')
CODE_VERIFIER = re.compile(r'^[A-Za-z0-9._~-]{32,128}$')

def s256_challenge(verifier):
	return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")

def redirect_with(redirect_uri, **params):
	"""A redirect to the client's redirect URI, with params added to its query."""
	parts = urlsplit(redirect_uri)
	query = parse_qsl(parts.query, keep_blank_values=True) + [(k, v) for k, v in params.items() if v is not None]
	return HttpResponseRedirect(urlunsplit(parts._replace(query=urlencode(query))))

def authorize_error(request, redirect_uri, state, error, description):
	"""An error for the client, once its redirect URI is known to be its own:
	sent back to it, as OAuth2 says, or shown here for the out of band URI."""
	if redirect_uri == OUT_OF_BAND:
		return render(request, 'oauth2/authorize_result.html', {'error': error, 'description': description}, status=400)
	return redirect_with(redirect_uri, error=error, error_description=description, state=state)

@rate_limit("oauth2_authorize", "OAUTH2_RATE_LIMIT_REQUESTS", "OAUTH2_RATE_LIMIT_WINDOW_SECONDS", methods=('GET', 'POST'))
@login_required
def authorize(request):
	"""Asks the signed in user whether to let an application act for them, with
	the scopes it asks for (GET), and gives it a code for an access token if
	they agree (POST, from the page's form).

	A client or redirect URI that is not registered is refused here, never
	redirected to; any other problem is sent back to the redirect URI as an
	OAuth2 error. Without a scope parameter the application asks for
	DEFAULT_SCOPES."""
	params = request.POST if request.method == "POST" else request.GET
	app = find_application(params.get('client_id'))
	if app is None:
		return HttpResponseNotFound("No such application")

	redirect_uri = params.get('redirect_uri') or OUT_OF_BAND
	if redirect_uri not in get_redirect_uris(app):
		return HttpResponseBadRequest("redirect_uri incorrect")
	state = params.get('state')

	if params.get('response_type', 'code') != 'code':
		return authorize_error(request, redirect_uri, state, "unsupported_response_type", "Only response_type=code is supported")
	try:
		scopes = parse_scope(params.get('scope'), app)
	except ValueError as err:
		return authorize_error(request, redirect_uri, state, "invalid_scope", str(err))
	# PKCE, which public applications such as JOSM use in place of a secret
	challenge = params.get('code_challenge', '')
	method = params.get('code_challenge_method', '')
	if challenge or method:
		if method != 'S256':
			return authorize_error(request, redirect_uri, state, "invalid_request", "Only code_challenge_method=S256 is supported")
		if not CODE_CHALLENGE.match(challenge):
			return authorize_error(request, redirect_uri, state, "invalid_request", "code_challenge is not an S256 challenge")

	if request.method != "POST":
		return render(request, 'oauth2/authorize.html', {
			'app': app,
			'scopes': [(scope, SCOPE_DESCRIPTIONS[scope]) for scope in scopes],
			'scope': " ".join(scopes),
			'client_id': app.client_id,
			'redirect_uri': redirect_uri,
			'out_of_band': redirect_uri == OUT_OF_BAND,
			'state': state,
			'code_challenge': challenge,
			'code_challenge_method': method,
		})

	if request.POST.get('decision') != 'approve':
		return authorize_error(request, redirect_uri, state, "access_denied", "The user did not allow access")

	now = timezone.now()
	expires_at = now + datetime.timedelta(seconds=oauth2_auth_code_seconds())
	jti = uuid.uuid4().hex
	Oauth2AuthorizationCode.objects.create(
		jti=jti,
		parent_app=app,
		user=request.user,
		redirect_uri=redirect_uri,
		scope=" ".join(scopes),
		code_challenge=challenge,
		code_challenge_method=method,
		expires_at=expires_at)

	code = jwt.encode({"type": "app", 
		"jti": jti,
		"user_id": request.user.id, 
		"client_id": app.client_id, 
		"redirect_uri": redirect_uri,
		"scope": " ".join(scopes),
		"iat": now,
		"exp": expires_at,
		'created_at': now.isoformat()}, 
		oauth2_signing_secret(), algorithm="HS256")

	if redirect_uri == OUT_OF_BAND:
		return render(request, 'oauth2/authorize_result.html', {'app': app, 'code': code})
	return redirect_with(redirect_uri, code=code, state=state)

def create_authorization(user, app, scopes=DEFAULT_SCOPES):
	"""An authorization for an application to act for a user with the scopes
	(those the application may have), and an access token for it."""
	scopes = [scope for scope in SCOPES if scope in scopes]
	scope = " ".join(scopes)
	now = timezone.now()
	lifetime = oauth2_access_token_seconds()

	auth = Oauth2Authorization.objects.create(parent_app = app,
		user = user,
		disabled = False,
		**{"permission_" + name: name in scopes for name in SCOPES})

	claims = {"type": "token", 
		"auth_id": auth.id, 
		"user_id": user.id, 
		"client_id": app.client_id, 
		'scope': scope,
		"iat": now,
		'created_at': now.isoformat()}
	if lifetime > 0:
		claims["exp"] = now + datetime.timedelta(seconds=lifetime)
	token = jwt.encode(claims, oauth2_signing_secret(), algorithm="HS256")

	return token, scope

@csrf_exempt
@api_view(['POST'])
@rate_limit("oauth2_token", "OAUTH2_RATE_LIMIT_REQUESTS", "OAUTH2_RATE_LIMIT_WINDOW_SECONDS")
def token(request):
	
	userRecord = request.user
	app = find_application(request.POST.get('client_id'))
	if app is None:
		return HttpResponseNotFound("No such application")
	if request.POST.get('grant_type', 'authorization_code') != 'authorization_code':
		return HttpResponseBadRequest("Only grant_type=authorization_code is supported")

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

	# A code asked for with PKCE needs the verifier the challenge was made from
	if codeRecord.code_challenge:
		verifier = request.POST.get('code_verifier', '')
		if not CODE_VERIFIER.match(verifier) or not constant_time_compare(s256_challenge(verifier), codeRecord.code_challenge):
			return HttpResponseBadRequest("code_verifier incorrect")
	# The application proves who it is by its secret, which a public one may
	# leave out when it used PKCE
	secret = request.POST.get('client_secret', '')
	if secret or app.confidential or not codeRecord.code_challenge:
		if not app.check_client_secret(secret):
			return HttpResponseBadRequest("client_secret incorrect")

	user = User.objects.filter(id=decoded['user_id']).first()
	if user is None:
		return HttpResponseNotFound("No such user")

	codeRecord.consumed_at = timezone.now()
	codeRecord.save()

	# What the user agreed to, which the application may still ask for
	token, scope = create_authorization(user, app,
		[name for name in codeRecord.scope.split() if getattr(app, "permission_" + name, False)])

	out = {'access_token': token, 
		'token_type': 'Bearer', 
		'scope': scope,
		'created_at': int(time.time())}
	if oauth2_access_token_seconds() > 0:
		out['expires_in'] = oauth2_access_token_seconds()

	return JsonResponse(out)

@rate_limit("oauth2_applications", "OAUTH2_RATE_LIMIT_REQUESTS", "OAUTH2_RATE_LIMIT_WINDOW_SECONDS")
@login_required
def applications(request):
	"""The signed in user's applications, and registering a new one, with the
	permissions (scopes) it may ask users for."""
	new_client_secret = None
	errors = []
	chosen = set()

	if request.method == "POST":
		name = request.POST.get("name", "").strip()
		chosen = {scope for scope in SCOPES if request.POST.get("permission_" + scope)}
		if not name:
			errors.append("Give the application a name.")
		if not chosen:
			errors.append("Choose at least one permission the application may ask for.")
		if not errors:
			new_client_secret = get_random_string(48)
			app = Oauth2Application.objects.create(
				name = name,
				user = request.user,
				client_id = get_random_string(32),
				client_secret = "",
				redirect_uris = request.POST.get("redirects", "urn:ietf:wg:oauth:2.0:oob"),
				confidential = False,
				disabled = False,
				**{"permission_" + scope: scope in chosen for scope in SCOPES})
			app.set_client_secret(new_client_secret)
			app.save()
			chosen = set()

	apps = Oauth2Application.objects.filter(user=request.user).all()

	return render(request, 'oauth2/applications.html', {'apps': apps, 'new_client_secret': new_client_secret,
		'errors': errors, 'name': request.POST.get("name", "") if errors else "",
		'scopes': [(scope, SCOPE_DESCRIPTIONS[scope], scope in chosen) for scope in SCOPES]})

@rate_limit("oauth2_authorized", "OAUTH2_RATE_LIMIT_REQUESTS", "OAUTH2_RATE_LIMIT_WINDOW_SECONDS")
@login_required
def authorized_applications(request):
	"""The applications the signed in user has allowed to use their account,
	with what each may do, and revoking one: every authorization the user
	gave it is disabled, which its tokens stop working with at once, and any
	code not yet exchanged for a token is used up."""
	if request.method == "POST":
		app = Oauth2Application.objects.filter(client_id=request.POST.get("client_id")).first()
		if app is not None:
			Oauth2Authorization.objects.filter(user=request.user, parent_app=app, disabled=False).update(disabled=True)
			Oauth2AuthorizationCode.objects.filter(user=request.user, parent_app=app, consumed_at=None).update(
				consumed_at=timezone.now())
		return HttpResponseRedirect(request.path)

	# Each application, with what its authorizations allow now, newest first
	found = {}
	for auth in Oauth2Authorization.objects.filter(user=request.user, disabled=False).select_related(
			"parent_app", "parent_app__user").order_by("created_at"):
		entry = found.setdefault(auth.parent_app.id, {"app": auth.parent_app, "scopes": set(),
			"first": auth.created_at})
		entry["scopes"].update(granted_scopes(auth))
		entry["last"] = auth.created_at
	authorized = sorted(found.values(), key=lambda entry: entry["last"], reverse=True)
	for entry in authorized:
		entry["scopes"] = [SCOPE_DESCRIPTIONS[scope] for scope in SCOPES if scope in entry["scopes"]]
	return render(request, 'oauth2/authorized.html', {'authorized': authorized})

@rate_limit("oauth2_application_detail", "OAUTH2_RATE_LIMIT_REQUESTS", "OAUTH2_RATE_LIMIT_WINDOW_SECONDS")
@login_required
def application_detail(request, client_id):

	app = Oauth2Application.objects.filter(client_id=client_id, user=request.user).first()
	if app is None:
		return HttpResponseNotFound("No such application")
	
	token = None
	if request.method == "POST":
		action = request.POST.get("action")
		if action == "Create Authorization":
			# The owner's own token, with whatever the application may have
			token, scope = create_authorization(request.user, app,
				[name for name in SCOPES if getattr(app, "permission_" + name)])

	auths = Oauth2Authorization.objects.filter(parent_app=app).all()
	
	return render(request, 'oauth2/application_detail.html', 
		{'app': app, 'auths': auths, 'token': token,
		'permissions': [SCOPE_DESCRIPTIONS[scope] for scope in SCOPES if getattr(app, "permission_" + scope)]})

def authorization_server_metadata(request):
	"""The OAuth2 authorization server metadata (RFC 8414), at
	/.well-known/oauth-authorization-server, by which clients such as JOSM find
	the endpoints and what they support."""
	from replicate.extracts import public_url
	base = public_url(request).rstrip("/")
	return JsonResponse({
		"issuer": base,
		"authorization_endpoint": base + reverse("oauth2:authorize"),
		"token_endpoint": base + reverse("oauth2:token"),
		"scopes_supported": list(SCOPES),
		"response_types_supported": ["code"],
		"grant_types_supported": ["authorization_code"],
		"code_challenge_methods_supported": ["S256"],
		"token_endpoint_auth_methods_supported": ["client_secret_post", "none"],
	})

