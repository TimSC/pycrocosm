from django.shortcuts import render
from django.http import HttpResponse, HttpResponseServerError, HttpResponseNotFound, HttpResponseBadRequest, JsonResponse
from django.conf import settings
from django.urls import reverse
from django.utils.dateparse import parse_datetime, parse_date
from rest_framework.decorators import api_view, permission_classes, parser_classes
#from defusedxml.ElementTree import fromstring
import xml.etree.ElementTree as ET
from pycrocosm.mapdb import get_pgmap
from .extracts import list_db_extracts, list_db_extracts_with_latest
from pycrocosm import common
import os
import re
import pgmap
from urllib.parse import quote
import io
import math
from xml.sax.saxutils import quoteattr
import time
import datetime
import zlib
from django.http import StreamingHttpResponse
from django.views.decorators.http import require_GET


# The file formats an extract can be downloaded in, by file name extension:
# how to make the encoder, whether the encoded stream is then gzipped, and the
# content type. PBF compresses its own blocks, so it is not gzipped again.
# Each encoder is made for an output and the extract's edit IDs: the edit
# activity ID and atomic edit ID it is current to, which go in the file header
# so that whoever holds the file can tell, and can update from there. PBF has
# nowhere to put them.
def xml_extract_encoder(out, edit_ids):
    attribs = dict(common.xmlAttribs)
    attribs.update(edit_ids)
    return pgmap.PyOsmXmlEncode(out, pgmap.mapstringstring(attribs))

EXTRACT_FORMATS = {
    "osm": (xml_extract_encoder, False, "application/xml"),
    "osm.gz": (xml_extract_encoder, True, "application/x-gzip"),
    "o5m.gz": (lambda out, edit_ids: pgmap.PyO5mEncode(out, pgmap.mapstringstring(edit_ids)),
        True, "application/x-gzip"),
    "pbf": (lambda out, edit_ids: pgmap.PyPbfEncode(out), False, "application/octet-stream"),
}

class ExtractDownload:
    """Stream bounded encoded batches while keeping the snapshot transaction alive."""
    def __init__(self, extract_id, name, make_encoder=None):
        self.map = get_pgmap()
        self.transaction = self.map.GetTransaction("ACCESS SHARE")
        self.buffer = io.BytesIO()
        if make_encoder is None:
            make_encoder = EXTRACT_FORMATS["osm"][0]
        self.encoder = None
        self.exporter = None
        try:
            self.encoder = make_encoder(self.buffer, self.edit_ids(extract_id, name))
            self.exporter = self.transaction.StartExportExtract(extract_id, name, self.encoder)
            self.extract_id = self.exporter.GetId()
        except BaseException:
            self.close()
            raise

    def edit_ids(self, extract_id, name):
        """The checkpoint of the extract about to be exported, as header attributes.

        Read in the transaction that exports it, so the two agree. Empty if the
        extract has no checkpoint yet, or is not found: the export itself
        reports an extract that is missing or whose name is ambiguous.
        """
        infos = pgmap.vectorextractinfo()
        self.transaction.ListExtracts(infos)
        chosen = [(info.editActivityId, info.atomicEditId, info.rectangular, info.boundaryWkt) for info in infos
            if (info.extractId == extract_id if extract_id else info.name == name)]
        if len(chosen) != 1:
            return {}
        attribs = {}
        if chosen[0][0] >= 0 and chosen[0][1] >= 0:
            attribs.update({"edit_activity_id": str(chosen[0][0]), "atomic_edit_id": str(chosen[0][1])})
        # A boundary that is not a rectangle, which the file's bounds cannot hold
        if not chosen[0][2]:
            attribs["boundary"] = chosen[0][3]
        return attribs

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


# Names a finished dump can have: pgmap's formats, optionally gzipped. A dump
# still being written has a temporary name, which does not end like this.
PLANET_DUMP_NAME = re.compile(r"^[^/]+\.(osm|o5m|pbf|json)(\.gz)?$")

