from django.shortcuts import render
from django.http import HttpResponse, HttpResponseServerError, HttpResponseNotFound, HttpResponseBadRequest, JsonResponse
from django.conf import settings
from django.utils.dateparse import parse_datetime, parse_date
from rest_framework.decorators import api_view, permission_classes, parser_classes
#from defusedxml.ElementTree import fromstring
import xml.etree.ElementTree as ET
from pycrocosm.mapdb import get_pgmap
from .extracts import list_db_extracts
from pycrocosm import common
import json
import pgmap
import io
import time
import datetime
import zlib
from django.http import StreamingHttpResponse
from django.views.decorators.http import require_GET


# The file formats an extract can be downloaded in, by file name extension:
# how to make the encoder, whether the encoded stream is then gzipped, and the
# content type. PBF compresses its own blocks, so it is not gzipped again.
EXTRACT_FORMATS = {
    "osm": (lambda out: pgmap.PyOsmXmlEncode(out, common.xmlAttribs), False, "application/xml"),
    "osm.gz": (lambda out: pgmap.PyOsmXmlEncode(out, common.xmlAttribs), True, "application/x-gzip"),
    "o5m.gz": (lambda out: pgmap.PyO5mEncode(out), True, "application/x-gzip"),
    "pbf": (lambda out: pgmap.PyPbfEncode(out), False, "application/octet-stream"),
}

class ExtractDownload:
    """Stream bounded encoded batches while keeping the snapshot transaction alive."""
    def __init__(self, extract_id, name, make_encoder=None):
        self.map = get_pgmap()
        self.transaction = self.map.GetTransaction("ACCESS SHARE")
        self.buffer = io.BytesIO()
        if make_encoder is None:
            make_encoder = EXTRACT_FORMATS["osm"][0]
        self.encoder = make_encoder(self.buffer)
        self.exporter = None
        try:
            self.exporter = self.transaction.StartExportExtract(extract_id, name, self.encoder)
            self.extract_id = self.exporter.GetId()
        except BaseException:
            self.close()
            raise

    def __iter__(self):
        return self

    def __next__(self):
        if self.transaction is None:
            raise StopIteration
        try:
            complete = self.exporter.Continue() == 1
            chunk = self.buffer.getvalue()
            self.buffer = io.BytesIO()
            self.encoder.SetOutput(self.buffer)
            if complete:
                self.exporter = None
                self.transaction.Commit()
                self.transaction = None
            return chunk
        except BaseException:
            self.close()
            raise

    def close(self):
        self.exporter = None
        if self.transaction is not None:
            transaction, self.transaction = self.transaction, None
            transaction.Abort()


class GzipStream:
    """Gzip a stream of byte chunks as they are produced."""
    def __init__(self, source):
        self.source = source
        self.compressor = zlib.compressobj(zlib.Z_DEFAULT_COMPRESSION, zlib.DEFLATED, zlib.MAX_WBITS | 16)

    def __iter__(self):
        return self

    def __next__(self):
        if self.compressor is None:
            raise StopIteration
        try:
            return self.compressor.compress(next(self.source))
        except StopIteration:
            compressor, self.compressor = self.compressor, None
            return compressor.flush()

    def close(self):
        self.source.close()


@require_GET
def extracts(request):
    """Public list of the stored extracts, with download links."""
    try:
        listing, error = list_db_extracts(), None
    except Exception as err:
        listing, error = [], str(err)
    return render(request, 'replicate/extracts.html', {'extracts': listing, 'error': error},
        status=500 if error else 200)


