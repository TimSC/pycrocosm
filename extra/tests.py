# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.test import TestCase
from django.test import Client
from django.urls import reverse
from django.contrib.auth.models import User

from changeset.tests import CreateTestChangeset, GetObj, ParseOsmDiffToDict
from querymap.tests import DecodeOsmdataResponse
from changeset.views import GetOsmDataIndex
from defusedxml.ElementTree import parse, fromstring
from querymap.views import p
import pgmap
import sys
import gc

# Create your tests here.

class ExtraFunctionsTestCase(TestCase):

	def setUp(self):
		self.username = "john"
		self.password = "glass onion"
		self.email = 'jlennon@beatles.com'
		self.user = User.objects.create_user(self.username, self.email, self.password)
		self.client = Client()
		self.client.login(username=self.username, password=self.password)

		self.xmlSimpleWay = """<osmChange generator="JOSM" version="0.6">
		<create>
		  <node changeset="{0}" id="-5393" lat="50.79046578105" lon="-1.04971367626" />
		  <node changeset="{0}" id="-5394" lat="50.81" lon="-1.051" />
		  <way changeset="{0}" id="-434">
		   <tag k="note" v="Just a way"/>
		   <nd ref="-5393"/>
		   <nd ref="-5394"/>
		  </way>
		</create>
		</osmChange>"""

	def test_upload_create_way_affect_node(self):

		cs = CreateTestChangeset(self.user, tags={"foo": "invade"}, is_open=True)

		response = self.client.post(reverse('changeset:upload', args=(cs.objId,)), self.xmlSimpleWay.format(cs.objId), 
			content_type='text/xml')
		if response.status_code != 200:
			print response.content
		self.assertEqual(response.status_code, 200)

		xml = fromstring(response.content)
		self.assertEqual(len(xml), 3)
		diffDict = ParseOsmDiffToDict(xml)

		response = self.client.get(reverse('extra:getaffected', args=("node", str(diffDict["node"][-5393][0]))))
		if response.status_code != 200:
			print response.content
		self.assertEqual(response.status_code, 200)

		osmData = DecodeOsmdataResponse(response.content)
		idDict = GetOsmDataIndex(osmData)
		self.assertEqual(len(idDict["node"]), 2)
		self.assertEqual(len(idDict["way"]), 1)
		self.assertEqual(len(idDict["relation"]), 0)
		self.assertEqual(diffDict["node"][-5393][0] in idDict["node"], True)
		self.assertEqual(diffDict["node"][-5394][0] in idDict["node"], True)
		self.assertEqual(diffDict["way"][-434][0] in idDict["way"], True)

	def test_upload_create_way_affect_way(self):

		cs = CreateTestChangeset(self.user, tags={"foo": "invade"}, is_open=True)

		response = self.client.post(reverse('changeset:upload', args=(cs.objId,)), self.xmlSimpleWay.format(cs.objId), 
			content_type='text/xml')
		if response.status_code != 200:
			print response.content
		self.assertEqual(response.status_code, 200)

		xml = fromstring(response.content)
		self.assertEqual(len(xml), 3)
		diffDict = ParseOsmDiffToDict(xml)

		response = self.client.get(reverse('extra:getaffected', args=("way", str(diffDict["way"][-434][0]))))
		if response.status_code != 200:
			print response.content
		self.assertEqual(response.status_code, 200)

		osmData = DecodeOsmdataResponse(response.content)
		idDict = GetOsmDataIndex(osmData)
		self.assertEqual(len(idDict["node"]), 2)
		self.assertEqual(len(idDict["way"]), 1)
		self.assertEqual(len(idDict["relation"]), 0)
		self.assertEqual(diffDict["node"][-5393][0] in idDict["node"], True)
		self.assertEqual(diffDict["node"][-5394][0] in idDict["node"], True)
		self.assertEqual(diffDict["way"][-434][0] in idDict["way"], True)

	def tearDown(self):
		u = User.objects.get(username = self.username)
		u.delete()

		#Swig based transaction object is not freed if an exception is thrown in python view code
		#Encourage this to happen here.
		#https://stackoverflow.com/a/8927538/4288232
		sys.exc_clear()
		gc.collect()

		errStr = pgmap.PgMapError()
		t = p.GetTransaction(b"EXCLUSIVE")
		ok = t.ResetActiveTables(errStr)
		if not ok:
			print errStr.errStr
		t.Commit()

