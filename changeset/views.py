# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.shortcuts import render
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseNotFound
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from rest_framework.permissions import IsAuthenticated, IsAuthenticatedOrReadOnly
from rest_framework.decorators import api_view, permission_classes, parser_classes

import xml.etree.ElementTree as ET
import cStringIO
import datetime
from querymap.views import p
from .models import Changeset
from pycrocosm.parsers import DefusedXmlParser, OsmChangeXmlParser

# Create your views here.

def CheckTags(tags):
	for k in tags:
		if len(k) > 255:
			return False
		if len(tags[k]) > 255:
			return False
	return True

def SerializeChangeset(changesetData, include_discussion=False):
	root = ET.Element('osm')
	doc = ET.ElementTree(root)
	root.attrib["version"] = str(settings.API_VERSION)
	root.attrib["generator"] = settings.GENERATOR

	changeset = ET.SubElement(root, "changeset")
	changeset.attrib["id"] = str(changesetData.id)
	changeset.attrib["user"] = str(changesetData.user.username)
	changeset.attrib["uid"] = str(changesetData.user.id)
	changeset.attrib["created_at"] = str(changesetData.open_datetime.isoformat())
	if not changesetData.is_open:
		changeset.attrib["closed_at"] = str(changesetData.close_datetime.isoformat())
	changeset.attrib["open"] = str(changesetData.is_open).lower()
	changeset.attrib["min_lon"] = str(changesetData.min_lon)
	changeset.attrib["min_lat"] = str(changesetData.min_lat)
	changeset.attrib["max_lon"] = str(changesetData.max_lon)
	changeset.attrib["max_lat"] = str(changesetData.max_lat)

	for tagKey in changesetData.tags:
		tag = ET.SubElement(changeset, "tag")
		tag.attrib["k"] = tagKey
		tag.attrib["v"] = changesetData.tags[tagKey]

	if include_discussion:

		discussion = ET.SubElement(changeset, "discussion")

		comment = ET.SubElement(discussion, "comment")
		comment.attrib["data"] = "2015-01-01T18:56:48Z"
		comment.attrib["uid"] = "1841"
		comment.attrib["user"] = "metaodi"

		text = ET.SubElement(comment, "text")
		text.text = "Did you verify those street names?"

	sio = cStringIO.StringIO()
	doc.write(sio, "utf-8")
	return HttpResponse(sio.getvalue(), content_type='text/xml')

@csrf_exempt
@api_view(['PUT'])
@permission_classes((IsAuthenticated, ))
@parser_classes((DefusedXmlParser, ))
def create(request):

	userRecord = request.user
	csIn = request.data.find("changeset")
	tags = {}
	for tag in csIn.findall("tag"):
		tags[tag.attrib["k"]] = tag.attrib["v"]
	if not CheckTags(tags):
		return HttpResponseBadRequest()

	changeset = Changeset.objects.create(user=userRecord, tags=tags)

	return HttpResponse(changeset.id, content_type='text/plain')

@csrf_exempt
@api_view(['GET', 'PUT'])
@permission_classes((IsAuthenticatedOrReadOnly, ))
@parser_classes((DefusedXmlParser, ))
def changeset(request, changesetId):
	include_discussion = request.GET.get('include_discussion', 'false') == "true"

	try:
		changesetData = Changeset.objects.get(id=changesetId)
	except Changeset.DoesNotExist:
		return HttpResponseNotFound()

	if request.method == 'GET':
		return SerializeChangeset(changesetData, include_discussion)

	if request.method == 'PUT':
		
		csIn = request.data.find("changeset")
		tags = {}
		for tag in csIn.findall("tag"):
			tags[tag.attrib["k"]] = tag.attrib["v"]
		if not CheckTags(tags):
			return HttpResponseBadRequest()

		changesetData.tags = tags
		changesetData.save()

		return SerializeChangeset(changesetData)

