# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from __future__ import print_function

from django.test import TestCase
from django.test import Client
from django.urls import reverse
from django.contrib.auth.models import User

from defusedxml.ElementTree import parse, fromstring

import pgmap
from changeset.tests import CreateTestChangeset, ParseOsmDiffToDict, GetObj
from querymap.views import p

class OverpassTestCase(TestCase):

	def setUp(self):
		self.username = "john"
		self.password = "glass onion"
		self.email = 'jlennon@beatles.com'
		self.user = User.objects.create_user(self.username, self.email, self.password)
		self.client = Client()
		self.client.login(username=self.username, password=self.password)

		t = p.GetTransaction("EXCLUSIVE")
		errStr = pgmap.PgMapError()
		self.schemaVersion = int(t.GetMetaValue("schema_version", errStr))

	def test_upload_create_way(self):

		if self.schemaVersion < 12:
			return #Skip test if schema is not up to date

		cs = CreateTestChangeset(self.user, tags={"foo": "invade"}, is_open=True)

		xml = """<osmChange generator="JOSM" version="0.6">
		<create>
		  <node changeset="{0}" id="-5393" lat="50.79046578105" lon="-1.04971367626" />
		  <node changeset="{0}" id="-5394" lat="50.81" lon="-1.051" />
		  <way changeset="{0}" id="-434">
		   <tag k="note" v="Just a way"/>
		   <nd ref="-5393"/>
		   <nd ref="-5394"/>
		  </way>
		</create>
		</osmChange>""".format(cs.objId)

		response = self.client.post(reverse('changeset:upload', args=(cs.objId,)), xml, 
			content_type='text/xml')
		if response.status_code != 200:
			print (response.content)
		self.assertEqual(response.status_code, 200)

		xml = fromstring(response.content)
		self.assertEqual(len(xml), 3)
		diffDict = ParseOsmDiffToDict(xml)
		self.assertEqual(-5393 in diffDict["node"], True)
		self.assertEqual(-5394 in diffDict["node"], True)
		self.assertEqual(-434 in diffDict["way"], True)
		
		newWayId, newWayVersion = diffDict["way"][-434]

		self.assertEqual(newWayVersion, 1)
		newWay = GetObj(p, "way", newWayId)
		self.assertEqual(newWay is not None, True)
		for ref in list(newWay.refs):
			self.assertEqual(ref > 0, True)

		t = p.GetTransaction("EXCLUSIVE")
		bbox = pgmap.vectord()
		t.GetObjectCachedBbox("way", diffDict["way"][-434][0], bbox)
		bbox = list(bbox)
		self.assertEqual(len(bbox), 4)
		self.assertEqual(abs(bbox[0]+1.051)<1e-6, True)
		self.assertEqual(abs(bbox[1]-50.7904658)<1e-6, True)
		self.assertEqual(abs(bbox[2]+1.04971368)<1e-6, True)
		self.assertEqual(abs(bbox[3]-50.81)<1e-6, True)

	def tearDown(self):
		u = User.objects.get(username = self.username)
		u.delete()

		errStr = pgmap.PgMapError()
		t = p.GetTransaction("EXCLUSIVE")
		ok = t.ResetActiveTables(errStr)
		if not ok:
			print (errStr.errStr)
		t.Commit()

