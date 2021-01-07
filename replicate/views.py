# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from __future__ import print_function

from django.shortcuts import render
from django.http import HttpResponse, HttpResponseServerError, HttpResponseNotFound, HttpResponseBadRequest, JsonResponse
from django.conf import settings
from django.utils.dateparse import parse_datetime, parse_date
from rest_framework.decorators import api_view, permission_classes, parser_classes
#from defusedxml.ElementTree import fromstring
import xml.etree.ElementTree as ET
from querymap.views import p
from pycrocosm import common
import pgmap
import io
import time
import datetime
import zlib

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

	t = p.GetTransaction("EXCLUSIVE")
	osmc = pgmap.OsmChange()
	t.GetReplicateDiff(pageStartTimestamp-pageStep3, pageStartTimestamp, osmc)

	sio = io.BytesIO()
	pgmap.SaveToOsmChangeXml(osmc, False, pgmap.CPyOutbuf(sio))
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

	if endTsArg is None:
		return HttpResponseBadRequest("end argument not set")
	endTs=parse_datetime(endTsArg)
	if endTs is None:
		endTs=parse_date(endTsArg)		

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

	t = p.GetTransaction("EXCLUSIVE")
	osmc = pgmap.OsmChange()
	t.GetReplicateDiff(int(time.mktime(startTs.timetuple())), int(time.mktime(endTs.timetuple())), osmc)

	sio = io.BytesIO()
	pgmap.SaveToOsmChangeXml(osmc, False, pgmap.CPyOutbuf(sio))

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

@api_view(['GET'])
def get_edit_activity(request, objId):

	t = p.GetTransaction("ACCESS SHARE")

	existingType = pgmap.vectorstring()
	existingIdVer = pgmap.vectorpairi64i64()
	updatedType = pgmap.vectorstring()
	updatedIdVer = pgmap.vectorpairi64i64()
	affectedparentsType = pgmap.vectorstring()
	affectedparentsIdVer = pgmap.vectorpairi64i64()
	relatedType = pgmap.vectorstring()
	relatedIdVer = pgmap.vectorpairi64i64()

	errStr = pgmap.PgMapError()
	t.GetEditActivity(int(objId), 
		existingType,
		existingIdVer,
		updatedType,
		updatedIdVer,
		affectedparentsType,
		affectedparentsIdVer,
		relatedType,
		relatedIdVer,
		errStr)

	#Get relevent objects from database
	existingNodeIdVers, existingWayIdVers, existingRelationIdVers = TypeIdVerSeparate(existingType, existingIdVer)
	updatedNodeIdVers, updatedWayIdVers, updatedRelationIdVers = TypeIdVerSeparate(updatedType, updatedIdVer)
	affectedparentsNodeIdVers, affectedparentsWayIdVers, affectedparentsRelationIdVers = TypeIdVerSeparate(affectedparentsType, affectedparentsIdVer)
	relatedNodeIdVers, relatedWayIdVers, relatedRelationIdVers = TypeIdVerSeparate(relatedType, relatedIdVer)

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

	#Read xml back into python ET
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

	#Encode output
	root = ET.Element('editactivity')
	root.attrib['id'] = objId
	existingEl = ET.SubElement(root, 'existing')
	for ch in existingRoot:
		existingEl.append(ch)

	updatedEl = ET.SubElement(root, 'updated')
	for ch in updatedRoot:
		updatedEl.append(ch)

	affectedparentsEl = ET.SubElement(root, 'affectedparents')
	for ch in affectedparentsRoot:
		affectedparentsEl.append(ch)

	relatedEl = ET.SubElement(root, 'related')
	for ch in relatedRoot:
		relatedEl.append(ch)

	#Write final xml
	doc = ET.ElementTree(root)
	sio = io.BytesIO()
	doc.write(sio, str("UTF-8")) # str work around https://bugs.python.org/issue15811

	return HttpResponse(sio.getvalue(), content_type='text/plain')

