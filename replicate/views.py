# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from __future__ import print_function

from django.shortcuts import render
from django.http import HttpResponse, HttpResponseServerError, HttpResponseNotFound
from django.conf import settings
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
		elapsedUnits = elapsed / 60
	if timebase == "hour":
		elapsedUnits = elapsed / 60 / 60
	if timebase == "day":
		elapsedUnits = elapsed / 60 / 60 / 24

	val1 = elapsedUnits / 1000 
	val2 = int(val1 / 1000)

	out = []
	for i in range(val2+1):
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

	pageStartTimestamp = int(cat1) * pageStep + epochts
	elapsedInPage = timenow - pageStartTimestamp
	if elapsedInPage < 0:
		return HttpResponseNotFound("Page does not exist")
	
	if timebase == "minute":
		elapsedInPageUnits = elapsedInPage / 60
	if timebase == "hour":
		elapsedInPageUnits = elapsedInPage / 60 / 60
	if timebase == "day":
		elapsedInPageUnits = elapsedInPage / 60 / 60 / 24

	val1 = int(elapsedInPageUnits / 1000)
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
	pageStep2 = pageStep / 1000

	pageStartTimestamp = int(cat1) * pageStep + int(cat2) * pageStep2 + epochts
	elapsedInPage = timenow - pageStartTimestamp
	if elapsedInPage < 0:
		return HttpResponseNotFound("Page does not exist")
	
	if timebase == "minute":
		val1 = elapsedInPage / 60
	if timebase == "hour":
		val1 = elapsedInPage / 60 / 60
	if timebase == "day":
		val1 = elapsedInPage / 60 / 60 / 24
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

	pageStep2 = pageStep / 1000
	pageStep3 = pageStep2 / 1000

	pageStartTimestamp = int(cat1) * pageStep + int(cat2) * pageStep2 + int(cat3) * pageStep3 + epochts
	elapsedInPage = timenow - pageStartTimestamp
	if elapsedInPage < 0:
		return HttpResponseNotFound("Page does not exist")

	t = p.GetTransaction("ACCESS SHARE")
	osmData = pgmap.OsmData()
	t.GetReplicateDiff(pageStartTimestamp-pageStep3, pageStartTimestamp, osmData)

	sio = io.BytesIO()
	enc = pgmap.PyOsmXmlEncode(sio, common.xmlAttribs)
	osmData.StreamTo(enc)
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

	pageStep2 = pageStep / 1000
	pageStep3 = pageStep2 / 1000

	pageStartTimestamp = int(cat1) * pageStep + int(cat2) * pageStep2 + int(cat3) * pageStep3 + epochts
	elapsedInPage = timenow - pageStartTimestamp
	if elapsedInPage < 0:
		return HttpResponseNotFound("Page does not exist")

	ts = datetime.datetime.utcfromtimestamp(pageStartTimestamp)

	out = []
	out.append(ts.strftime("#%a %b %d %X UTC %Y\n"))
	out.append(ts.strftime("timestamp=%Y-%m-%dT%H\\:%M\\:%SZ\n"))

	return HttpResponse(out, content_type='text/plain')

