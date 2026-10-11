from django.shortcuts import render
from django.http import HttpResponse, HttpResponseBadRequest, StreamingHttpResponse
from django.conf import settings
from django.utils.dateparse import parse_datetime
from rest_framework.decorators import api_view, permission_classes, parser_classes
from django.views.decorators.gzip import gzip_page
from django.views.decorators.csrf import csrf_exempt
from rest_framework.permissions import IsAuthenticated, IsAuthenticatedOrReadOnly
from rest_framework.negotiation import BaseContentNegotiation
from pycrocosm import common
from pycrocosm.mapdb import get_pgmap
import pgmap
import io
import random
import time
import zlib

# The formats the map can be asked for with the format argument: how the
# encoder is made, whether the output is then gzipped, the content type, and
# whether the format is text, which can take padding at the end.
MAP_FORMATS = {
	"osm": (lambda out: pgmap.PyOsmXmlEncode(out, common.xmlAttribs), False, common.XML_CONTENT_TYPE, True),
	"osm.gz": (lambda out: pgmap.PyOsmXmlEncode(out, common.xmlAttribs), True, "application/x-gzip", False),
	"json": (lambda out: pgmap.PyOsmJsonEncode(out, common.xmlAttribs), False, common.JSON_CONTENT_TYPE, True),
	"json.gz": (lambda out: pgmap.PyOsmJsonEncode(out, common.xmlAttribs), True, "application/x-gzip", False),
	"o5m": (lambda out: pgmap.PyO5mEncode(out), False, "application/octet-stream", False),
	"o5m.gz": (lambda out: pgmap.PyO5mEncode(out), True, "application/x-gzip", False),
	"pbf": (lambda out: pgmap.PyPbfEncode(out), False, "application/octet-stream", False),
}

class IgnoreFormatArgument(BaseContentNegotiation):
	"""Leaves the format argument to the view.

	Django REST framework otherwise takes a format argument as the name of one
	of its own renderers, and answers 404 when it is not one.
	"""
	def select_parser(self, request, parsers):
		return parsers[0] if parsers else None

	def select_renderer(self, request, renderers, format_suffix=None):
		return (renderers[0], renderers[0].media_type)

class MapQueryResponse:
	def __init__(self, bbox, request, file_format=None):
		self.sio = io.BytesIO()
		self.compressor = None
		self.text = True
		if file_format is None:
			# XML, or JSON if the request asked for that
			self.enc = common.make_osm_encoder(request, self.sio)
		else:
			make_encoder, compressed, content_type, self.text = MAP_FORMATS[file_format]
			self.enc = make_encoder(self.sio)
			if compressed:
				self.compressor = zlib.compressobj(zlib.Z_DEFAULT_COMPRESSION, zlib.DEFLATED, zlib.MAX_WBITS | 16)
		
		#Don't let map or transaction objects go out of scope while query is running
		self.p = get_pgmap()
		self.t = self.p.GetTransaction("ACCESS SHARE")
		self.mapQuery = self.t.GetQueryMgr()
		timestamp = time.time()
		if self.mapQuery.Start(bbox, int(timestamp), self.enc)<0:
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
			#Finish the query.
			self.complete = True
			abp = False
			if hasattr(settings, 'ENABLE_ANTI_BREACH_PADDING'):
				abp = settings.ENABLE_ANTI_BREACH_PADDING
			if self.compressor is not None:
				return self.compressor.compress(self.sio.getvalue()) + self.compressor.flush()
			if abp and self.text:
				#Add random whitespace to the end to confuse BREACH attacks				
				whitespace = []
				for i in range(random.randint(0, 256)):
					whitespace.append(random.choice((b'\n',b' ',b'\t')))
				return self.sio.getvalue() + b"".join(whitespace)
			else:
				return self.sio.getvalue()

		buff = self.sio.getvalue()
		self.sio = io.BytesIO() #Reset buffer 
		self.enc.SetOutput(self.sio)

		if self.compressor is not None:
			return self.compressor.compress(buff)
		return buff

	def __next__(self):
		return self.next()

