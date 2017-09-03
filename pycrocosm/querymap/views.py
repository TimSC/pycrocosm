# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.shortcuts import render
from django.http import HttpResponse
from django.conf import settings
import pgmap
import io

defaultDb = settings.DATABASES['default']
p = pgmap.PgMap(b"dbname={} user={} password='{}' hostaddr={} port={}".format(defaultDb["NAME"], 
	defaultDb["USER"], defaultDb["PASSWORD"], defaultDb["HOST"], defaultDb["PORT"]), 
	str(defaultDb["PREFIX"]))

# Create your views here.

def index(request):
	bbox = request.GET.get('bbox', None)
	bbox = map(float, bbox.split(","))
	
	sio = io.BytesIO()
	enc = pgmap.PyOsmXmlEncode(sio)
	p.MapQuery(bbox, 50000, enc)

	return HttpResponse(sio.getvalue(), content_type='text/xml')


