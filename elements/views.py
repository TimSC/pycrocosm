# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.shortcuts import render
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseNotFound, HttpResponseServerError
from django.views.decorators.csrf import csrf_exempt
from rest_framework.permissions import IsAuthenticated, IsAuthenticatedOrReadOnly
from rest_framework.decorators import api_view, permission_classes, parser_classes

import xml.etree.ElementTree as ET
from defusedxml.ElementTree import parse
from querymap.views import p
import pgmap
import io
import datetime
import time
from pycrocosm.parsers import DefusedXmlParser, OsmDataXmlParser

# Create your views here.

@csrf_exempt
@api_view(['GET', 'PUT'])
@permission_classes((IsAuthenticatedOrReadOnly, ))
@parser_classes((DefusedXmlParser,))
def element(request, objType, objId):

	if request.method == 'GET':
		osmData = pgmap.OsmData()
		t = p.GetTransaction(b"ACCESS SHARE")
		t.GetObjectsById(objType.encode("UTF-8"), pgmap.seti64([int(objId)]), osmData);

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
		errStr = pgmap.PgMapError()
		t = p.GetTransaction(b"EXCLUSIVE")
		t.StoreObjects(osmData, createdNodeIds, createdWayIds, createdRelationIds, errStr)

		if not ok:
			return HttpResponseServerError(errStr, content_type='text/plain')
		else:
			t.Commit()

		return HttpResponse("", content_type='text/plain')

@csrf_exempt
@api_view(['PUT'])
@permission_classes((IsAuthenticated, ))
@parser_classes((OsmDataXmlParser,))
def create(request, objType):

	createdNodeIds = pgmap.mapi64i64()
	createdWayIds = pgmap.mapi64i64()
	createdRelationIds = pgmap.mapi64i64()
	errStr = pgmap.PgMapError()
	t = p.GetTransaction(b"EXCLUSIVE")
	ok = t.StoreObjects(request.data, createdNodeIds, createdWayIds, createdRelationIds, errStr)

	if not ok:
		return HttpResponseServerError(errStr, content_type='text/plain')
	else:
		t.Commit()

	return HttpResponse("", content_type='text/plain')

