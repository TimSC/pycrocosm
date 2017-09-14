# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.shortcuts import render
from django.http import HttpResponse, HttpResponseBadRequest, StreamingHttpResponse
from django.conf import settings
import pgmap
import io

defaultDb = settings.DATABASES['default']
p = pgmap.PgMap(b"dbname={} user={} password='{}' hostaddr={} port={}".format(defaultDb["NAME"], 
	defaultDb["USER"], defaultDb["PASSWORD"], defaultDb["HOST"], defaultDb["PORT"]), 
	str(defaultDb["PREFIX"]), str(defaultDb["PREFIX_TEST"]))

# Create your views here.

class MapQueryResponse(object):
	def __init__(self, bbox):
		self.sio = io.BytesIO()
		self.enc = pgmap.PyOsmXmlEncode(self.sio)
		self.mapQuery = p.GetQueryMgr()
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
			self.complete = True			

		buff = self.sio.getvalue()
		self.sio = io.BytesIO() #Reset buffer 
		self.enc.SetOutput(self.sio)

		return buff

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


