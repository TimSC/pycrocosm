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
        chosen = [(info.editActivityId, info.atomicEditId) for info in infos
            if (info.extractId == extract_id if extract_id else info.name == name)]
        if len(chosen) != 1 or chosen[0][0] < 0 or chosen[0][1] < 0:
            return {}
        return {"edit_activity_id": str(chosen[0][0]), "atomic_edit_id": str(chosen[0][1])}

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
	# The most recent edit activity record, as an example of asking for records.
	# All of them together could be the whole history of the map.
	latest = 1
	try:
		t = get_pgmap().GetTransaction("ACCESS SHARE")
		try:
			latest = max(1, t.GetLatestEditIds()[0])
		finally:
			common.abort_transaction(t)
	except Exception:
		pass # The page is still useful without it
	return HttpResponse(
		"<p><a href='{front}'>Front page</a></p>"
		"<p><a id='changesets' href='{history}'>Recent changesets in html format</a></p>"
		"<p><a id='extracts' href='extracts'>Map extracts to download</a></p>"
		"<p><a id='planet' href='planet'>Planet dumps to download</a></p>".format(
			front=reverse('frontpage:index'), history=reverse('objectinfo:history')) +
		"<p>Diffs: <a href='minute/'>Minutely</a> <a href='hour/'>Hourly</a> <a href='day/'>Daily</a></p>"
		"<p>Edit activity API: <a id='edit_ids' href='edit_ids'>latest edit IDs</a>, "
		"<a id='edit_activities' href='edit_activities?first_id={0}&amp;last_id={0}'>the most recent record</a>. "
		"Records are asked for by a range of IDs, as in "
		"<code>edit_activities?first_id=1&amp;last_id=50</code>.</p>".format(latest))

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

def bboxes_to_et(parent, name, wkt, objTypes, objIdVers, activityId):
	"""Add the element publishing one of a record's box collections.

	It has one entry for every object of the list it goes with, in the order
	they were recorded, each saying which object it is for. A node, or a way or
	relation whose box is not known, has a null entry. A record made before
	the boxes were kept says so, with a null element.
	"""
	holder = ET.SubElement(parent, name)
	if not wkt:
		holder.set('null', 'true')
		return
	boxes = collection_boxes(wkt)
	if len(boxes) != len(objTypes):
		# Fail rather than pair boxes with the wrong objects
		raise ValueError("{} of activity {} does not match its objects".format(name, activityId))
	for objType, (objId, objVer), box in zip(objTypes, objIdVers, boxes):
		boxEl = ET.SubElement(holder, 'bbox')
		boxEl.set('type', objType)
		boxEl.set('id', str(objId))
		if box is None:
			boxEl.set('null', 'true')
		else:
			for attrib, value in zip(('minlon', 'minlat', 'maxlon', 'maxlat'), box):
				boxEl.set(attrib, repr(float(value)))

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
		activityEl.attrib['timestamp'] = utc_text(activity.timestamp)
	activityEl.attrib['action'] = str(activity.action)
	if activity.atomicEditId > 0:
		activityEl.set('atomic_edit_id', str(activity.atomicEditId))
		activityEl.set('block_index', str(activity.blockIndex))

	existingEl = ET.SubElement(activityEl, 'existing')
	for ch in existingRoot:
		existingEl.append(ch)

	# The box each existing way or relation had before the edit
	bboxes_to_et(activityEl, 'existing_bbox', activity.existingBbox,
		activity.existingType, activity.existingIdVer, activity.objId)

	updatedEl = ET.SubElement(activityEl, 'updated')
	for ch in updatedRoot:
		updatedEl.append(ch)

	# The box each updated way or relation has once the edit is made
	bboxes_to_et(activityEl, 'updated_bbox', activity.updatedBbox,
		activity.updatedType, activity.updatedIdVer, activity.objId)

	affectedparentsEl = ET.SubElement(activityEl, 'affectedparents')
	for ch in affectedparentsRoot:
		affectedparentsEl.append(ch)

	# The box of each affected parent on either side of the edit. The parent
	# keeps its version, so this is the only record of how its extent changed.
	bboxes_to_et(activityEl, 'affectedparents_before_bbox', activity.affectedparentsBeforeBbox,
		activity.affectedparentsType, activity.affectedparentsIdVer, activity.objId)
	bboxes_to_et(activityEl, 'affectedparents_after_bbox', activity.affectedparentsAfterBbox,
		activity.affectedparentsType, activity.affectedparentsIdVer, activity.objId)

	relatedEl = ET.SubElement(activityEl, 'related')
	for ch in relatedRoot:
		relatedEl.append(ch)

	# The relations holding the related nodes. A record made before these
	# were kept says so, which is not the same as there being none.
	relatedParentsEl = ET.SubElement(activityEl, 'relatedparents')
	if activity.relatedparentsRecorded:
		_, _, relatedParentsIdVers = TypeIdVerSeparate(activity.relatedparentsType, activity.relatedparentsIdVer)
		relatedParents = pgmap.OsmData()
		t.GetObjectsByIdVer("relation", relatedParentsIdVers, relatedParents)
		sio = io.BytesIO()
		relatedParents.StreamTo(pgmap.PyOsmXmlEncode(sio, common.xmlAttribs))
		for ch in ET.fromstring(sio.getvalue()):
			relatedParentsEl.append(ch)
	else:
		relatedParentsEl.set('null', 'true')

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
	doc.write(sio, "UTF-8", xml_declaration=True)

	return HttpResponse(sio.getvalue(), content_type='text/xml')

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

	try:
		id_filters = activity_id_filters(request.GET)
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

	# One more record than is sent is asked for, to know whether there are more
	maximum = edit_activity_maximum_records()
	t = get_pgmap().GetTransaction("ACCESS SHARE")

	errStr = pgmap.PgMapError()
	results = pgmap.vectorsharedptreditactivity()

	if id_filters is not None:
		t.QueryEditActivityByIds(*id_filters, results, errStr, maximum + 1)
	else:
		t.QueryEditActivityByTimestamp(timestamps['since'], timestamps['until'], results, errStr, maximum + 1)
	if errStr.errStr:
		return HttpResponseServerError(errStr.errStr, content_type='text/plain')

	resultsEl = ET.Element('editactivities')
	sent = min(results.size(), maximum)
	for i in range(sent):
		resultsEl.append(edit_activity_to_et(results[i], t))
	if results.size() > maximum:
		# There are more than one answer holds. The rest are asked for by ID,
		# starting from next_id, whichever way this request chose its records.
		resultsEl.attrib['truncated'] = 'true'
		resultsEl.attrib['next_id'] = str(results[sent - 1].objId + 1)

	#Write final xml
	doc = ET.ElementTree(resultsEl)
	sio = io.BytesIO()
	doc.write(sio, "UTF-8", xml_declaration=True)

	return HttpResponse(sio.getvalue(), content_type='text/xml')
