# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from __future__ import print_function

from django.shortcuts import render
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseNotFound, HttpResponseServerError, HttpResponseForbidden
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from rest_framework.permissions import IsAuthenticated, IsAuthenticatedOrReadOnly
from rest_framework.decorators import api_view, permission_classes, parser_classes

import xml.etree.ElementTree as ET
from defusedxml.ElementTree import parse
from querymap.views import p
from pycrocosm import common
import pgmap
import io
import datetime
import time
from pycrocosm.parsers import DefusedXmlParser, OsmDataXmlParser
from changeset.views import upload_block

def upload_single_object(action, request, obj, objType, t):

	#Additional validate of input
	if(request.data.nodes.size() != (objType == "node")
		or request.data.ways.size() != (objType == "way")
		or request.data.relations.size() != (objType == "relation")):
		return HttpResponseBadRequest("Wrong number of objects")

	changesetId = obj.metaData.changeset

	#Check changeset is open and for this user
	changesetData = pgmap.PgChangeset()
	errStr = pgmap.PgMapError()
	ret = t.GetChangeset(int(changesetId), changesetData, errStr)
	if ret == -1:
		return HttpResponseNotFound("Changeset not found")
	if ret == 0:	
		return HttpResponseServerError(errStr.errStr)

	if not changesetData.is_open:
		err = "The changeset {} was closed at {}.".format(changesetData.id, 
			datetime.datetime.fromtimestamp(changesetData.close_timestamp).isoformat())
		response = HttpResponse(err, content_type="text/plain")
		response.status_code = 409
		return response

	if request.user.id != changesetData.uid:
		return HttpResponse("This changeset belongs to a different user", status=409, content_type="text/plain")

	#Prepare diff result xml
	responseRoot = ET.Element('diffResult')
	doc = ET.ElementTree(responseRoot)
	responseRoot.attrib["version"] = str(settings.API_VERSION)
	responseRoot.attrib["generator"] = settings.GENERATOR

	createdNodeIds = pgmap.mapi64i64()
	createdWayIds = pgmap.mapi64i64()
	createdRelationIds = pgmap.mapi64i64()

	timestamp = time.time()
	ret = upload_block("create", request.data, changesetId, t, responseRoot, 
		request.user.id, request.user.username, timestamp,
		createdNodeIds, createdWayIds, createdRelationIds)
	if ret != True:
		return ret

	t.Commit()
	return True

# Create your views here.

@csrf_exempt
@api_view(['GET', 'PUT', 'DELETE'])
@permission_classes((IsAuthenticatedOrReadOnly, ))
@parser_classes((OsmDataXmlParser,))
def element(request, objType, objId):

	if request.method == 'GET':
		osmData = pgmap.OsmData()
		t = p.GetTransaction("ACCESS SHARE")
		t.GetObjectsById(objType, pgmap.seti64([int(objId)]), osmData)

		if len(osmData.nodes) + len(osmData.ways) + len(osmData.relations) == 0:
			return HttpResponseNotFound("{} {} not found".format(objType, objId))

		sio = io.BytesIO()
		enc = pgmap.PyOsmXmlEncode(sio, common.xmlAttribs)
		osmData.StreamTo(enc)
		return HttpResponse(sio.getvalue(), content_type='text/xml')

	if request.method in ['PUT', 'DELETE']:
		t = p.GetTransaction("EXCLUSIVE")
		action = None
		if request.method == "PUT": action = "modify"
		if request.method == "DELETE": action = "delete"

		obj = None
		if objType == "node": obj = request.data.nodes[0]
		if objType == "way": obj = request.data.ways[0]
		if objType == "relation": obj = request.data.relations[0]

		if obj.objId != objId:
			return HttpResponseBadRequest("Object has wrong ID")

		ret = upload_single_object(action, request, obj, objType, t)
		if ret != True:
			return ret

		return HttpResponse("", content_type='text/plain')

@csrf_exempt
@api_view(['PUT'])
@permission_classes((IsAuthenticated, ))
@parser_classes((OsmDataXmlParser,))
def create(request, objType):
	t = p.GetTransaction("EXCLUSIVE")

	obj = None
	if objType == "node": obj = request.data.nodes[0]
	if objType == "way": obj = request.data.ways[0]
	if objType == "relation": obj = request.data.relations[0]

	ret = upload_single_object("created", request, obj, objType, t)
	if ret != True:
		return ret

	return HttpResponse("", content_type='text/plain')

