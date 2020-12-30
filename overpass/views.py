from django.shortcuts import render
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseNotFound, HttpResponseServerError, HttpResponseForbidden
from django.views.decorators.gzip import gzip_page
from django.views.decorators.csrf import csrf_exempt
from querymap.views import p
import pgmap
import io
from rest_framework.decorators import api_view
from pycrocosm import common

@gzip_page #Control gzip on a per-page basis because of BREACH vun. This page contains no secrets.
@csrf_exempt #Contain no secrets to avoid BREACH vun (and this page is never POSTed anyway).
@api_view(['GET'])
def xapi(request):
	bbox = request.GET.get('bbox', None)
	if bbox is not None:
		bbox = list(map(float, bbox.split(",")))
		if len(bbox) != 4:
			return HttpResponseBadRequest("Invalid bbox", content_type="text/plain")

	objectType = request.GET.get('type', None)
	queryKey = request.GET.get('key', None)
	queryValue = request.GET.get('value', None)

	if bbox is None and queryKey is None:
		return HttpResponseBadRequest("Specify either a bbox or a query key (at least)", content_type="text/plain")

	if objectType is None: objectType = 'node'
	if queryKey is None: queryKey = ''
	if queryValue is None: queryValue = ''
	if bbox is None: bbox = []

	t = p.GetTransaction("ACCESS SHARE")

	osmData = pgmap.OsmData()
	t.XapiQuery(objectType, queryKey, queryValue, bbox, osmData)

	sio = io.BytesIO()
	enc = pgmap.PyOsmXmlEncode(sio, common.xmlAttribs)
	osmData.StreamTo(enc)
	return HttpResponse(sio.getvalue(), content_type='text/xml')