def list_planet_dumps():
    """The finished dumps in PLANET_DUMP_DIR, newest first: name, size, time and address of each."""
    folder = settings.PLANET_DUMP_DIR
    base = settings.PLANET_DUMP_URL
    if not base.endswith("/"):
        base += "/"
    dumps = []
    try:
        names = os.listdir(folder)
    except FileNotFoundError:
        names = [] # No dump has been made yet
    for name in names:
        path = os.path.join(folder, name)
        if name.startswith(".") or not PLANET_DUMP_NAME.match(name) or not os.path.isfile(path):
            continue
        status = os.stat(path)
        dumps.append({
            "name": name,
            "size": status.st_size,
            "written_at": datetime.datetime.fromtimestamp(status.st_mtime, datetime.timezone.utc),
            "url": base + quote(name),
        })
    dumps.sort(key=lambda dump: (dump["written_at"], dump["name"]), reverse=True)
    return dumps

@require_GET
def planet_dumps(request):
    """Public list of the planet dumps made by "manage.py dumpplanet", with download links."""
    try:
        dumps, error = list_planet_dumps(), None
    except OSError as err:
        dumps, error = [], str(err)
    return render(request, 'replicate/planet.html', {'dumps': dumps, 'error': error},
        status=500 if error else 200)

@require_GET
def extracts(request):
    """Public list of the stored extracts, with download links."""
    try:
        (listing, latest), error = list_db_extracts_with_latest(), None
    except Exception as err:
        listing, latest, error = [], None, str(err)
    return render(request, 'replicate/extracts.html',
        {'extracts': listing, 'latest': latest, 'error': error}, status=500 if error else 200)


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
	return HttpResponse(
		"<p><a href='{front}'>Front page</a></p>"
		"<p><a id='changesets' href='{history}'>Recent changesets in html format</a></p>"
		"<p><a id='extracts' href='extracts'>Map extracts to download</a></p>"
		"<p><a id='planet' href='planet'>Planet dumps to download</a></p>".format(
			front=reverse('frontpage:index'), history=reverse('objectinfo:history')) +
		"<p>Diffs: <a href='minute/'>Minutely</a> <a href='hour/'>Hourly</a> <a href='day/'>Daily</a></p>"
		"<p>Edit activity API: <a id='edit_ids' href='edit_ids'>latest edit IDs</a>, "
		"<a id='edit_activities' href='edit_activities'>the most recent records</a>. "
		"Records can be asked for by a range of IDs, as in "
		"<code>edit_activities?first_id=1&amp;last_id=50</code>, and by the area they concern, "
		"as in <code>edit_activities?bbox=-1.1,50.7,-1.0,50.8</code>.</p>")

def catalog(request, timebase):

	timenow = int(time.time())
	epochts = utc_epoch(settings.REPLICATE_EPOCH)

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
	epochts = utc_epoch(settings.REPLICATE_EPOCH)

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
	epochts = utc_epoch(settings.REPLICATE_EPOCH)

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
	epochts = utc_epoch(settings.REPLICATE_EPOCH)

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
	epochts = utc_epoch(settings.REPLICATE_EPOCH)

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

	ts = datetime.datetime.fromtimestamp(pageStartTimestamp, datetime.timezone.utc)

	out = []
	out.append(ts.strftime("#%a %b %d %X UTC %Y\n"))
	out.append(ts.strftime("timestamp=%Y-%m-%dT%H\\:%M\\:%SZ\n"))

	return HttpResponse(out, content_type='text/plain')

def TimestampToPath(ts, timebase):
	
	epochts = utc_epoch(settings.REPLICATE_EPOCH)
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
	t.GetReplicateDiff(utc_epoch(startTs), utc_epoch(endTs), osmc)

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