@api_view(['GET'])
def relations_for_obj(request, objType, objId):
	t = p.GetTransaction("ACCESS SHARE")

	#Check reference object exists
	osmData = pgmap.OsmData()
	t.GetObjectsById(objType, pgmap.seti64([int(objId)]), osmData)

	# From the 0.6 spec: "There is no error if the element does not exist."

	osmData = pgmap.OsmData()
	t.GetRelationsForObjs(objType, [int(objId)], osmData)

	sio = io.BytesIO()
	enc = pgmap.PyOsmXmlEncode(sio, common.xmlAttribs)
	osmData.StreamTo(enc)
	return HttpResponse(sio.getvalue(), content_type='text/xml')

@api_view(['GET'])
def ways_for_node(request, objType, objId):
	t = p.GetTransaction("ACCESS SHARE")

	#Check reference object exists
	osmData = pgmap.OsmData()
	t.GetObjectsById(objType, pgmap.seti64([int(objId)]), osmData)

	# From the 0.6 spec: "There is no error if the element does not exist."

	osmData = pgmap.OsmData()
	t.GetWaysForNodes([int(objId)], osmData);	

	sio = io.BytesIO()
	enc = pgmap.PyOsmXmlEncode(sio, common.xmlAttribs)
	osmData.StreamTo(enc)
	return HttpResponse(sio.getvalue(), content_type='text/xml')

@api_view(['GET'])
def full_obj(request, objType, objId):
	t = p.GetTransaction("ACCESS SHARE")

	osmData = pgmap.OsmData()
	t.GetFullObjectById(objType, int(objId), osmData)

	if len(osmData.nodes) + len(osmData.ways) + len(osmData.relations) == 0:

		# No live version found. Check to see if this ever existed.
		t.GetObjectsHistoryById(objType, [int(objId)], osmData)

		if len(osmData.nodes) + len(osmData.ways) + len(osmData.relations) == 0:
			return HttpResponseNotFound("{} {} not found".format(objType, objId))
		else:
			# The object once existed, therefore it was deleted.
			return HttpResponse("Gone", status=410, content_type="text/plain")

	sio = io.BytesIO()
	enc = pgmap.PyOsmXmlEncode(sio, common.xmlAttribs)
	osmData.StreamTo(enc)
	return HttpResponse(sio.getvalue(), content_type='text/xml')

@api_view(['GET'])
def object_version(request, objType, objId, objVer):
	t = p.GetTransaction("ACCESS SHARE")

	osmData = pgmap.OsmData()
	idVerPair = pgmap.pairi64i64(int(objId), int(objVer))
	t.GetObjectsByIdVer(objType, [idVerPair], osmData)

	if len(osmData.nodes) + len(osmData.ways) + len(osmData.relations) == 0:
		return HttpResponseNotFound("{} {} {} not found".format(objType, objId, objVer))

	sio = io.BytesIO()
	enc = pgmap.PyOsmXmlEncode(sio, common.xmlAttribs)
	osmData.StreamTo(enc)
	return HttpResponse(sio.getvalue(), content_type='text/xml')

@api_view(['GET'])
def object_history(request, objType, objId):
	t = p.GetTransaction("ACCESS SHARE")

	osmData = pgmap.OsmData()
	t.GetObjectsHistoryById(objType, [int(objId)], osmData)

	if len(osmData.nodes) + len(osmData.ways) + len(osmData.relations) == 0:
		return HttpResponseNotFound("{} {} not found".format(objType, objId))

	sio = io.BytesIO()
	enc = pgmap.PyOsmXmlEncode(sio, common.xmlAttribs)
	osmData.StreamTo(enc)
	return HttpResponse(sio.getvalue(), content_type='text/xml')

@api_view(['GET'])
def object_bbox(request, objType, objId):
	t = p.GetTransaction("ACCESS SHARE")

	bboxes = pgmap.mapi64vectord()
	t.GetObjectBboxes(objType, [int(objId)], bboxes)

	if len(bboxes) == 0:
		return HttpResponseNotFound("{} {} not found".format(objType, objId))

	responseRoot = ET.Element('bbox')
	doc = ET.ElementTree(responseRoot)
	responseRoot.attrib["version"] = str(settings.API_VERSION)
	responseRoot.attrib["generator"] = settings.GENERATOR

	for objIdRet in bboxes:
		bbox = bboxes[objIdRet]
		elObj = ET.SubElement(responseRoot, objType)
		elObj.attrib["id"] = objId
		if len(bbox) == 4:
			elObj.attrib["minlon"] = str(bbox[0])
			elObj.attrib["minlat"] = str(bbox[1])
			elObj.attrib["maxlon"] = str(bbox[2])
			elObj.attrib["maxlat"] = str(bbox[3])
	
	sio = io.BytesIO()
	doc.write(sio, str("UTF-8")) # str work around https://bugs.python.org/issue15811

	return HttpResponse(sio.getvalue(), content_type='text/xml')

