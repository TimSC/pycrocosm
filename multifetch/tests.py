# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from __future__ import print_function

from django.test import TestCase
from django.test import Client
from django.urls import reverse
from django.contrib.auth.models import User
import sys
import gc
import pgmap
from querymap.views import p
from changeset.tests import CreateTestChangeset, ParseOsmDiffToDict
from defusedxml.ElementTree import parse, fromstring
from querymap.tests import DecodeOsmdataResponse
from changeset.views import GetOsmDataIndex

class MultifetchTestCase(TestCase):

	def setUp(self):
		self.username = "john"
		self.password = "glass onion"
		self.email = 'jlennon@beatles.com'
		self.user = User.objects.create_user(self.username, self.email, self.password)
		self.client = Client()
		self.client.login(username=self.username, password=self.password)

	def test_multifetch_nodes(self):

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
		diffDict = ParseOsmDiffToDict(xml)
		node1Id = diffDict["node"][-5393][0]
		node2Id = diffDict["node"][-5394][0]
		way1Id = diffDict["way"][-434][0]

		response = self.client.get(reverse('multifetch:multifetch', args=("nodes",))+"?nodes={},{}".format(node1Id, node2Id))
		self.assertEqual(response.status_code, 200)

		data = DecodeOsmdataResponse(response.content)

		idDicts = GetOsmDataIndex(data)
		self.assertEqual(node1Id in idDicts['node'], True)
		self.assertEqual(node2Id in idDicts['node'], True)
		self.assertEqual(len(idDicts['way']), 0)
		self.assertEqual(len(idDicts['relation']), 0)

	def test_multifetch_ways(self):

		cs = CreateTestChangeset(self.user, tags={"foo": "invade"}, is_open=True)

		xml = """<osmChange generator="JOSM" version="0.6">
		<create>
		  <node changeset="{0}" id="-5393" lat="50.79046578105" lon="-1.04971367626" />
		  <node changeset="{0}" id="-5394" lat="50.81" lon="-1.051" />
		  <node changeset="{0}" id="-5395" lat="50.82" lon="-1.053" />
		  <way changeset="{0}" id="-434">
		   <tag k="note" v="Just a way"/>
		   <nd ref="-5393"/>
		   <nd ref="-5394"/>
		  </way>
		  <way changeset="{0}" id="-435">
		   <tag k="note" v="Just a way"/>
		   <nd ref="-5394"/>
		   <nd ref="-5395"/>
		  </way>
		</create>
		</osmChange>""".format(cs.objId)

		response = self.client.post(reverse('changeset:upload', args=(cs.objId,)), xml, 
			content_type='text/xml')
		if response.status_code != 200:
			print (response.content)
		self.assertEqual(response.status_code, 200)

		xml = fromstring(response.content)
		diffDict = ParseOsmDiffToDict(xml)
		node1Id = diffDict["node"][-5393][0]
		node2Id = diffDict["node"][-5394][0]
		node3Id = diffDict["node"][-5395][0]
		way1Id = diffDict["way"][-434][0]
		way2Id = diffDict["way"][-435][0]

		response = self.client.get(reverse('multifetch:multifetch', args=("ways",))+"?ways={},{}".format(way1Id, way2Id))
		self.assertEqual(response.status_code, 200)

		data = DecodeOsmdataResponse(response.content)

		idDicts = GetOsmDataIndex(data)
		self.assertEqual(way1Id in idDicts['way'], True)
		self.assertEqual(way2Id in idDicts['way'], True)
		self.assertEqual(len(idDicts['node']), 0)
		self.assertEqual(len(idDicts['relation']), 0)

	def tearDown(self):
		u = User.objects.get(username = self.username)
		u.delete()

		#Swig based transaction object is not freed if an exception is thrown in python view code
		#Encourage this to happen here.
		#https://stackoverflow.com/a/8927538/4288232
		sys.exc_clear()
		gc.collect()

		errStr = pgmap.PgMapError()
		t = p.GetTransaction("EXCLUSIVE")
		ok = t.ResetActiveTables(errStr)
		if not ok:
			print (errStr.errStr)
		t.Commit()

