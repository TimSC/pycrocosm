from django.shortcuts import render
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseNotFound
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
from rest_framework.decorators import api_view, permission_classes, parser_classes
from rest_framework.permissions import IsAuthenticated
from .models import UserData, UserPreference

import xml.etree.ElementTree as ET
from rest_framework.parsers import BaseParser
from pycrocosm.parsers import DefusedXmlParser
from pycrocosm import common
from pycrocosm.mapdb import get_pgmap

import io
import sys
import datetime

class PlainTextParser(BaseParser):
	media_type = 'text/plain'
	def parse(self, stream, media_type, parser_context):
		return stream.read()

# Create your views here.

@api_view(['GET'])
@permission_classes((IsAuthenticated, ))
def details(request):

	userRecord = request.user

	if common.wants_json(request):
		# The same information as the XML below
		user = {
			"id": userRecord.id,
			"display_name": userRecord.username,
			"account_created": userRecord.date_joined.isoformat(),
			"description": userRecord.userdata.description,
			"contributor_terms": {"agreed": False, "pd": False},
			"roles": [],
			"changesets": {"count": 12345},
			"traces": {"count": 12345},
			"blocks": {"received": {"count": 0, "active": 0}},
			"languages": ["en"],
			"messages": {"received": {"count": 1, "unread": 0}, "sent": {"count": 1}},
		}
		if userRecord.userdata.home_zoom >= 0:
			user["home"] = {"lat": userRecord.userdata.home_lat, "lon": userRecord.userdata.home_lon,
				"zoom": userRecord.userdata.home_zoom}
		return common.json_response({"user": user}, legal=False)

	root = ET.Element('osm')
	doc = ET.ElementTree(root)
	root.attrib["version"] = str(settings.API_VERSION)
	root.attrib["generator"] = settings.GENERATOR

	user = ET.SubElement(root, "user")
	user.attrib["display_name"] = userRecord.username
	user.attrib["account_created"] = str(userRecord.date_joined.isoformat())
	user.attrib["id"] = str(userRecord.id)

	cts = ET.SubElement(user, "contributor-terms")
	cts.attrib["agreed"] = "false"
	cts.attrib["pd"] = "false"

	roles = ET.SubElement(user, "roles")

	changesets = ET.SubElement(user, "changesets")
	changesets.attrib["count"] = "12345"

	traces = ET.SubElement(user, "traces")
	traces.attrib["count"] = "12345"

	blocks = ET.SubElement(user, "blocks")
	received = ET.SubElement(blocks, "received")
	received.attrib["count"] = "0"
	received.attrib["active"] = "0"

	if userRecord.userdata.home_zoom >= 0:
		home = ET.SubElement(user, "home")
		home.attrib["lat"] = str(userRecord.userdata.home_lat)
		home.attrib["lon"] = str(userRecord.userdata.home_lon)
		home.attrib["zoom"] = str(userRecord.userdata.home_zoom)

	description = ET.SubElement(user, "description")
	description.text = userRecord.userdata.description

	languages = ET.SubElement(user, "languages")
	lang = ET.SubElement(languages, "lang")
	lang.text="en"

	messages = ET.SubElement(user, "messages")
	msgReceived = ET.SubElement(messages, "received")
	msgReceived.attrib["count"] = "1"
	msgReceived.attrib["unread"] = "0"
	msgSent = ET.SubElement(messages, "sent")
	msgSent.attrib["count"] = "1"

	print (ET.dump(root))
	sio = io.BytesIO()
	doc.write(sio, "UTF-8")
	return HttpResponse(sio.getvalue(), content_type='text/xml')

# ****** Public user information ******

def user_changeset_counts(userRecords):
	"""The number of changesets each of the users has opened, by user ID."""
	counts = {}
	if len(userRecords) == 0:
		return counts
	t = get_pgmap().GetTransaction("ACCESS SHARE")
	try:
		for userRecord in userRecords:
			counts[userRecord.id] = t.GetChangesetCount(userRecord.id)
	finally:
		common.abort_transaction(t)
	return counts

def account_created(userRecord):
	return userRecord.date_joined.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def user_description(userRecord):
	try:
		return userRecord.userdata.description
	except UserData.DoesNotExist:
		return ""

def public_user_to_element(userRecord, changesetCount):
	"""What anyone may know about a user: nothing private, such as a home location."""
	user = ET.Element("user")
	user.attrib["id"] = str(userRecord.id)
	user.attrib["display_name"] = userRecord.username
	user.attrib["account_created"] = account_created(userRecord)
	description = ET.SubElement(user, "description")
	description.text = user_description(userRecord)
	cts = ET.SubElement(user, "contributor-terms")
	cts.attrib["agreed"] = "false"
	ET.SubElement(user, "roles")
	changesets = ET.SubElement(user, "changesets")
	changesets.attrib["count"] = str(changesetCount)
	# Traces and user blocks are not implemented, so there are none
	traces = ET.SubElement(user, "traces")
	traces.attrib["count"] = "0"
	blocks = ET.SubElement(user, "blocks")
	received = ET.SubElement(blocks, "received")
	received.attrib["count"] = "0"
	received.attrib["active"] = "0"
	return user

