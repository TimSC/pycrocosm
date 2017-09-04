# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.shortcuts import render
from django.http import HttpResponse
from django.contrib.auth.decorators import login_required
from django.conf import settings

import xml.etree.ElementTree as ET
import cStringIO

# Create your views here.

@login_required
def details(request):

	root = ET.Element('osm')
	doc = ET.ElementTree(root)
	root.attrib["version"] = str(settings.API_VERSION)
	root.attrib["generator"] = settings.GENERATOR

	user = ET.SubElement(root, "user")
	user.attrib["display_name"] = "Max Muster"
	user.attrib["account_created"] = "2006-07-21T19:28:26Z"
	user.attrib["id"] = "1234"

	cts = ET.SubElement(user, "contributor-terms")
	cts.attrib["agreed"] = "false"
	cts.attrib["pd"] = "false"

	roles = ET.SubElement(user, "roles")

	changesets = ET.SubElement(user, "changesets")
	changesets.attrib["count"] = "12345"

	traces = ET.SubElement(user, "traces")
	traces.attrib["count"] = "12345"

	blocks = ET.SubElement(user, "blocks")
	received = ET.SubElement(blocks, "received")
	received.attrib["count"] = "0"
	received.attrib["active"] = "0"

	home = ET.SubElement(user, "home")
	home.attrib["lat"] = "0.0"
	home.attrib["lon"] = "0.0"
	home.attrib["zoom"] = "3"

	description = ET.SubElement(user, "description")
	description.text = "The description of your profile"

	languages = ET.SubElement(user, "languages")
	lang = ET.SubElement(languages, "lang")
	lang.text="en"

	messages = ET.SubElement(user, "messages")
	msgReceived = ET.SubElement(messages, "received")
	msgReceived.attrib["count"] = "1"
	msgReceived.attrib["unread"] = "0"
	msgSent = ET.SubElement(messages, "sent")
	msgSent.attrib["count"] = "1"

	sio = cStringIO.StringIO()
	doc.write(sio, "utf8")
	return HttpResponse(sio.getvalue(), content_type='text/xml')

@login_required
def preferences(request):

	root = ET.Element('osm')
	doc = ET.ElementTree(root)
	root.attrib["version"] = str(settings.API_VERSION)
	root.attrib["generator"] = settings.GENERATOR

	preferences = ET.SubElement(root, "preferences")

	preference = ET.SubElement(preferences, "preference")
	preference.attrib["k"] = "some-key"
	preference.attrib["v"] = "some-value"

	sio = cStringIO.StringIO()
	doc.write(sio, "utf8")
	return HttpResponse(sio.getvalue(), content_type='text/xml')