@require_GET
def download_extract(request, extract_id=None, file_format="osm"):
    """Download a stored snapshot by ID, or by a unique ?name= value.

    file_format is one of the EXTRACT_FORMATS extensions.
    """
    make_encoder, compressed, content_type = EXTRACT_FORMATS[file_format]
    name = request.GET.get("name", "")
    if extract_id is None:
        if not name.strip():
            return HttpResponseBadRequest("Specify an extract name", content_type="text/plain")
        extract_id = 0
    else:
        if name:
            return HttpResponseBadRequest("Select by ID or name, not both", content_type="text/plain")
        extract_id = int(extract_id)
        if not 0 < extract_id <= 9223372036854775807:
            return HttpResponseBadRequest("Invalid extract ID", content_type="text/plain")
    try:
        download = ExtractDownload(extract_id, name, make_encoder)
    except RuntimeError as error:
        message = str(error).removeprefix("Standard runtime exception: ")
        if message == "Extract not found":
            return HttpResponseNotFound(message, content_type="text/plain")
        if message == "Extract name is ambiguous; select by ID":
            return HttpResponseBadRequest(message, content_type="text/plain")
        raise
    response = StreamingHttpResponse(GzipStream(download) if compressed else download,
        content_type=content_type)
    response["Content-Disposition"] = 'attachment; filename="extract-{}.{}"'.format(
        download.extract_id, file_format)
    return response

def index(request):
	return HttpResponse("<a href='minute/'>Minutely</a> <a href='hour/'>Hourly</a> <a href='day/'>Daily</a>")

