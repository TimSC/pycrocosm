# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from __future__ import print_function

import xml.etree.ElementTree as ET
import sys
import io

from django.shortcuts import render
from django.http import HttpResponse
from django.conf import settings
from rest_framework.decorators import api_view

# Create your views here.

@api_view(['GET'])
def capabilities(request):

	root = ET.Element('osm')
	doc = ET.ElementTree(root)
	root.attrib["version"] = str(settings.API_VERSION)
	root.attrib["generator"] = settings.GENERATOR
	if(len(settings.COPYRIGHT)>0): root.attrib["copyright"] = settings.COPYRIGHT
	if(len(settings.ATTRIBUTION)>0): root.attrib["attribution"] = settings.ATTRIBUTION
	if(len(settings.LICENSE)>0): root.attrib["license"] = settings.LICENSE

	api = ET.SubElement(root, "api")
	version = ET.SubElement(api, "version")
	version.attrib["minimum"] = str(settings.API_VERSION)
	version.attrib["maximum"] = str(settings.API_VERSION)
	area = 	ET.SubElement(api, "area")
	area.attrib["maximum"] = str(settings.AREA_MAXIMUM)
	note_area = ET.SubElement(api, "note_area")
	note_area.attrib["maximum"] = str(settings.NOTE_AREA_MAXIMUM)
	tracepoints = ET.SubElement(api, "tracepoints")
	tracepoints.attrib["per_page"] = str(settings.TRACEPOINTS_PER_PAGE)
	waynodes = ET.SubElement(api, "waynodes")
	waynodes.attrib["maximum"] = str(settings.WAYNODES_MAXIMUM)
	changesets = ET.SubElement(api, "changesets")
	changesets.attrib["maximum_elements"] = str(settings.CHANGESETS_MAXIMUM_ELEMENTS)
	timeout = ET.SubElement(api, "timeout")
	timeout.attrib["seconds"] = str(settings.TIMEOUT_SECONDS)
	status = ET.SubElement(api, "status")
	status.attrib["database"] = settings.STATUS_DATABASE
	status.attrib["api"] = settings.STATUS_API
	status.attrib["gpx"] = settings.STATUS_GPX
	
	policy = ET.SubElement(root, "policy")
	imagery = ET.SubElement(policy, "imagery")
	for bl in settings.POLICY_IMAGERY_BLACKLIST:
		blacklist = ET.SubElement(imagery, "blacklist")
		blacklist.attrib["regex"] = bl

	sio = io.BytesIO()
	doc.write(sio, "UTF-8")
	return HttpResponse(sio.getvalue(), content_type='text/xml')

@api_view(['GET'])
def permissions(request):
	root = ET.Element('osm')
	doc = ET.ElementTree(root)
	root.attrib["version"] = str(settings.API_VERSION)
	root.attrib["generator"] = settings.GENERATOR

	permissions = ET.SubElement(root, "permissions")

	sio = io.BytesIO()
	doc.write(sio, "UTF-8")
	return HttpResponse(sio.getvalue(), content_type='text/xml')

@api_view(['GET'])
def apibase(request):
	return HttpResponse("API root", content_type='text/plain')

@api_view(['GET', 'POST'])
def not_implemented(request):
	return HttpResponse("Not implemented", status=501)