@csrf_exempt
@api_view(['PUT'])
@permission_classes((IsAuthenticated, ))
def close(request, changesetId):
	try:
		changesetData = Changeset.objects.get(id=changesetId)
	except Changeset.DoesNotExist:
		return HttpResponseNotFound()

	if not changesetData.is_open:
		err = "The changeset {} was closed at {}.".format(changesetData.id, changesetData.close_datetime.isoformat())
		response = HttpResponse(err, content_type="text/plain")
		response.status_code = 409
		return response

	changesetData.is_open = False
	changesetData.close_datetime = datetime.datetime.now()
	changesetData.save()

	return HttpResponse("", content_type='text/plain')

@api_view(['GET'])
@permission_classes((IsAuthenticatedOrReadOnly, ))
def download(request, changesetId):
	return get(request, changesetId)

@csrf_exempt
@api_view(['POST'])
@permission_classes((IsAuthenticated, ))
@parser_classes((DefusedXmlParser, ))
def expand_bbox(request, changesetId):
	try:
		changesetData = Changeset.objects.get(id=changesetId)
	except Changeset.DoesNotExist:
		return HttpResponseNotFound()

	if not changesetData.is_open:
		err = "The changeset {} was closed at {}.".format(changesetData.id, changesetData.close_datetime.isoformat())
		response = HttpResponse(err, content_type="text/plain")
		response.status_code = 409
		return response

	for node in request.data.findall("node"):
		if not changesetData.bbox_set:
			changesetData.min_lat = node.attrib["lat"]
			changesetData.max_lat = node.attrib["lat"]
			changesetData.min_lon = node.attrib["lon"]
			changesetData.max_lon = node.attrib["lon"]
			changesetData.bbox_set = True
		else:
			if node.attrib["lat"] < changesetData.min_lat: changesetData.min_lat = node.attrib["lat"]
			if node.attrib["lat"] > changesetData.max_lat: changesetData.max_lat = node.attrib["lat"]
			if node.attrib["lon"] < changesetData.min_lon: changesetData.min_lon = node.attrib["lon"]
			if node.attrib["lon"] > changesetData.max_lon: changesetData.max_lon = node.attrib["lon"]

	changesetData.save()

	return SerializeChangeset(changesetData)

@api_view(['GET'])
def list(request):

	bbox = request.GET.get('bbox', None) #min_lon,min_lat,max_lon,max_lat
	user = request.GET.get('user', None)
	display_name = request.GET.get('display_name', None)
	timearg = request.GET.get('time', None)
	openarg = request.GET.get('open', None)
	close = request.GET.get('closed', None)
	changesets = request.GET.get('changesets', None)

	cs = Changeset.objects.all()[:100]

	return HttpResponse("xxx"+str(len(cs)), content_type='text/plain')

@csrf_exempt
@api_view(['POST'])
@permission_classes((IsAuthenticated, ))
@parser_classes((OsmChangeXmlParser, ))
def upload(request, changesetId):
	t = p.GetTransaction(b"EXCLUSIVE")

	for i in range(request.data.blocks.size()):
		print request.data.actions[i]
		block = request.data.blocks[i]

		createdNodeIds = pgmap.mapi64i64()
		createdWayIds = pgmap.mapi64i64()
		createdRelationIds = pgmap.mapi64i64()
		errStr = pgmap.PgMapError()
		t = p.GetTransaction(b"EXCLUSIVE")
		ok = t.StoreObjects(block, createdNodeIds, createdWayIds, createdRelationIds, errStr)

	if not ok:
		return HttpResponseServerError(errStr, content_type='text/plain')
	else:
		t.Commit()

	return HttpResponse("", content_type='text/xml')

@csrf_exempt
@api_view(['POST'])
@permission_classes((IsAuthenticated, ))
def comment(request, changesetId):
	return HttpResponse("", content_type='text/xml')

@csrf_exempt
@api_view(['POST'])
@permission_classes((IsAuthenticated, ))
def subscribe(request, changesetId):
	return HttpResponse("", content_type='text/xml')

@csrf_exempt
@api_view(['POST'])
@permission_classes((IsAuthenticated, ))
def unsubscribe(request, changesetId):
	return HttpResponse("", content_type='text/xml')

