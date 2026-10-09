# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from __future__ import print_function

from django.shortcuts import render
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseNotFound
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
from rest_framework.decorators import api_view
from elements import views as element_views

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

	return common.osm_data_response(request, osmData)

@csrf_exempt
def collection(request, objType):
	"""/api/0.6/[nodes|ways|relations]: GET fetches several elements, POST creates one."""
	if request.method == 'POST':
		return element_views.create_plural(request, objType)
	return index(request, objType)
