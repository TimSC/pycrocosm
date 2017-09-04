# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import xml.etree.ElementTree as ET
import cStringIO

from django.shortcuts import render
from django.http import HttpResponse
from django.conf import settings

# Create your views here.

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

	sio = cStringIO.StringIO()
	doc.write(sio, "utf8")
	return HttpResponse(sio.getvalue(), content_type='text/xml')

def permissions(request):
	root = ET.Element('osm')
	doc = ET.ElementTree(root)
	root.attrib["version"] = str(settings.API_VERSION)
	root.attrib["generator"] = settings.GENERATOR

	permissions = ET.SubElement(root, "permissions")

	sio = cStringIO.StringIO()
	doc.write(sio, "utf8")
	return HttpResponse(sio.getvalue(), content_type='text/xml')

