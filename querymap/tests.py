# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.test import TestCase
from django.test import Client
from django.urls import reverse
from django.contrib.auth.models import User

from querymap.views import p
import pgmap
import random

# Create your tests here.

class ElementsTestCase(TestCase):
	def setUp(self):
		self.username = "john"
		self.password = "glass onion"
		self.email = 'jlennon@beatles.com'
		self.user = User.objects.create_user(self.username, self.email, self.password)
		self.client = Client()
		self.client.login(username=self.username, password=self.password)

	def create_node(self):
		node = pgmap.OsmNode()
		node.objId = -1
		node.metaData.version = 1
		node.metaData.timestamp = 0
		node.metaData.changeset = 1000
		node.metaData.uid = self.user.id
		node.metaData.username = self.user.username.encode("UTF-8")
		node.metaData.visible = True
		node.tags[b"test"] = b"autumn"
		node.lat = 43.0 + random.uniform(-1.0, 1.0)
		node.lon = -70.3 + random.uniform(-1.0, 1.0)

		data = pgmap.OsmData()
		data.nodes.append(node)

		createdNodeIds = pgmap.mapi64i64()
		createdWayIds = pgmap.mapi64i64()
		createdRelationIds = pgmap.mapi64i64()
		errStr = pgmap.PgMapError()

		ok = p.StoreObjects(data, createdNodeIds, createdWayIds, createdRelationIds, errStr)
		if not ok:
			print errStr.errStr
		self.assertEqual(ok, True)
		node.objId = createdNodeIds[-1]
		return node

	def modify_node(self, nodeIn, nodeCurrentVer):
		node = pgmap.OsmNode()
		node.objId = nodeIn.objId
		node.metaData.version = nodeCurrentVer + 1
		node.metaData.timestamp = 0
		node.metaData.changeset = 1000
		node.metaData.uid = self.user.id
		node.metaData.username = self.user.username.encode("UTF-8")
		node.metaData.visible = True
		node.tags[b"test"] = b"winter"
		node.lat = nodeIn.lat + 0.1
		node.lon = nodeIn.lon + 0.2

		data = pgmap.OsmData()
		data.nodes.append(node)

		createdNodeIds = pgmap.mapi64i64()
		createdWayIds = pgmap.mapi64i64()
		createdRelationIds = pgmap.mapi64i64()
		errStr = pgmap.PgMapError()

		ok = p.StoreObjects(data, createdNodeIds, createdWayIds, createdRelationIds, errStr)
		if not ok:
			print errStr.errStr
		self.assertEqual(ok, True)
		return node

	def delete_node(self, nodeIn, nodeCurrentVer):
		node = pgmap.OsmNode()
		node.objId = nodeIn.objId
		node.metaData.version = nodeCurrentVer + 1
		node.metaData.timestamp = 0
		node.metaData.changeset = 1000
		node.metaData.uid = self.user.id
		node.metaData.username = self.user.username.encode("UTF-8")
		node.metaData.visible = False
		node.lat = nodeIn.lat
		node.lon = nodeIn.lon

		data = pgmap.OsmData()
		data.nodes.append(node)

		createdNodeIds = pgmap.mapi64i64()
		createdWayIds = pgmap.mapi64i64()
		createdRelationIds = pgmap.mapi64i64()
		errStr = pgmap.PgMapError()

		ok = p.StoreObjects(data, createdNodeIds, createdWayIds, createdRelationIds, errStr)
		if not ok:
			print errStr.errStr
		self.assertEqual(ok, True)

	def decode_response(self, xml):
		data = pgmap.OsmData()
		dec = pgmap.OsmXmlDecodeString()
		dec.output = data
		for chunk in xml:
			dec.DecodeSubString(chunk, len(chunk), False)
		dec.DecodeSubString(b"", 0, True)
		return data		

	def check_node_in_query(self, node):

		anonClient = Client()
		bbox = [node.lon-0.0001, node.lat-0.0001, node.lon+0.0001, node.lat+0.0001]
		response = anonClient.get(reverse('index') + "?bbox={},{},{},{}".format(*bbox))
		self.assertEqual(response.status_code, 200)

		data = self.decode_response(response.streaming_content)

		nodeIdSet = set()
		for nodeNum in range(len(data.nodes)):
			node2 = data.nodes[nodeNum]
			nodeIdSet.add(node2.objId)
		return node.objId in nodeIdSet
		
	def test_query_active_node(self):
		
		node = self.create_node()
		found = self.check_node_in_query(node)
		self.assertEqual(found, True)

	def test_modify_active_node(self):
		node = self.create_node()

		modNode = self.modify_node(node, 1)
		self.assertEqual(self.check_node_in_query(modNode), True)
		self.assertEqual(self.check_node_in_query(node), False)

	def test_delete_active_node(self):
		node = self.create_node()

		self.delete_node(node, 1)
		self.assertEqual(self.check_node_in_query(node), False)
		
	def tearDown(self):
		u = User.objects.get(username = self.username)
		u.delete()

