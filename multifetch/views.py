# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from __future__ import print_function

from django.shortcuts import render
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseNotFound
from django.conf import settings
from rest_framework.decorators import api_view

from pycrocosm.mapdb import get_pgmap
from pycrocosm import common
import pgmap
import io

# Create your views here.

@api_view(['GET'])
def index(request, objType):
	if objType not in request.GET:
		return HttpResponseBadRequest("Incorrect arguments in URL")

	try:
		objIds = list(map(int, request.GET[objType].split(",")))
	except ValueError as err:
		return HttpResponseBadRequest(err)
	if len(objIds) > settings.MULTIFETCH_MAXIMUM_IDS:
		return HttpResponseBadRequest("Too many object IDs")

	t = get_pgmap().GetTransaction("ACCESS SHARE")
	osmData = pgmap.OsmData()
	t.GetObjectsById(objType[:-1], objIds, osmData);

	sio = io.BytesIO()
	enc = pgmap.PyOsmXmlEncode(sio, common.xmlAttribs)
	osmData.StreamTo(enc)
	return HttpResponse(sio.getvalue(), content_type='text/xml')
