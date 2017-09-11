# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.shortcuts import render
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseNotFound, HttpResponseServerError
from django.views.decorators.csrf import csrf_exempt
from rest_framework.permissions import IsAuthenticated, IsAuthenticatedOrReadOnly
from rest_framework.decorators import api_view, permission_classes, parser_classes

import xml.etree.ElementTree as ET
from defusedxml.ElementTree import parse
from rest_framework.parsers import BaseParser
from querymap.views import p
import pgmap
import io
import datetime
import time

class DefusedXmlParser(BaseParser):
	media_type = 'application/xml'
	def parse(self, stream, media_type, parser_context):
		return parse(stream)

# Create your views here.

@csrf_exempt
@api_view(['GET', 'PUT'])
@permission_classes((IsAuthenticatedOrReadOnly, ))
@parser_classes((DefusedXmlParser,))
def element(request, objType, objId):

	if request.method == 'GET':
		osmData = pgmap.OsmData()
		p.GetObjectsById(objType.encode("UTF-8"), pgmap.seti64([int(objId)]), osmData);

		if len(osmData.nodes) + len(osmData.ways) + len(osmData.relations) == 0:
			return HttpResponseNotFound("{} {} not found".format(objType, objId))

		sio = io.BytesIO()
		enc = pgmap.PyOsmXmlEncode(sio)
		osmData.StreamTo(enc)
		return HttpResponse(sio.getvalue(), content_type='text/xml')

	if request.method == 'PUT':
		osmData = pgmap.OsmData()
		createdNodeIds = pgmap.mapi64i64()
		createdWayIds = pgmap.mapi64i64()
		createdRelationIds = pgmap.mapi64i64()
		p.StoreObjects(osmData, createdNodeIds, createdWayIds, createdRelationIds)

		return HttpResponse("", content_type='text/plain')

@csrf_exempt
@api_view(['PUT'])
@permission_classes((IsAuthenticated, ))
@parser_classes((DefusedXmlParser,))
def create(request, objType):

	osmData = pgmap.OsmData()

	if objType == "node":
		nodeEl = request.data.find("node")
		nodeObj = pgmap.OsmNode()
		nodeObj.objId = 0
		nodeObj.metaData.timestamp = int(time.time())
		nodeObj.metaData.changeset = int(nodeEl.attrib["changeset"])
		nodeObj.metaData.uid = request.user.id
		nodeObj.metaData.username = request.user.username.encode("UTF-8")
		nodeObj.metaData.visible = True
		for tagEl in nodeEl.findall("tag"):
			nodeObj.tags[tagEl.attrib["k"]] = tagEl.attrib["v"]
		nodeObj.lat = float(nodeEl.attrib["lat"])
		nodeObj.lon = float(nodeEl.attrib["lon"])
		osmData.nodes.append(nodeObj)

	createdNodeIds = pgmap.mapi64i64()
	createdWayIds = pgmap.mapi64i64()
	createdRelationIds = pgmap.mapi64i64()
	errStr = pgmap.PgMapError()
	ok = p.StoreObjects(osmData, createdNodeIds, createdWayIds, createdRelationIds, errStr)

	if not ok:
		return HttpResponseServerError(errStr, content_type='text/plain')

	return HttpResponse("", content_type='text/plain')