@gzip_page #Control gzip on a per-page basis because of BREACH vun. This page contains no secrets.
@csrf_exempt #Contain no secrets to avoid BREACH vun (and this page is never POSTed anyway).
@api_view(['GET'])
def index(request):
	"""The map within a bounding box: bbox=left,bottom,right,top.

	format chooses how it is written: osm (XML), json, o5m or pbf, and osm.gz,
	json.gz or o5m.gz for a gzipped file. Without it the answer is XML, or
	JSON if the request's Accept header asks for that.
	"""
	bbox = request.GET.get('bbox', None)
	if bbox is None:
		return HttpResponseBadRequest("Bbox must be specified as an argument", content_type="text/plain")

	file_format = request.GET.get('format', None)
	if file_format is not None and file_format not in MAP_FORMATS:
		return HttpResponseBadRequest("format must be one of: " + ", ".join(sorted(MAP_FORMATS)), content_type="text/plain")

	bbox = list(map(float, bbox.split(",")))
	if len(bbox) != 4:
		return HttpResponseBadRequest("Invalid bbox", content_type="text/plain")

	#left,bottom,right,top
	dLon = bbox[2] - bbox[0]
	dLat = bbox[3] - bbox[1]
	area = dLon * dLat

	if (bbox[0] < -180.0 or bbox[0] > 180.0 or
		bbox[1] < -90.0 or bbox[1] > 90.0 or
		bbox[2] < -180.0 or bbox[2] > 180.0 or
		bbox[3] < -90.0 or bbox[3] > 90.0 or
		bbox[0] > bbox[2] or bbox[1] > bbox[3]):

		err = "The latitudes must be between -90 and 90, longitudes between -180 and 180 and the minima must be less than the maxima."
		response = HttpResponseBadRequest(err, content_type="text/plain")
		response["Error"] = err
		return response

	if area > settings.AREA_MAXIMUM:
		err = "The maximum bbox size is {}, and your request was too large. Either request a smaller area, or use planet.osm".format(settings.AREA_MAXIMUM)
		response = HttpResponseBadRequest(err, content_type="text/plain")
		response["Error"] = err
		return response

	if file_format is None:
		return StreamingHttpResponse(iter(MapQueryResponse(bbox, request)),
			content_type=common.response_content_type(request))

	make_encoder, compressed, content_type, text = MAP_FORMATS[file_format]
	response = StreamingHttpResponse(iter(MapQueryResponse(bbox, request, file_format)), content_type=content_type)
	if not text:
		# A file to keep, which is not to be compressed again on its way:
		# pbf is compressed within, and the gzipped formats already are
		response["Content-Disposition"] = 'attachment; filename="map.{}"'.format(file_format)
		if compressed or file_format == "pbf":
			response["Content-Encoding"] = "identity"
	return response

# The view has a format argument of its own
index.cls.content_negotiation_class = IgnoreFormatArgument

@gzip_page #Control gzip on a per-page basis because of BREACH vun. This page contains no secrets.
@csrf_exempt #Contain no secrets to avoid BREACH vun (and this page is never POSTed anyway).
@api_view(['GET'])
def historic_map(request):
	bbox = request.GET.get('bbox', None)
	if bbox is None:
		return HttpResponseBadRequest("bbox must be specified as an argument", content_type="text/plain")

	dt = request.GET.get('datetime', None)
	print (dt)
	if dt is None:
		return HttpResponseBadRequest("datetime must be specified as an argument", content_type="text/plain")
	dt2 = int(round(common.get_utc_posix_timestamp(parse_datetime(dt))))

	bbox = list(map(float, bbox.split(",")))
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

	t = get_pgmap().GetTransaction("ACCESS SHARE")

	sio = io.BytesIO()
	enc = common.make_osm_encoder(request, sio)
	ret = t.GetHistoricMapQuery(bbox, dt2, enc)

	t.Commit()

	return HttpResponse(sio.getvalue(), content_type=common.response_content_type(request))
