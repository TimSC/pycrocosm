# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from __future__ import print_function

import xml.etree.ElementTree as ET
import sys
import io

from django.shortcuts import render
from django.http import HttpResponse
from django.conf import settings
from pycrocosm import common
from changeset.views import get_changeset_query_limits
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, IsAuthenticatedOrReadOnly

# Create your views here.

def query_limits():
	"""The limits advertised by the capabilities call that have defaults.

	The notes limits describe the notes API, which this server does not have
	yet; they are advertised because editors expect to find them.
	"""
	changesetsDefault, changesetsMaximum = get_changeset_query_limits()
	return {
		"changesets_default": changesetsDefault,
		"changesets_maximum": changesetsMaximum,
		"notes_default": getattr(settings, 'NOTES_DEFAULT_QUERY_LIMIT', 100),
		"notes_maximum": getattr(settings, 'NOTES_MAXIMUM_QUERY_LIMIT', 10000),
		"relationmembers": settings.RELATION_MEMBERS_MAXIMUM,
	}

@api_view(['GET'])
def capabilities(request):

	limits = query_limits()

	if common.wants_json(request):
		return common.json_response({
			"api": {
				"version": {"minimum": str(settings.API_VERSION), "maximum": str(settings.API_VERSION)},
				"area": {"maximum": settings.AREA_MAXIMUM},
				"note_area": {"maximum": settings.NOTE_AREA_MAXIMUM},
				"tracepoints": {"per_page": settings.TRACEPOINTS_PER_PAGE},
				"waynodes": {"maximum": settings.WAYNODES_MAXIMUM},
				"changesets": {"maximum_elements": settings.CHANGESETS_MAXIMUM_ELEMENTS,
					"default_query_limit": limits["changesets_default"],
					"maximum_query_limit": limits["changesets_maximum"]},
				"notes": {"default_query_limit": limits["notes_default"],
					"maximum_query_limit": limits["notes_maximum"]},
				"relationmembers": {"maximum": limits["relationmembers"]},
				"timeout": {"seconds": settings.TIMEOUT_SECONDS},
				"status": {"database": settings.STATUS_DATABASE, "api": settings.STATUS_API,
					"gpx": settings.STATUS_GPX},
			},
			"policy": {
				"imagery": {"blacklist": [{"regex": bl} for bl in settings.POLICY_IMAGERY_BLACKLIST]},
			},
		})

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
	changesets.attrib["default_query_limit"] = str(limits["changesets_default"])
	changesets.attrib["maximum_query_limit"] = str(limits["changesets_maximum"])
	notes = ET.SubElement(api, "notes")
	notes.attrib["default_query_limit"] = str(limits["notes_default"])
	notes.attrib["maximum_query_limit"] = str(limits["notes_maximum"])
	relationmembers = ET.SubElement(api, "relationmembers")
	relationmembers.attrib["maximum"] = str(limits["relationmembers"])
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
@permission_classes((IsAuthenticatedOrReadOnly, ))
def permissions(request):
	if common.wants_json(request):
		granted = list(request.auth) if request.auth is not None else []
		return common.json_response({"permissions": granted}, legal=False)

	root = ET.Element('osm')
	doc = ET.ElementTree(root)
	root.attrib["version"] = str(settings.API_VERSION)
	root.attrib["generator"] = settings.GENERATOR

	permissions = ET.SubElement(root, "permissions")

	if request.auth is not None:
		for perm in request.auth:
			pe = ET.SubElement(permissions, "permission")
			pe.attrib["name"] = perm

	sio = io.BytesIO()
	doc.write(sio, "UTF-8")
	return HttpResponse(sio.getvalue(), content_type='text/xml')

@api_view(['GET'])
def versions(request):
	"""GET /api/versions: the API versions this server supports."""
	if common.wants_json(request):
		# The legal members are not part of this document in JSON
		return common.json_response({"api": {"versions": [str(settings.API_VERSION)]}}, legal=False)

	root = ET.Element('osm')
	doc = ET.ElementTree(root)
	root.attrib["generator"] = settings.GENERATOR
	if(len(settings.COPYRIGHT)>0): root.attrib["copyright"] = settings.COPYRIGHT
	if(len(settings.ATTRIBUTION)>0): root.attrib["attribution"] = settings.ATTRIBUTION
	if(len(settings.LICENSE)>0): root.attrib["license"] = settings.LICENSE
	api = ET.SubElement(root, "api")
	version = ET.SubElement(api, "version")
	version.text = str(settings.API_VERSION)

	sio = io.BytesIO()
	doc.write(sio, "UTF-8")
	return HttpResponse(sio.getvalue(), content_type='text/xml')

@api_view(['GET'])
def apibase(request):
	return HttpResponse("API root", content_type='text/plain')

@api_view(['GET', 'POST'])
def not_implemented(request):
	return HttpResponse("Not implemented", status=501)

