# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.shortcuts import render
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseNotFound
from django.views.decorators.csrf import csrf_exempt
from rest_framework.permissions import IsAuthenticated, IsAuthenticatedOrReadOnly
from rest_framework.decorators import api_view, permission_classes, parser_classes

import xml.etree.ElementTree as ET
from defusedxml.ElementTree import parse
from rest_framework.parsers import BaseParser
from querymap.views import p
import pgmap
import io

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

