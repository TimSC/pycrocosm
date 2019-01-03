# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from __future__ import print_function

from django.shortcuts import render
from django.http import HttpResponse, HttpResponseServerError, HttpResponseNotFound, HttpResponseBadRequest
from django.conf import settings
from django.utils.dateparse import parse_datetime, parse_date
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

	t = p.GetTransaction("ACCESS SHARE")
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

	startTsArg = request.GET.get('start', None) #Normally ISO 8601
	endTsArg = request.GET.get('end', None) #Normally ISO 8601
	compress = request.GET.get('compress', 'no')
	if startTsArg is None:
		return HttpResponseBadRequest("start argument not set")
	if endTsArg is None:
		return HttpResponseBadRequest("end argument not set")
	startTs=parse_datetime(startTsArg)
	if startTs is None:
		startTs=parse_date(startTsArg)
	endTs=parse_datetime(endTsArg)
	if endTs is None:
		endTs=parse_date(endTsArg)
	if startTs is None:
		return HttpResponseBadRequest("start argument not understood (should be ISO 8601 date or datetime)")
	if endTs is None:
		return HttpResponseBadRequest("end argument not understood (should be ISO 8601 date or datetime)")
	if endTs < startTs:
		return HttpResponseBadRequest("end cannot be before start")

	t = p.GetTransaction("ACCESS SHARE")
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