def collection_boxes(wkt):
	"""The extent of each geometry of a GeometryCollection given as WKT, in order:
	(min x, min y, max x, max y), or None for an empty geometry."""
	wkt = wkt.strip()
	if wkt.upper().startswith("SRID="):
		wkt = wkt.split(";", 1)[1].strip()
	if not wkt.upper().startswith("GEOMETRYCOLLECTION"):
		raise ValueError("not a geometry collection")
	body = wkt[len("GEOMETRYCOLLECTION"):].strip()
	if body.upper() == "EMPTY":
		return []
	body = body[1:-1]
	# Split at the commas between geometries, which are the ones outside any brackets
	parts, depth, start = [], 0, 0
	for i, ch in enumerate(body):
		if ch == "(":
			depth += 1
		elif ch == ")":
			depth -= 1
		elif ch == "," and depth == 0:
			parts.append(body[start:i])
			start = i + 1
	parts.append(body[start:])
	boxes = []
	for part in parts:
		numbers = [float(v) for v in re.findall(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?", part)]
		if len(numbers) == 0:
			boxes.append(None)
			continue
		xs, ys = numbers[0::2], numbers[1::2]
		boxes.append((min(xs), min(ys), max(xs), max(ys)))
	return boxes

def activity_object_lists(activity):
	"""The lists of object references a record holds, as (section name, types, ID/version pairs)."""
	lists = [('existing', activity.existingType, activity.existingIdVer),
		('updated', activity.updatedType, activity.updatedIdVer),
		('affectedparents', activity.affectedparentsType, activity.affectedparentsIdVer),
		('related', activity.relatedType, activity.relatedIdVer)]
	if activity.relatedparentsRecorded:
		lists.append(('relatedparents', activity.relatedparentsType, activity.relatedparentsIdVer))
	return [(name, list(types), [tuple(pair) for pair in idVers]) for name, types, idVers in lists]

def fetch_activity_objects(lists, t):
	"""Read every object the given records refer to, each kind in one request.

	Returns the XML element of each as text, by (type, ID, version). The same
	object is often named by several records, and is read and encoded once.
	"""
	wanted = {"node": set(), "way": set(), "relation": set()}
	for recordLists in lists:
		for name, types, idVers in recordLists:
			for objType, idVer in zip(types, idVers):
				wanted[objType].add(idVer)

	fragments = {}
	for objType in ("node", "way", "relation"):
		if len(wanted[objType]) == 0:
			continue
		data = pgmap.OsmData()
		t.GetObjectsByIdVer(objType, [pgmap.pairi64i64(*idVer) for idVer in sorted(wanted[objType])], data)
		objs = {"node": data.nodes, "way": data.ways, "relation": data.relations}[objType]
		for i in range(objs.size()):
			obj = objs[i]
			key = (objType, obj.objId, obj.metaData.version)
			if key not in fragments:
				fragments[key] = pgmap.OsmObjectXml(obj)
	return fragments

def bboxes_xml(name, wkt, objTypes, objIdVers, activityId):
	"""The element publishing one of a record's box collections.

	It has one entry for every object of the list it goes with, in the order
	they were recorded, each saying which object it is for. A node's entry is
	its position, with the minimum and maximum the same. A deleted object has
	a null entry. Where the collection was not recorded, because the record is
	older than this or the box of one of its objects was not known, the
	element itself is null.
	"""
	if not wkt:
		return ['<', name, ' null="true"/>']
	boxes = collection_boxes(wkt)
	if len(boxes) != len(objTypes):
		# Fail rather than pair boxes with the wrong objects
		raise ValueError("{} of activity {} does not match its objects".format(name, activityId))
	out = ['<', name, '>']
	for objType, (objId, objVer), box in zip(objTypes, objIdVers, boxes):
		out.append('<bbox type={} id="{}"'.format(quoteattr(objType), objId))
		if box is None:
			out.append(' null="true"/>')
		else:
			out.append(' minlon="{!r}" minlat="{!r}" maxlon="{!r}" maxlat="{!r}"/>'.format(*(float(v) for v in box)))
	out.extend(['</', name, '>'])
	return out

def edit_activity_xml(activity, lists, fragments):
	"""One record as pieces of XML text, its objects taken from fragments."""
	out = ['<editactivity id="{}"'.format(activity.objId)]
	if activity.changeset > 0:
		out.append(' changeset="{}"'.format(activity.changeset))
	if activity.timestamp > 0:
		out.append(' timestamp={}'.format(quoteattr(utc_text(activity.timestamp))))
	out.append(' action={}'.format(quoteattr(str(activity.action))))
	if activity.atomicEditId > 0:
		out.append(' atomic_edit_id="{}" block_index="{}"'.format(activity.atomicEditId, activity.blockIndex))
	out.append('>')

	# The boxes that go with each list: those of the existing objects before
	# the edit, of the updated objects once it is made, and of the affected
	# parents on either side of it. A parent keeps its version, so those are
	# the only record of how its extent changed.
	boxes = {'existing': [('existing_bbox', activity.existingBbox)],
		'updated': [('updated_bbox', activity.updatedBbox)],
		'affectedparents': [('affectedparents_before_bbox', activity.affectedparentsBeforeBbox),
			('affectedparents_after_bbox', activity.affectedparentsAfterBbox)]}

	for name, types, idVers in lists:
		out.extend(['<', name, '>'])
		written = set()
		for objType, (objId, objVer) in zip(types, idVers):
			key = (objType, objId, objVer)
			if key in fragments and key not in written:
				written.add(key)
				out.append(fragments[key])
		out.extend(['</', name, '>'])
		for boxName, wkt in boxes.get(name, []):
			out.extend(bboxes_xml(boxName, wkt, types, idVers, activity.objId))

	# The relations holding the related nodes. A record made before these
	# were kept says so, which is not the same as there being none.
	if not activity.relatedparentsRecorded:
		out.append('<relatedparents null="true"/>')

	out.append('</editactivity>')
	return out

def edit_activities_response(activities, t, rootAttribs=None):
	"""Publish records as one XML document.

	The objects of all the records are read together, and written into the
	response as the XML text the encoder produces for them.
	"""
	lists = [activity_object_lists(activity) for activity in activities]
	fragments = fetch_activity_objects(lists, t)

	out = ["<?xml version='1.0' encoding='UTF-8'?>\n<editactivities"]
	for name, value in (rootAttribs or {}).items():
		out.append(' {}={}'.format(name, quoteattr(value)))
	out.append('>')
	for activity, recordLists in zip(activities, lists):
		out.extend(edit_activity_xml(activity, recordLists, fragments))
	out.append('</editactivities>')

	return HttpResponse("".join(out).encode("utf-8"), content_type='text/xml')

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

	return edit_activities_response([activity], t)

def utc_epoch(moment):
	"""Seconds since 1970 for a datetime, taking one without a zone as UTC.

	Written so as not to depend on the server's own time zone, which
	time.mktime would bring in.
	"""
	if moment.tzinfo is None:
		moment = moment.replace(tzinfo=datetime.timezone.utc)
	return int(moment.timestamp())

def utc_text(timestamp):
	return datetime.datetime.fromtimestamp(timestamp, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def edit_activity_maximum_records():
	"""The most edit activity records one request is answered with."""
	return max(1, getattr(settings, 'EDIT_ACTIVITY_MAXIMUM_RECORDS', 100))

def edit_activity_default_records():
	"""How many of the latest records a request that does not say which it wants is answered with."""
	return min(edit_activity_maximum_records(), max(1, getattr(settings, 'EDIT_ACTIVITY_DEFAULT_RECORDS', 20)))

def activity_bbox_filter(params):
	"""The bbox parameter as [min lon, min lat, max lon, max lat], or None if it was not given."""
	if 'bbox' not in params:
		return None
	try:
		bbox = [float(v) for v in params['bbox'].split(",")]
	except ValueError:
		bbox = []
	if len(bbox) != 4 or not all(math.isfinite(v) for v in bbox):
		raise ValueError('bbox must be four numbers: min lon, min lat, max lon, max lat')
	if bbox[0] > bbox[2] or bbox[1] > bbox[3]:
		raise ValueError('bbox has a minimum greater than its maximum')
	if bbox[0] < -180 or bbox[2] > 180 or bbox[1] < -90 or bbox[3] > 90:
		raise ValueError('bbox is outside the world')
	return bbox

def activity_limit(params):
	"""The limit parameter, or None if it was not given."""
	if 'limit' not in params:
		return None
	value = params['limit']
	maximum = edit_activity_maximum_records()
	if not value or not value.isascii() or not value.isdecimal() or not 0 < int(value) <= maximum:
		raise ValueError('limit must be a whole number from 1 to {}'.format(maximum))
	return int(value)

@api_view(['GET'])
def latest_edit_ids(request):
	"""The latest edit activity ID and atomic edit ID of this map: where anything
	following its edit activity has to reach to be up to date."""
	t = get_pgmap().GetTransaction("ACCESS SHARE")
	try:
		latest = t.GetLatestEditIds()
		root = ET.Element('editids')
		root.attrib['edit_activity_id'] = str(latest[0])
		root.attrib['atomic_edit_id'] = str(latest[1])
	finally:
		common.abort_transaction(t)
	sio = io.BytesIO()
	ET.ElementTree(root).write(sio, "UTF-8", xml_declaration=True)
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

	"""Edit activity records, in ID order.

	They are chosen by a range of IDs (id, first_id, last_id, atomic_edit_id)
	or of times (since, until). bbox keeps those that may concern an area: the
	records whose existing objects, updated objects, or affected parents before
	or after the edit reach into it, along with any that do not say where
	these are.
	Without a range, the latest records are sent: 20 unless limit says
	otherwise. With one, records are sent from the start of the range, up to
	the limit, and an answer that was cut short says where to go on from.
	"""
	try:
		id_filters = activity_id_filters(request.GET)
		bbox = activity_bbox_filter(request.GET)
		limit = activity_limit(request.GET)
	except ValueError as error:
		return HttpResponseBadRequest(str(error))

	# since and until are times, taken as UTC unless they name an offset
	timestamps = {}
	for key in ('since', 'until'):
		text = request.GET.get(key, None)
		moment = None
		if text is not None:
			try:
				moment = parse_datetime(text)
			except ValueError:
				moment = None
			if moment is None:
				return HttpResponseBadRequest(key + ' must be a date and time such as 2026-01-31T12:00:00Z')
		timestamps[key] = utc_epoch(moment) if moment is not None else 0

	# Without a range of IDs or times, the newest records are wanted
	newest = id_filters is None and 'since' not in request.GET and 'until' not in request.GET
	if limit is None:
		limit = edit_activity_default_records() if newest else edit_activity_maximum_records()
	bboxArg = pgmap.vectord(bbox if bbox is not None else [])

	t = get_pgmap().GetTransaction("ACCESS SHARE")

	errStr = pgmap.PgMapError()
	results = pgmap.vectorsharedptreditactivity()

	# Working through a range, one more record than is sent is asked for, to
	# know whether there are more
	if id_filters is not None:
		t.QueryEditActivityByIds(*id_filters, results, errStr, limit + 1, bboxArg, False)
	elif newest:
		t.QueryEditActivityByTimestamp(0, 0, results, errStr, limit, bboxArg, True)
	else:
		t.QueryEditActivityByTimestamp(timestamps['since'], timestamps['until'], results, errStr, limit + 1, bboxArg, False)
	if errStr.errStr:
		return HttpResponseServerError(errStr.errStr, content_type='text/plain')

	sent = min(results.size(), limit)
	rootAttribs = {}
	if results.size() > limit:
		# There are more than one answer holds. The rest are asked for by ID,
		# starting from next_id, whichever way this request chose its records.
		rootAttribs['truncated'] = 'true'
		rootAttribs['next_id'] = str(results[sent - 1].objId + 1)

	return edit_activities_response([results[i] for i in range(sent)], t, rootAttribs)
