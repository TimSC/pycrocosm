# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.shortcuts import render
from django.http import HttpResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from rest_framework.permissions import IsAuthenticated, IsAuthenticatedOrReadOnly
from rest_framework.decorators import api_view, permission_classes

import xml.etree.ElementTree as ET
import cStringIO

# Create your views here.

@csrf_exempt
@api_view(['PUT'])
@permission_classes((IsAuthenticated, ))
def create(request):
	return HttpResponse("1", content_type='text/plain')

@csrf_exempt
@api_view(['GET', 'PUT'])
@permission_classes((IsAuthenticatedOrReadOnly, ))
def changeset(request, changesetId):

	root = ET.Element('osm')
	doc = ET.ElementTree(root)
	root.attrib["version"] = str(settings.API_VERSION)
	root.attrib["generator"] = settings.GENERATOR

	changeset = ET.SubElement(root, "changeset")
	changeset.attrib["id"] = str(changesetId)
	changeset.attrib["user"] = "fred"
	changeset.attrib["uid"] = "123"
	changeset.attrib["created_at"] = "2008-11-08T19:07:39+01:00"
	changeset.attrib["open"] = "true"
	changeset.attrib["min_lon"] = "7.0191821"
	changeset.attrib["min_lat"] = "49.2785426"
	changeset.attrib["max_lon"] = "7.0197485"
	changeset.attrib["max_lat"] = "49.2793101"

	tag = ET.SubElement(root, "tag")
	tag.attrib["k"] = "comment"
	tag.attrib["v"] = "Just adding some streetnames"

	discussion = ET.SubElement(root, "discussion")

	comment = ET.SubElement(discussion, "comment")
	comment.attrib["data"] = "2015-01-01T18:56:48Z"
	comment.attrib["uid"] = "1841"
	comment.attrib["user"] = "metaodi"

	text = ET.SubElement(comment, "text")
	text.text = "Did you verify those street names?"

	sio = cStringIO.StringIO()
	doc.write(sio, "utf8")
	return HttpResponse(sio.getvalue(), content_type='text/xml')

@csrf_exempt
@api_view(['PUT'])
@permission_classes((IsAuthenticated, ))
def close(request, changesetId):
	return HttpResponse("", content_type='text/plain')

@api_view(['GET'])
@permission_classes((IsAuthenticatedOrReadOnly, ))
def download(request, changesetId):
	return get(request, changesetId)

@csrf_exempt
@api_view(['POST'])
@permission_classes((IsAuthenticated, ))
def expand_bbox(request, changesetId):
		
	return get(request, changesetId)

@api_view(['GET'])
@permission_classes((IsAuthenticatedOrReadOnly, ))
def list(request):
	return HttpResponse("", content_type='text/xml')

@csrf_exempt
@api_view(['POST'])
@permission_classes((IsAuthenticated, ))
def upload(request, changesetId):
	return HttpResponse("", content_type='text/xml')

@csrf_exempt
@api_view(['POST'])
@permission_classes((IsAuthenticated, ))
def comment(request, changesetId):
	return HttpResponse("", content_type='text/xml')

@csrf_exempt
@api_view(['POST'])
@permission_classes((IsAuthenticated, ))
def subscribe(request, changesetId):
	return HttpResponse("", content_type='text/xml')

@csrf_exempt
@api_view(['POST'])
@permission_classes((IsAuthenticated, ))
def unsubscribe(request, changesetId):
	return HttpResponse("", content_type='text/xml')

