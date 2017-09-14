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

	def test_query_node(self):
		createdNodeIds = pgmap.mapi64i64()
		createdWayIds = pgmap.mapi64i64()
		createdRelationIds = pgmap.mapi64i64()
		errStr = pgmap.PgMapError()
		data = pgmap.OsmData()
		
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
		data.nodes.append(node)

		ok = p.StoreObjects(data, createdNodeIds, createdWayIds, createdRelationIds, errStr)
		newNodeId = createdNodeIds[-1]

		anonClient = Client()
		bbox = [node.lon-0.0001, node.lat-0.0001, node.lon+0.0001, node.lat+0.0001]
		response = anonClient.get(reverse('index') + "?bbox={},{},{},{}".format(*bbox))
		self.assertEqual(response.status_code, 200)

		data = pgmap.OsmData()
		dec = pgmap.OsmXmlDecodeString()
		dec.output = data
		for chunk in response.streaming_content:
			dec.DecodeSubString(chunk, len(chunk), False)
		dec.DecodeSubString(b"", 0, True)

		self.assertEqual(len(data.nodes)>0, True)

		nodeIdSet = set()
		for nodeNum in range(len(data.nodes)):
			node2 = data.nodes[nodeNum]
			nodeIdSet.add(node2.objId)
		self.assertEqual(newNodeId in nodeIdSet, True)

	def tearDown(self):
		u = User.objects.get(username = self.username)
		u.delete()

