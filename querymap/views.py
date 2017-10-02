# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.shortcuts import render
from django.http import HttpResponse, HttpResponseBadRequest, StreamingHttpResponse
from django.conf import settings
from rest_framework.decorators import api_view, permission_classes, parser_classes
from django.views.decorators.gzip import gzip_page
from django.views.decorators.csrf import csrf_exempt
from rest_framework.permissions import IsAuthenticated, IsAuthenticatedOrReadOnly
import pgmap
import io
import random
import sys

TEST = 'test' in sys.argv
if TEST:
	ACTIVE_DB = "PREFIX_TEST"
else:
	ACTIVE_DB = "PREFIX_MOD"

mapDbSettings = settings.MAP_DATABASE
connectionString = b"dbname={} user={} password='{}' hostaddr={} port={}".format(mapDbSettings["NAME"], 
	mapDbSettings["USER"], mapDbSettings["PASSWORD"], mapDbSettings["HOST"], mapDbSettings["PORT"])
p = pgmap.PgMap(connectionString, 
	str(mapDbSettings["PREFIX"]), str(mapDbSettings[ACTIVE_DB]))

class MapQueryResponse(object):
	def __init__(self, bbox):
		self.sio = io.BytesIO()
		self.enc = pgmap.PyOsmXmlEncode(self.sio)
		
		t = p.GetTransaction(b"ACCESS SHARE")
		self.mapQuery = t.GetQueryMgr()
		if self.mapQuery.Start(bbox, self.enc)<0:
			raise RuntimeError("Map query failed to start")
		self.complete = False

	def __iter__(self):
		return self

	def next(self):
		if self.complete:
			raise StopIteration()
	
		ret = self.mapQuery.Continue()

		if ret < 0:
			raise RuntimeError("Map query error")
		if ret == 1:
			#Add random whitespace to the end to confuse BREACH attacks
			self.complete = True
			whitespace = []
			for i in range(random.randint(0, 256)):
				whitespace.append(random.choice((b'\n',b' ',b'\t')))
			return self.sio.getvalue() + b"".join(whitespace)

		buff = self.sio.getvalue()
		self.sio = io.BytesIO() #Reset buffer 
		self.enc.SetOutput(self.sio)

		return buff

@gzip_page #Control gzip on a per-page basis because of BREACH vun. This page contains no secrets.
@csrf_exempt #Contain no secrets to avoid BREACH vun (and this page is never POSTed anyway).
@api_view(['GET'])
def index(request):
	bbox = request.GET.get('bbox', None)
	if bbox is None:
		return HttpResponseBadRequest("Bbox must be specified as an argument", content_type="text/plain")

	bbox = map(float, bbox.split(","))
	if len(bbox) != 4:
		return HttpResponseBadRequest("Invalid bbox", content_type="text/plain")

	#left,bottom,right,top
	dLon = bbox[2] - bbox[0]
	dLat = bbox[3] - bbox[1]
	area = dLon * dLat
	if area > settings.AREA_MAXIMUM:
		err = "The maximum bbox size is {}, and your request was too large. Either request a smaller area, or use planet.osm".format(settings.AREA_MAXIMUM)
		response = HttpResponseBadRequest(err, content_type="text/plain")
		response["Error"] = err
		return response

	return StreamingHttpResponse(MapQueryResponse(bbox), content_type='text/xml')