def catalog(request, timebase):

	timenow = int(time.time())
	epochts = int(time.mktime(settings.REPLICATE_EPOCH.timetuple()))

	elapsed = timenow - epochts
	if elapsed < 0: elapsed = 0
	
	if timebase == "minute":
		elapsedUnits = elapsed // 60
	if timebase == "hour":
		elapsedUnits = elapsed // 60 // 60
	if timebase == "day":
		elapsedUnits = elapsed // 60 // 60 // 24

	val1 = elapsedUnits // 1000 
	val2 = int(val1 // 1000)+settings.REPLICATE_OFFSET

	out = []
	for i in range(settings.REPLICATE_OFFSET, val2+1):
		out.append("<a href='{0:03d}/'>{0:03d}</a><br/>".format(i))

	return HttpResponse(out)

def catalog2(request, timebase, cat1):

	timenow = int(time.time())
	epochts = int(time.mktime(settings.REPLICATE_EPOCH.timetuple()))

	if timebase == "minute":
		pageStep = 60000000
	if timebase == "hour":
		pageStep = 60000000 * 60
	if timebase == "day":
		pageStep = 60000000 * 60 * 24

	pageStartTimestamp = (int(cat1)-settings.REPLICATE_OFFSET) * pageStep + epochts
	elapsedInPage = timenow - pageStartTimestamp
	if elapsedInPage < 0:
		return HttpResponseNotFound("Page does not exist")
	
	if timebase == "minute":
		elapsedInPageUnits = elapsedInPage // 60
	if timebase == "hour":
		elapsedInPageUnits = elapsedInPage // 60 // 60
	if timebase == "day":
		elapsedInPageUnits = elapsedInPage // 60 // 60 // 24

	val1 = int(elapsedInPageUnits // 1000)
	if val1 > 999: val1 = 999

	out = []
	for i in range(val1+1):
		out.append("<a href='{0:03d}/'>{0:03d}</a><br/>".format(i))

	return HttpResponse(out)

def catalog3(request, timebase, cat1, cat2):

	timenow = int(time.time())
	epochts = int(time.mktime(settings.REPLICATE_EPOCH.timetuple()))

	if timebase == "minute":
		pageStep = 60000000
	if timebase == "hour":
		pageStep = 60000000 * 60
	if timebase == "day":
		pageStep = 60000000 * 60 * 24
	pageStep2 = pageStep // 1000

	pageStartTimestamp = (int(cat1)-settings.REPLICATE_OFFSET) * pageStep + int(cat2) * pageStep2 + epochts
	elapsedInPage = timenow - pageStartTimestamp
	if elapsedInPage < 0:
		return HttpResponseNotFound("Page does not exist")
	
	if timebase == "minute":
		val1 = elapsedInPage // 60
	if timebase == "hour":
		val1 = elapsedInPage // 60 // 60
	if timebase == "day":
		val1 = elapsedInPage // 60 // 60 // 24
	val1 = int(val1)
	if val1 > 999: val1 = 999

	out = []
	for i in range(val1+1):
		out.append("<a href='{0:03d}.osc'>{0:03d}.osc</a> <a href='{0:03d}.osc.gz'>{0:03d}.osc.gz</a> ".format(i))
		out.append("<a href='{0:03d}.state.txt'>{0:03d}.state.txt</a><br/>".format(i))

	return HttpResponse(out)

def getoscdiff(timebase, cat1, cat2, cat3):

	timenow = int(time.time())
	epochts = int(time.mktime(settings.REPLICATE_EPOCH.timetuple()))

	if timebase == "minute":
		pageStep = 60000000
	if timebase == "hour":
		pageStep = 60000000 * 60
	if timebase == "day":
		pageStep = 60000000 * 60 * 24

	pageStep2 = pageStep // 1000
	pageStep3 = pageStep2 // 1000

	pageStartTimestamp = (int(cat1)-settings.REPLICATE_OFFSET) * pageStep + int(cat2) * pageStep2 + int(cat3) * pageStep3 + epochts
	elapsedInPage = timenow - pageStartTimestamp
	if elapsedInPage < 0:
		return HttpResponseNotFound("Page does not exist")

	t = get_pgmap().GetTransaction("EXCLUSIVE")
	osmc = pgmap.OsmChange()
	t.GetReplicateDiff(pageStartTimestamp-pageStep3, pageStartTimestamp, osmc)

	sio = io.BytesIO()
	pgmap.SaveToOsmChangeXml(osmc, pgmap.CPyOutbuf(sio), False)
	return sio.getvalue()

def diff(request, timebase, cat1, cat2, cat3):
	data = getoscdiff(timebase, cat1, cat2, cat3)
	if isinstance(data, HttpResponse):
		return data
	return HttpResponse(data, content_type='text/xml')

def diffgz(request, timebase, cat1, cat2, cat3):
	data = getoscdiff(timebase, cat1, cat2, cat3)
	if isinstance(data, HttpResponse):
		return data
	comp = zlib.compressobj(zlib.Z_DEFAULT_COMPRESSION, zlib.DEFLATED, zlib.MAX_WBITS | 16)
	gzip_data = comp.compress(data) + comp.flush()
	return HttpResponse(gzip_data, content_type='application/x-gzip')
	
def state(request, timebase, cat1, cat2, cat3):

	timenow = int(time.time())
	epochts = int(time.mktime(settings.REPLICATE_EPOCH.timetuple()))

	if timebase == "minute":
		pageStep = 60000000
	if timebase == "hour":
		pageStep = 60000000 * 60
	if timebase == "day":
		pageStep = 60000000 * 60 * 24

	pageStep2 = pageStep // 1000
	pageStep3 = pageStep2 // 1000

	pageStartTimestamp = (int(cat1)-settings.REPLICATE_OFFSET) * pageStep + int(cat2) * pageStep2 + int(cat3) * pageStep3 + epochts
	elapsedInPage = timenow - pageStartTimestamp
	if elapsedInPage < 0:
		return HttpResponseNotFound("Page does not exist")

	ts = datetime.datetime.utcfromtimestamp(pageStartTimestamp)

	out = []
	out.append(ts.strftime("#%a %b %d %X UTC %Y\n"))
	out.append(ts.strftime("timestamp=%Y-%m-%dT%H\\:%M\\:%SZ\n"))

	return HttpResponse(out, content_type='text/plain')

def TimestampToPath(ts, timebase):
	
	epochts = int(time.mktime(settings.REPLICATE_EPOCH.timetuple()))
	ts2 = ts - epochts
	
	if timebase == "minute":
		pageStep3 = 60
	if timebase == "hour":
		pageStep3 = 60 * 60
	if timebase == "day":
		pageStep3 = 60 * 60 * 24

	pageStep2 = pageStep3 * 1000
	pageStep = pageStep2 * 1000

	a = ts2 % pageStep3
	ts2 -= a #Discard seconds
	b = ts2 % pageStep2
	cat3 = b // pageStep3 + 1
	ts2 -= b # Remove 1000 minutes blocks
	c = ts2 % pageStep
	cat2 = c // pageStep2
	ts2 -= c # Remove 1,000,000 minutes blocks
	cat1 = ts2 // pageStep + settings.REPLICATE_OFFSET

	return (cat1, cat2, cat3)

def customdiff(request):
	#This is a non-standard (pycrocosm specific) API call to get diffs of custom time ranges.
	now = datetime.datetime.now(datetime.timezone.utc)

	startTsArg = request.GET.get('start', None) #Normally ISO 8601
	endTsArg = request.GET.get('end', None) #Normally ISO 8601
	compress = request.GET.get('compress', 'no')

	if startTsArg is None:
		return HttpResponseBadRequest("start argument not set")
	startTs=parse_datetime(startTsArg)
	if startTs is None:
		startTs=parse_date(startTsArg)
		startTs=datetime.datetime(startTs.year, startTs.month, startTs.day)

	if endTsArg is None:
		return HttpResponseBadRequest("end argument not set")
	endTs=parse_datetime(endTsArg)
	if endTs is None:
		endTs=parse_date(endTsArg)
		endTs=datetime.datetime(endTs.year, endTs.month, endTs.day)

	if startTs is None:
		return HttpResponseBadRequest("start argument not understood (should be ISO 8601 date or datetime)")
	if endTs is None:
		return HttpResponseBadRequest("end argument not understood (should be ISO 8601 date or datetime)")

	startTs = startTs.replace(tzinfo=datetime.timezone.utc)
	endTs = endTs.replace(tzinfo=datetime.timezone.utc)

	if endTs < startTs:
		return HttpResponseBadRequest("end cannot be before start")
	if endTs > now:
		return HttpResponseBadRequest("end cannot be in the future")
	if (endTs - startTs).total_seconds() > settings.REPLICATE_DIFF_MAXIMUM_SECONDS:
		return HttpResponseBadRequest("requested diff range is too large")

	t = get_pgmap().GetTransaction("EXCLUSIVE")
	osmc = pgmap.OsmChange()
	t.GetReplicateDiff(int(time.mktime(startTs.timetuple())), int(time.mktime(endTs.timetuple())), osmc)

	sio = io.BytesIO()
	pgmap.SaveToOsmChangeXml(osmc, pgmap.CPyOutbuf(sio), False)

	if compress == 'no':
		return HttpResponse(sio.getvalue(), content_type='text/xml')
	if compress == 'gz':
		comp = zlib.compressobj(zlib.Z_DEFAULT_COMPRESSION, zlib.DEFLATED, zlib.MAX_WBITS | 16)
		gzip_data = comp.compress(sio.getvalue()) + comp.flush()
		return HttpResponse(gzip_data, content_type='application/x-gzip')

	return HttpResponseBadRequest("compression argument not understood")

def timenow(request):

	now = datetime.datetime.now(datetime.timezone.utc)

	return JsonResponse({'now': now, 'time': now.timestamp()})

def TypeIdVerSeparate(types, idVers):
	nodeIdVers, wayIdVers, relationIdVers = [], [], []
	for objType, (objId, ObjVer) in zip(types, idVers):
		if objType == "node":
			nodeIdVers.append(pgmap.pairi64i64(objId, ObjVer))
		if objType == "way":
			wayIdVers.append(pgmap.pairi64i64(objId, ObjVer))
		if objType == "relation":
			relationIdVers.append(pgmap.pairi64i64(objId, ObjVer))

	return nodeIdVers, wayIdVers, relationIdVers

def sync_context_to_et(parent, name, references, t):
	"""Expand versioned references, preserving their geometry component order."""
	element = ET.SubElement(parent, name)
	if not references:
		element.set('null', 'true')
		return
	refs = json.loads(references)
	data = pgmap.OsmData()
	for obj_type in ('node', 'way', 'relation'):
		id_versions = [pgmap.pairi64i64(obj_id, version)
			for kind, obj_id, version in refs if kind == obj_type]
		if id_versions:
			t.GetObjectsByIdVer(obj_type, id_versions, data)
	sio = io.BytesIO()
	data.StreamTo(pgmap.PyOsmXmlEncode(sio, common.xmlAttribs))
	objects = {(obj.tag, int(obj.attrib['id']), int(obj.attrib['version'])): obj
		for obj in ET.fromstring(sio.getvalue()) if obj.tag in ('node', 'way', 'relation')}
	for kind, obj_id, version in refs:
		# Fail rather than return an incomplete collection with shifted alignment.
		element.append(objects[(kind, obj_id, version)])

def edit_activity_to_et(activity, t):
	#Get relevent objects from database
	existingNodeIdVers, existingWayIdVers, existingRelationIdVers = TypeIdVerSeparate(activity.existingType, activity.existingIdVer)
	updatedNodeIdVers, updatedWayIdVers, updatedRelationIdVers = TypeIdVerSeparate(activity.updatedType, activity.updatedIdVer)
	affectedparentsNodeIdVers, affectedparentsWayIdVers, affectedparentsRelationIdVers = TypeIdVerSeparate(activity.affectedparentsType, activity.affectedparentsIdVer)
	relatedNodeIdVers, relatedWayIdVers, relatedRelationIdVers = TypeIdVerSeparate(activity.relatedType, activity.relatedIdVer)

	existing = pgmap.OsmData()
	t.GetObjectsByIdVer("node", existingNodeIdVers, existing)
	t.GetObjectsByIdVer("way", existingWayIdVers, existing)
	t.GetObjectsByIdVer("relation", existingRelationIdVers, existing)

	updated = pgmap.OsmData()
	t.GetObjectsByIdVer("node", updatedNodeIdVers, updated)
	t.GetObjectsByIdVer("way", updatedWayIdVers, updated)
	t.GetObjectsByIdVer("relation", updatedRelationIdVers, updated)

	affectedparents = pgmap.OsmData()
	t.GetObjectsByIdVer("node", affectedparentsNodeIdVers, affectedparents)
	t.GetObjectsByIdVer("way", affectedparentsWayIdVers, affectedparents)
	t.GetObjectsByIdVer("relation", affectedparentsRelationIdVers, affectedparents)

	related = pgmap.OsmData()
	t.GetObjectsByIdVer("node", relatedNodeIdVers, related)
	t.GetObjectsByIdVer("way", relatedWayIdVers, related)
	t.GetObjectsByIdVer("relation", relatedRelationIdVers, related)

	#Save objects to xml then read xml back into python ET
	sio = io.BytesIO()
	enc = pgmap.PyOsmXmlEncode(sio, common.xmlAttribs)
	existing.StreamTo(enc)
	existingRoot = ET.fromstring(sio.getvalue())

	sio = io.BytesIO()
	enc = pgmap.PyOsmXmlEncode(sio, common.xmlAttribs)
	updated.StreamTo(enc)
	updatedRoot = ET.fromstring(sio.getvalue())

	sio = io.BytesIO()
	enc = pgmap.PyOsmXmlEncode(sio, common.xmlAttribs)
	affectedparents.StreamTo(enc)
	affectedparentsRoot = ET.fromstring(sio.getvalue())

	sio = io.BytesIO()
	enc = pgmap.PyOsmXmlEncode(sio, common.xmlAttribs)
	related.StreamTo(enc)
	relatedRoot = ET.fromstring(sio.getvalue())

	#Organize output
	activityEl = ET.Element('editactivity')
	activityEl.attrib['id'] = str(activity.objId)
	if activity.changeset > 0:
		activityEl.attrib['changeset'] = str(activity.changeset)
	if activity.timestamp > 0:
		activityEl.attrib['timestamp'] = datetime.datetime.fromtimestamp(activity.timestamp).isoformat()
	activityEl.attrib['action'] = str(activity.action)
	if activity.atomicEditId > 0:
		activityEl.set('atomic_edit_id', str(activity.atomicEditId))
		activityEl.set('block_index', str(activity.blockIndex))

	sync_context_to_et(activityEl, 'sync_before', activity.syncBefore, t)
	sync_context_to_et(activityEl, 'sync_after', activity.syncAfter, t)
	for name, geometry in [('bbox_before', activity.bboxBefore), ('bbox_after', activity.bboxAfter)]:
		geometry_el = ET.SubElement(activityEl, name)
		if geometry:
			geometry_el.set('format', 'wkt')
			geometry_el.set('srid', '4326')
			geometry_el.text = geometry
		else:
			geometry_el.set('null', 'true')


	existingEl = ET.SubElement(activityEl, 'existing')
	for ch in existingRoot:
		existingEl.append(ch)

	updatedEl = ET.SubElement(activityEl, 'updated')
	for ch in updatedRoot:
		updatedEl.append(ch)

	affectedparentsEl = ET.SubElement(activityEl, 'affectedparents')
	for ch in affectedparentsRoot:
		affectedparentsEl.append(ch)

	relatedEl = ET.SubElement(activityEl, 'related')
	for ch in relatedRoot:
		relatedEl.append(ch)

	return activityEl

@api_view(['GET'])
def get_edit_activity(request, objId):

	if not 0 < int(objId) <= 9223372036854775807:
		return HttpResponseBadRequest("Activity ID is out of range")

	t = get_pgmap().GetTransaction("ACCESS SHARE")

	errStr = pgmap.PgMapError()
	activity = pgmap.EditActivity()

	found = t.GetEditActivityById(int(objId), 
		activity,
		errStr)
	if errStr.errStr:
		return HttpResponseServerError(errStr.errStr, content_type='text/plain')
	if not found:
		return HttpResponseNotFound("Edit activity does not exist")

	resultsEl = ET.Element('editactivities')

	activityEl = edit_activity_to_et(activity, t)
	resultsEl.append(activityEl)

	#Write final xml
	doc = ET.ElementTree(resultsEl)
	sio = io.BytesIO()
	doc.write(sio, "UTF-8")

	return HttpResponse(sio.getvalue(), content_type='text/xml')

def activity_id_filters(params):
	keys = ('id', 'first_id', 'last_id', 'atomic_edit_id')
	values = {}
	for key in keys:
		if key in params:
			value = params[key]
			if not value or not value.isascii() or not value.isdecimal():
				raise ValueError(key + ' must be a positive integer')
			value = int(value)
			if not 0 < value <= 9223372036854775807:
				raise ValueError(key + ' is out of range')
			values[key] = value
	if not values:
		return None
	if any(key in params for key in ('since', 'until')):
		raise ValueError('ID filters cannot be combined with timestamp filters')
	if 'id' in values and any(key in values for key in ('first_id', 'last_id')):
		raise ValueError('Use id or a row range, not both')
	first = values.get('id', values.get('first_id', 0))
	last = values.get('id', values.get('last_id', 0))
	if last and last < first:
		raise ValueError('last_id must be greater than or equal to first_id')
	return first, last, values.get('atomic_edit_id', 0)

@api_view(['GET'])
def query_edit_activity_by_timestamp(request):

	try:
		id_filters = activity_id_filters(request.GET)
	except ValueError as error:
		return HttpResponseBadRequest(str(error))

	sinceTimestamp = request.GET.get('since', None)
	untilTimestamp = request.GET.get('until', None)

	if sinceTimestamp is not None:
		sinceTimestamp=parse_datetime(sinceTimestamp)
		sinceTimestamp = sinceTimestamp.replace(tzinfo=datetime.timezone.utc)
		sinceTimestamp = int(time.mktime(sinceTimestamp.timetuple()))
	else:
		sinceTimestamp = 0

	if untilTimestamp is not None:
		untilTimestamp=parse_datetime(untilTimestamp)
		untilTimestamp = untilTimestamp.replace(tzinfo=datetime.timezone.utc)
		untilTimestamp = int(time.mktime(untilTimestamp.timetuple()))
	else:
		untilTimestamp = 0

	t = get_pgmap().GetTransaction("ACCESS SHARE")

	errStr = pgmap.PgMapError()
	results = pgmap.vectorsharedptreditactivity()

	if id_filters is not None:
		t.QueryEditActivityByIds(*id_filters, results, errStr)
	else:
		t.QueryEditActivityByTimestamp(sinceTimestamp, untilTimestamp, results, errStr)
	if errStr.errStr:
		return HttpResponseServerError(errStr.errStr, content_type='text/plain')

	resultsEl = ET.Element('editactivities')

	test = []
	for i in range(len(results)):
		test.append(results[i])

	for i in range(results.size()):
		activity = results[i]
		activityEl = edit_activity_to_et(activity, t)
		resultsEl.append(activityEl)

	#Write final xml
	doc = ET.ElementTree(resultsEl)
	sio = io.BytesIO()
	doc.write(sio, "UTF-8")

	return HttpResponse(sio.getvalue(), content_type='text/xml')
