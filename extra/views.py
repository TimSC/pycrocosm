# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from __future__ import print_function

from django.shortcuts import render
from django.http import HttpResponse, HttpResponseBadRequest
from django.utils.dateparse import parse_datetime
from django.contrib.auth.models import User
from querymap.views import p
from rest_framework.decorators import api_view, permission_classes, parser_classes
from pycrocosm.parsers import DefusedXmlParser, OsmChangeXmlParser
from pycrocosm import common
import xml.etree.ElementTree as ET
import pgmap
import io
import time

@api_view(['GET'])
def most_active_users(request):
	timeNow = time.time()
	
	dt = request.GET.get('start', None)
	if dt is None:
		return HttpResponseBadRequest("start must be specified as an argument", content_type="text/plain")
	timearg = int(round(common.get_utc_posix_timestamp(parse_datetime(dt))))

	if timearg is not None:
		timearg = int(timearg)
		if timearg < timeNow - 31*24*60*60:
			return HttpResponseBadRequest("Timestamp too far into the past (must be within 31 days)", content_type="text/plain")

	t = p.GetTransaction("ACCESS SHARE")

	uids = pgmap.vectori64()
	objectCount = pgmap.vectorvectori64()

	t.GetMostActiveUsers(timearg, uids, objectCount)

	responseRoot = ET.Element('activeusers')
	doc = ET.ElementTree(responseRoot)

	for i in range(uids.size()):
		user = User.objects.get(id=uids[i])

		userEl = ET.SubElement(responseRoot, "user")
		userEl.attrib['uid'] = str(uids[i])
		if user is not None:
			userEl.attrib['username'] = str(user.username)
		userEl.attrib['nodes'] = str(objectCount[i][0])
		userEl.attrib['ways'] = str(objectCount[i][1])
		userEl.attrib['relations'] = str(objectCount[i][2])

	sio = io.BytesIO()
	doc.write(sio, str("UTF-8")) # str work around https://bugs.python.org/issue15811
	return HttpResponse(sio.getvalue(), content_type='text/xml')