def public_user_to_dict(userRecord, changesetCount):
	return {
		"id": userRecord.id,
		"display_name": userRecord.username,
		"account_created": account_created(userRecord),
		"description": user_description(userRecord),
		"contributor_terms": {"agreed": False},
		"roles": [],
		"changesets": {"count": changesetCount},
		"traces": {"count": 0},
		"blocks": {"received": {"count": 0, "active": 0}},
	}

def public_users_response(request, userRecords, single=False):
	counts = user_changeset_counts(userRecords)
	if common.wants_json(request):
		users = [public_user_to_dict(u, counts[u.id]) for u in userRecords]
		if single:
			return common.json_response({"user": users[0]}, legal=False)
		return common.json_response({"users": [{"user": u} for u in users]}, legal=False)

	root = ET.Element('osm')
	doc = ET.ElementTree(root)
	root.attrib["version"] = str(settings.API_VERSION)
	root.attrib["generator"] = settings.GENERATOR
	for userRecord in userRecords:
		root.append(public_user_to_element(userRecord, counts[userRecord.id]))
	sio = io.BytesIO()
	doc.write(sio, "UTF-8")
	return HttpResponse(sio.getvalue(), content_type='text/xml')

@api_view(['GET'])
def user(request, uid):
	"""GET /api/0.6/user/#id: public information about one user."""
	try:
		userRecord = User.objects.get(id=int(uid))
	except (User.DoesNotExist, OverflowError):
		return HttpResponseNotFound("User not found", content_type="text/plain")
	if not userRecord.is_active:
		return HttpResponse("User has been deleted", status=410, content_type="text/plain")
	return public_users_response(request, [userRecord], single=True)

@api_view(['GET'])
def users(request):
	"""GET /api/0.6/users?users=#id1,#id2: public information about several users.

	Users are listed in ID order. IDs of unknown or deleted users are left out.
	"""
	if 'users' not in request.GET:
		return HttpResponseBadRequest("The parameter users is required, and must be of the form users=id[,id[,id...]]",
			content_type="text/plain")
	maximum = getattr(settings, 'MULTIFETCH_MAXIMUM_IDS', 1000)
	try:
		ids = set(int(v) for v in request.GET['users'].split(","))
	except ValueError:
		return HttpResponseBadRequest("Parameter users should be a comma separated list of user IDs",
			content_type="text/plain")
	if len(ids) > maximum:
		return HttpResponseBadRequest("Too many users requested; maximum is {}".format(maximum),
			content_type="text/plain")
	# IDs too large for the database cannot belong to anyone
	ids = [v for v in ids if 0 < v < 2**31]
	userRecords = list(User.objects.filter(id__in=ids, is_active=True).order_by("id"))
	return public_users_response(request, userRecords)

@csrf_exempt
@api_view(['GET', 'PUT'])
@permission_classes((IsAuthenticated, ))
@parser_classes((DefusedXmlParser,))
def preferences(request):

	userRecord = request.user
	prefs = UserPreference.objects.filter(user=userRecord)

	if request.method == 'GET' and common.wants_json(request):
		return common.json_response({"preferences": {pref.key: pref.value for pref in prefs}}, legal=False)

	if request.method == 'GET':
		root = ET.Element('osm')
		doc = ET.ElementTree(root)
		root.attrib["version"] = str(settings.API_VERSION)
		root.attrib["generator"] = settings.GENERATOR

		preferences = ET.SubElement(root, "preferences")

		for pref in prefs:
			preference = ET.SubElement(preferences, "preference")
			preference.attrib["k"] = pref.key
			preference.attrib["v"] = pref.value

		sio = io.BytesIO()
		doc.write(sio, "UTF-8")
		return HttpResponse(sio.getvalue(), content_type='text/xml')

	if request.method == 'PUT':
		#Clear existing records
		prefs.delete()

		prefsNode = request.data.find("preferences")
		dataMap = {}
		for pref in prefsNode:
			dataMap[pref.attrib["k"]] = pref.attrib["v"]
		for pref in dataMap:
			UserPreference.objects.create(user=request.user, key=pref, value=dataMap[pref])

		return HttpResponse("", content_type='text/plain')

@csrf_exempt
@api_view(['PUT'])
@permission_classes((IsAuthenticated, ))
@parser_classes((PlainTextParser,))
def preferences_put(request, key):
	if(len(key) > 255):
		return HttpResponseBadRequest()
	if(len(request.data) > 255):
		return HttpResponseBadRequest()

	try:
		existing = UserPreference.objects.get(user=request.user, key=key)
		existing.value = request.data.decode("utf-8")
		existing.save()
	except UserPreference.DoesNotExist:
		UserPreference.objects.create(user=request.user, key=key, value=request.data.decode("utf-8"))

	return HttpResponse("", content_type='text/plain')

@api_view(['GET', 'POST'])
def not_implemented(request):
	return HttpResponse("Not implemented", status=501)
