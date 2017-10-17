# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.shortcuts import render
from django.http import HttpResponse
from querymap.views import p
import pgmap
import io

def index(request):

	t = p.GetTransaction(b"ACCESS SHARE")

	osmData = pgmap.OsmData()
	t.GetReplicateDiff(1481539503, 1481539523, osmData)

	sio = io.BytesIO()
	enc = pgmap.PyOsmXmlEncode(sio)
	osmData.StreamTo(enc)
	return HttpResponse(sio.getvalue(), content_type='text/xml')



