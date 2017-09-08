# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.shortcuts import render
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseNotFound
from rest_framework.decorators import api_view

from querymap.views import p
import pgmap
import io

# Create your views here.

@api_view(['GET'])
def index(request, objType):
	if objType not in request.GET:
		return HttpResponseBadRequest("Incorrect arguments in URL")

	try:
		objIds = map(int, request.GET[objType].split(","))
	except ValueError as err:
		return HttpResponseBadRequest(err)

	osmData = pgmap.OsmData()
	p.GetObjectsById(objType[:-1].encode("UTF-8"), pgmap.seti64(objIds), osmData);

	sio = io.BytesIO()
	enc = pgmap.PyOsmXmlEncode(sio)
	osmData.StreamTo(enc)
	return HttpResponse(sio.getvalue(), content_type='text/xml')

