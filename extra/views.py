# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.shortcuts import render
from django.http import HttpResponse
from querymap.views import p
from rest_framework.decorators import api_view
import pgmap
import io

# Create your views here.

@api_view(['GET'])
def getaffected(request):

	t = p.GetTransaction(b"ACCESS SHARE")

	osmData = pgmap.OsmData()
	t.GetObjectsById(b"node", pgmap.seti64([1000594013181]), osmData)

	affectedData = pgmap.OsmData()
	t.GetAffectedObjects(osmData, affectedData)

	sio = io.BytesIO()
	enc = pgmap.PyOsmXmlEncode(sio)
	affectedData.StreamTo(enc)
	return HttpResponse(sio.getvalue(), content_type='text/xml')

