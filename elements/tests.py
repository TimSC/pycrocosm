# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from __future__ import print_function

from django.test import TestCase
from django.test import Client
from django.urls import reverse
from django.contrib.auth.models import User

from querymap.views import p
from querymap.tests import create_node, create_way, DecodeOsmdataResponse
from changeset.tests import CreateTestChangeset
import gc
import sys
from changeset.views import GetOsmDataIndex

# Create your tests here.

class ElementsTestCase(TestCase):
	def setUp(self):
		self.username = "john"
		self.password = "glass onion"
		self.email = 'jlennon@beatles.com'
		self.user = User.objects.create_user(self.username, self.email, self.password)
		self.client = Client()
		self.client.login(username=self.username, password=self.password)

	#def test_put_node(self):

	#	response = self.client.put(reverse('elements:element', args=['node', '5']), "", content_type='text/xml')

	#	self.assertEqual(response.status_code, 200)

	def test_create_node(self):
		cs = CreateTestChangeset(self.user, tags={"foo": "me"}, is_open=True)

		createXml = """<osm>
			 <node changeset="{}" lat="51.0" lon="2.2">
			   <tag k="note" v="Just a node"/>
			 </node>
			</osm>""".format(cs.objId)
		response = self.client.put(reverse('elements:create', args=['node']), createXml, content_type='text/xml')
		if response.status_code != 200:
			print (response.content)

		self.assertEqual(response.status_code, 200)

	def test_create_node_invalid_xml(self):
		cs = CreateTestChangeset(self.user, tags={"foo": "me"}, is_open=True)

		createXml = """<osm>
			 <node changeset="{}" lat="51.0" lon="2.2">
			   <tag k="note" v="Just a node"/>
			</osm>""".format(cs.objId)
		response = self.client.put(reverse('elements:create', args=['node']), createXml, content_type='text/xml')

		self.assertEqual(response.status_code, 400)

	def tearDown(self):
		u = User.objects.get(username = self.username)
		u.delete()

class ElementsGetParentsTestCase(TestCase):
	def setUp(self):
		self.username = "john"
		self.password = "glass onion"
		self.email = 'jlennon@beatles.com'
		self.user = User.objects.create_user(self.username, self.email, self.password)

	def test_get_ways_for_node(self):
		anonClient = Client()

		node = create_node(self.user.id, self.user.username)
		node2 = create_node(self.user.id, self.user.username, node)
		way = create_way(self.user.id, self.user.username, [node.objId, node2.objId])

		response = anonClient.get(reverse('elements:ways_for_node', args=['node', str(node.objId)]))
		if response.status_code != 200:
			print (response.content)
		self.assertEqual(response.status_code, 200)

		osmData = DecodeOsmdataResponse([response.content])
		wayIdDict = GetOsmDataIndex(osmData)['way']

		self.assertEqual(len(wayIdDict), 1)
		self.assertEqual(way.objId in wayIdDict, True)

	def tearDown(self):

		u = User.objects.get(username = self.username)
		u.delete()

