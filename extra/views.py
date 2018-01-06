# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from __future__ import print_function

from django.shortcuts import render
from django.http import HttpResponse
from querymap.views import p
from rest_framework.decorators import api_view, permission_classes, parser_classes
from pycrocosm.parsers import DefusedXmlParser, OsmChangeXmlParser
from pycrocosm import common
import pgmap
import io

# Create your views here.

@api_view(['GET'])
def get_affected(request, objType, objIds):

	t = p.GetTransaction(b"ACCESS SHARE")

	osmData = pgmap.OsmData()
	t.GetObjectsById(objType.encode("UTF-8"), pgmap.seti64(map(int, objIds.split(","))), osmData)

	affectedData = pgmap.OsmData()
	t.GetAffectedObjects(osmData, affectedData)

	sio = io.BytesIO()
	enc = pgmap.PyOsmXmlEncode(sio, common.xmlAttribs)
	affectedData.StreamTo(enc)
	return HttpResponse(sio.getvalue(), content_type='text/xml')


@api_view(['POST'])
@parser_classes((DefusedXmlParser, ))
def get_affected_from_upload(request):

	t = p.GetTransaction(b"ACCESS SHARE")

	affectedData = pgmap.OsmData()
	t.GetAffectedObjects(request.data, affectedData)

	sio = io.BytesIO()
	enc = pgmap.PyOsmXmlEncode(sio)
	affectedData.StreamTo(enc)
	return HttpResponse(sio.getvalue(), content_type='text/xml')

