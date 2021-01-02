from django.shortcuts import render
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseNotFound, HttpResponseServerError, HttpResponseForbidden
from django.views.decorators.gzip import gzip_page
from django.views.decorators.csrf import csrf_exempt
from querymap.views import p
import pgmap
import io
from rest_framework.decorators import api_view
from pycrocosm import common

def ParseBrackets(queryStr):
	
	c = 0
	skipNext = False
	depth = 0
	buff = []
	fragments = []
	while c < len(queryStr):
		if skipNext:
			skipNext = False
			if depth >= 1:
				buff.append(queryStr[c])
		elif queryStr[c] == '\\':
			if depth >= 1:
				buff.append(queryStr[c])
			else:
				skipNext = True
		elif queryStr[c] == '[':
			if depth >= 1:
				buff.append(queryStr[c])	
			depth += 1
		elif queryStr[c] == ']':
			depth -= 1
			if depth >= 1:
				buff.append(queryStr[c])
			elif depth < 0:
				raise ValueError("Unexpected closing bracket")
			else:
				fragments.append("".join(buff))
				buff = []
		else:
			buff.append(queryStr[c])
		c += 1

	return fragments

def ParseFragment(frag):

	c = 0
	skipNext = False
	fragments = []
	buff = []
	while c < len(frag):
		if skipNext:
			skipNext = False
		elif frag[c] == '\\':
			skipNext = True
		elif frag[c] == '=':
			fragments.append("".join(buff))
			buff = []
		else:
			buff.append(frag[c])

		c += 1	

	fragments.append("".join(buff))

	return fragments

def xapi(request, queryStr):

	#Parse input query to find object type
	allowedTypes = ['*', 'node', 'way', 'relation']
	objectType = None
	for alTy in allowedTypes:
		if queryStr[:len(alTy)] == alTy:
			objectType = alTy
			queryStr = queryStr[len(alTy):]
	if objectType is None:
		return HttpResponseBadRequest("Object type not recognized", content_type="text/plain")

	fragments = ParseBrackets(queryStr)

	#Parse square bracket predicates
	bbox = None
	keyPredicate = []
	for frag in fragments:
		fragParts = ParseFragment(frag)
		if fragParts[0] == "bbox" and len(fragParts) >= 2:
			bbox = list(map(float, fragParts[1].split(",")))
		elif len(fragParts) >= 2:
			keyPredicate.append(fragParts[:2])

	if bbox is not None and len(bbox) != 4:
			return HttpResponseBadRequest("Invalid bbox", content_type="text/plain")
	if bbox is None: bbox = []

	if len(keyPredicate) >= 1:
		queryKey = keyPredicate[0][0]
		queryValue = keyPredicate[0][1]

	if bbox is None and queryKey is None:
		return HttpResponseBadRequest("Specify either a bbox or a query key (at least)", content_type="text/plain")

	if queryValue == '*': queryValue = ''

	t = p.GetTransaction("ACCESS SHARE")

	osmData = pgmap.OsmData()
	t.XapiQuery(objectType, queryKey, queryValue, bbox, osmData)

	sio = io.BytesIO()
	enc = pgmap.PyOsmXmlEncode(sio, common.xmlAttribs)
	osmData.StreamTo(enc)
	return HttpResponse(sio.getvalue(), content_type='text/xml')


@gzip_page #Control gzip on a per-page basis because of BREACH vun. This page contains no secrets.
@csrf_exempt #Contain no secrets to avoid BREACH vun (and this page is never POSTed anyway).
@api_view(['GET'])
def xapi1(request, queryStr):
	return xapi(request, queryStr)

@gzip_page #Control gzip on a per-page basis because of BREACH vun. This page contains no secrets.
@csrf_exempt #Contain no secrets to avoid BREACH vun (and this page is never POSTed anyway).
@api_view(['GET'])
def xapi2(request):
	return xapi(request, request.META['QUERY_STRING'])

