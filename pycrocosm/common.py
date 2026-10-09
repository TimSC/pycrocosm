from django.conf import settings
from django.http import HttpResponse
import pgmap
import io
import json
import datetime

xmlAttribs = pgmap.mapstringstring({
	'version': str(settings.API_VERSION),
	'generator': settings.GENERATOR,
	'copyright': settings.COPYRIGHT,
	'attribution': settings.ATTRIBUTION,
	'license': settings.LICENSE})

def get_utc_posix_timestamp(dt):
	if dt.tzinfo is None:
		raise ValueError("datetime object should not be naive")

	return dt.timestamp()

def abort_transaction(t):
	try:
		t.Abort()
	except Exception:
		pass

# ****** Response formats ******
# An API request asks for JSON with a .json suffix on the path or with an
# Accept header naming application/json; otherwise the response is XML.

XML_CONTENT_TYPE = 'text/xml'
JSON_CONTENT_TYPE = 'application/json'

def wants_json(request):
	"""Whether this API request asked for a JSON response."""
	# Set by pycrocosm.formats.with_format when the path had a format suffix,
	# which takes precedence over the Accept header.
	fmt = getattr(request, 'osm_format', None)
	if fmt is not None:
		return fmt == 'json'
	return 'application/json' in request.META.get('HTTP_ACCEPT', '')

def response_content_type(request):
	return JSON_CONTENT_TYPE if wants_json(request) else XML_CONTENT_TYPE

def make_osm_encoder(request, out):
	"""An encoder writing map objects to the file object out, in the requested format."""
	if wants_json(request):
		return pgmap.PyOsmJsonEncode(out, xmlAttribs)
	return pgmap.PyOsmXmlEncode(out, xmlAttribs)

def osm_data_response(request, osmData):
	"""Respond with a set of map objects in the requested format."""
	sio = io.BytesIO()
	osmData.StreamTo(make_osm_encoder(request, sio))
	return HttpResponse(sio.getvalue(), content_type=response_content_type(request))

def json_document(members, legal=True):
	"""A JSON API document: the standard header members followed by the given ones."""
	doc = {"version": str(settings.API_VERSION), "generator": settings.GENERATOR}
	if legal:
		for key, value in (("copyright", settings.COPYRIGHT), ("attribution", settings.ATTRIBUTION),
			("license", settings.LICENSE)):
			if len(value) > 0:
				doc[key] = value
	doc.update(members)
	return doc

def json_response(members, legal=True):
	return HttpResponse(json.dumps(json_document(members, legal)), content_type=JSON_CONTENT_TYPE)
