# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.shortcuts import render
from django.http import HttpResponse, HttpResponseServerError, HttpResponseNotFound
from django.conf import settings
from querymap.views import p
import pgmap
import io
import time
import datetime

def index(request):
	return HttpResponse("<a href='minute/'>Minutely</a>")

def catalog(request, timebase):
	if timebase != "minute":
		return HttpResponseServerError("Not implemented yet")

	timenow = int(time.time())
	epochts = int(time.mktime(settings.REPLICATE_EPOCH.timetuple()))

	elapsed = timenow - epochts
	if elapsed < 0: elapsed = 0
	
	elapsedMin = elapsed / 60
	val1 = elapsedMin / 1000 
	val2 = val1 / 1000

	out = []
	for i in range(val2+1):
		out.append("<a href='{0:03d}/'>{0:03d}</a><br/>".format(i))

	return HttpResponse(out)

def catalog2(request, timebase, cat1):
	if timebase != "minute":
		return HttpResponseServerError("Not implemented yet")

	timenow = int(time.time())
	epochts = int(time.mktime(settings.REPLICATE_EPOCH.timetuple()))

	pageStartTimestamp = int(cat1) * 60000000 + epochts
	elapsedInPage = timenow - pageStartTimestamp
	if elapsedInPage < 0:
		return HttpResponseNotFound("Page does not exist")
	
	elapsedInPageMin = elapsedInPage / 60
	val1 = elapsedInPageMin / 1000 
	if val1 > 999: val1 = 999

	out = []
	for i in range(val1+1):
		out.append("<a href='{0:03d}/'>{0:03d}</a><br/>".format(i))

	return HttpResponse(out)

def catalog3(request, timebase, cat1, cat2):
	if timebase != "minute":
		return HttpResponseServerError("Not implemented yet")

	timenow = int(time.time())
	epochts = int(time.mktime(settings.REPLICATE_EPOCH.timetuple()))

	pageStartTimestamp = int(cat1) * 60000000 + int(cat2) * 60000 + epochts
	elapsedInPage = timenow - pageStartTimestamp
	if elapsedInPage < 0:
		return HttpResponseNotFound("Page does not exist")
	
	val1 = elapsedInPage / 60
	if val1 > 999: val1 = 999

	out = []
	for i in range(val1):
		out.append("<a href='{0:03d}.osc'>{0:03d}.osc</a><br/>".format(i))
		out.append("<a href='{0:03d}.state.txt'>{0:03d}.state.txt</a><br/>".format(i))

	return HttpResponse(out)

def diff(request, timebase, cat1, cat2, cat3):
	if timebase != "minute":
		return HttpResponseServerError("Not implemented yet")

	timenow = int(time.time())
	epochts = int(time.mktime(settings.REPLICATE_EPOCH.timetuple()))

	pageStartTimestamp = int(cat1) * 60000000 + int(cat2) * 60000 + int(cat3) * 60 + epochts
	elapsedInPage = timenow - pageStartTimestamp
	if elapsedInPage < 0:
		return HttpResponseNotFound("Page does not exist")

	t = p.GetTransaction(b"ACCESS SHARE")
	osmData = pgmap.OsmData()
	t.GetReplicateDiff(pageStartTimestamp-60, pageStartTimestamp, osmData)

	sio = io.BytesIO()
	enc = pgmap.PyOsmXmlEncode(sio)
	osmData.StreamTo(enc)
	return HttpResponse(sio.getvalue(), content_type='text/xml')

def state(request, timebase, cat1, cat2, cat3):
	if timebase != "minute":
		return HttpResponseServerError("Not implemented yet")

	timenow = int(time.time())
	epochts = int(time.mktime(settings.REPLICATE_EPOCH.timetuple()))

	pageStartTimestamp = int(cat1) * 60000000 + int(cat2) * 60000 + int(cat3) * 60 + epochts
	elapsedInPage = timenow - pageStartTimestamp
	if elapsedInPage < 0:
		return HttpResponseNotFound("Page does not exist")

	ts = datetime.datetime.utcfromtimestamp(pageStartTimestamp)

	return HttpResponse(ts.strftime("#%a %b %d %X UTC %Y"), content_type='text/plain')

