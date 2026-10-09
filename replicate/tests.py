from django.test import TestCase, SimpleTestCase
from django.test import Client
from django.urls import reverse
from django.contrib.auth.models import User
from django.conf import settings

from pycrocosm.mapdb import get_pgmap
import replicate.views as rv
import querymap.tests as qmt
import pgmap
import random
import sys
import io
import time
import datetime
import zlib
from changeset.views import GetOsmDataIndex

def CreateIndexOsmChange(osc):
	index = {'node':{}, 'way':{}, 'relation':{}}

	for i in range(osc.blocks.size()):
		block = osc.blocks[i].data
		action = osc.blocks[i].action
		for j in range(block.nodes.size()):
			node = block.nodes[j]

			if node.objId not in index["node"]:
				index["node"][node.objId] = []				
			index["node"][node.objId].append((action, node))

		for j in range(block.ways.size()):
			way = block.ways[j]

			if way.objId not in index["way"]:
				index["way"][way.objId] = []				
			index["way"][way.objId].append((action, way))

		for j in range(block.relations.size()):
			relation = block.relations[j]

			if relation.objId not in index["relation"]:
				index["relation"][relation.objId] = []				
			index["relation"][relation.objId].append((action, relation))

	return index

class ReplicateTestCase(TestCase):
	def setUp(self):
		self.username = "john"
		self.password = "glass onion"
		self.email = 'jlennon@beatles.com'
		self.user = User.objects.create_user(self.username, self.email, self.password)
		self.client = Client()
		self.client.login(username=self.username, password=self.password)
		self.roi = [-1.0684204,50.8038735,-1.0510826,50.812877]
		errStr = pgmap.PgMapError()
		t = get_pgmap().GetTransaction("EXCLUSIVE")
		ok = t.ResetActiveTables(errStr)
		if not ok:
			t.Abort()
			print (errStr.errStr)
		else:
			t.Commit()
		self.assertEqual(ok, True)

	def tearDown(self):
		u = User.objects.get(username = self.username)
		u.delete()

	def test_create_node_diff(self):
		ts = time.time() - 60
		node = qmt.create_node(self.user.id, self.user.username, timestamp = ts)
		cat = rv.TimestampToPath(node.metaData.timestamp, "minute")

		#Check minutely diff
		anonClient = Client()
		response = anonClient.get(reverse('replicate:diffdata', args=['minute', str(cat[0]), str(cat[1]), str(cat[2])]))
		if response.status_code != 200:
			print (response.content)
		self.assertEqual(response.status_code, 200)

		osc = pgmap.OsmChange()
		pgmap.LoadFromOsmChangeXml(response.content.decode("utf-8"), osc)

		oscIndex = CreateIndexOsmChange(osc)

		self.assertEqual(node.objId in oscIndex["node"], True)
		self.assertEqual(oscIndex["node"][node.objId][0][0], "create")

	def test_create_node_diff_custom_range(self):
		ts = time.time() - 60
		node = qmt.create_node(self.user.id, self.user.username, timestamp = ts)

		#Check custom time range diff
		anonClient = Client()
		startTs = datetime.datetime.utcfromtimestamp(ts-5).isoformat()
		endTs = datetime.datetime.utcfromtimestamp(ts+5).isoformat()
		response = anonClient.get(reverse('replicate:customdiff')+'?start={}&end={}'.format(startTs, endTs))
		if response.status_code != 200:
			print (response.content)
		self.assertEqual(response.status_code, 200)

		osc = pgmap.OsmChange()
		pgmap.LoadFromOsmChangeXml(response.content.decode("utf-8"), osc)

		oscIndex = CreateIndexOsmChange(osc)

		self.assertEqual(node.objId in oscIndex["node"], True)
		self.assertEqual(oscIndex["node"][node.objId][0][0], "create")

	def test_create_node_diff_custom_range_compressed(self):
		ts = time.time() - 60
		node = qmt.create_node(self.user.id, self.user.username, timestamp = ts)

		#Check custom time range diff
		anonClient = Client()
		startTs = datetime.datetime.utcfromtimestamp(ts-5).isoformat()
		endTs = datetime.datetime.utcfromtimestamp(ts+5).isoformat()
		response = anonClient.get(reverse('replicate:customdiff')+'?start={}&end={}&compress=gz'.format(startTs, endTs))
		if response.status_code != 200:
			print (response.content)
		self.assertEqual(response.status_code, 200)

		osc = pgmap.OsmChange()
		pgmap.LoadFromOsmChangeXml(zlib.decompress(response.content, zlib.MAX_WBITS|16).decode("utf-8"), osc)

		oscIndex = CreateIndexOsmChange(osc)

		self.assertEqual(node.objId in oscIndex["node"], True)
		self.assertEqual(oscIndex["node"][node.objId][0][0], "create")

	def test_create_way_diff(self):
		ts = time.time() - 60
		node1 = qmt.create_node(self.user.id, self.user.username, timestamp = ts)
		node2 = qmt.create_node(self.user.id, self.user.username, timestamp = ts)
		refs = [node1.objId, node2.objId]
		way = qmt.create_way(self.user.id, self.user.username, refs, timestamp = ts)
		cat = rv.TimestampToPath(way.metaData.timestamp, "minute")

		anonClient = Client()
		response = anonClient.get(reverse('replicate:diffdata', args=['minute', str(cat[0]), str(cat[1]), str(cat[2])]))
		if response.status_code != 200:
			print (response.content)
		self.assertEqual(response.status_code, 200)

		osc = pgmap.OsmChange()
		pgmap.LoadFromOsmChangeXml(response.content.decode("utf-8"), osc)

		oscIndex = CreateIndexOsmChange(osc)

		self.assertEqual(way.objId in oscIndex["way"], True)
		self.assertEqual(oscIndex["way"][way.objId][0][0], "create")

	def test_create_relation_diff(self):
		ts = time.time() - 60
		node1 = qmt.create_node(self.user.id, self.user.username, timestamp = ts)
		node2 = qmt.create_node(self.user.id, self.user.username, timestamp = ts)
		refs = [("node", node1.objId, "foo"), ("node", node2.objId, "bar")]
		relation = qmt.create_relation(self.user.id, self.user.username, refs, timestamp = ts)
		cat = rv.TimestampToPath(relation.metaData.timestamp, "minute")

		anonClient = Client()
		response = anonClient.get(reverse('replicate:diffdata', args=['minute', str(cat[0]), str(cat[1]), str(cat[2])]))
		if response.status_code != 200:
			print (response.content)
		self.assertEqual(response.status_code, 200)

		osc = pgmap.OsmChange()
		pgmap.LoadFromOsmChangeXml(response.content.decode("utf-8"), osc)

		oscIndex = CreateIndexOsmChange(osc)

		self.assertEqual(relation.objId in oscIndex["relation"], True)
		self.assertEqual(oscIndex["relation"][relation.objId][0][0], "create")

	def test_modify_node_diff(self):
		ts = time.time() - 120
		ts2 = time.time() - 60
		node = qmt.create_node(self.user.id, self.user.username, timestamp = ts)
		ok, node = qmt.modify_node(node, node.metaData.version, self.user, timestamp = ts2)
		self.assertEqual(ok, True)
		cat = rv.TimestampToPath(node.metaData.timestamp, "minute")

		anonClient = Client()
		response = anonClient.get(reverse('replicate:diffdata', args=['minute', str(cat[0]), str(cat[1]), str(cat[2])]))
		if response.status_code != 200:
			print (response.content)
		self.assertEqual(response.status_code, 200)

		osc = pgmap.OsmChange()
		pgmap.LoadFromOsmChangeXml(response.content.decode("utf-8"), osc)

		oscIndex = CreateIndexOsmChange(osc)

		self.assertEqual(node.objId in oscIndex["node"], True)
		found = False
		for action, obj in oscIndex["node"][node.objId]:
			if action == "modify":
				found = True
				break
		self.assertEqual(found, True)

	def test_delete_node_diff(self):
		ts = time.time() - 120
		ts2 = time.time() - 60
		node = qmt.create_node(self.user.id, self.user.username, timestamp = ts)
		ok = qmt.delete_object(node, self.user, timestamp = ts2)
		self.assertEqual(ok, True)
		cat = rv.TimestampToPath(int(ts2), "minute")

		anonClient = Client()
		response = anonClient.get(reverse('replicate:diffdata', args=['minute', str(cat[0]), str(cat[1]), str(cat[2])]))
		if response.status_code != 200:
			print (response.content)
		self.assertEqual(response.status_code, 200)

		osc = pgmap.OsmChange()
		pgmap.LoadFromOsmChangeXml(response.content.decode("utf-8"), osc)

		oscIndex = CreateIndexOsmChange(osc)

		self.assertEqual(node.objId in oscIndex["node"], True)
		found = False
		for action, obj in oscIndex["node"][node.objId]:
			print (action, obj.objId, obj.metaData.version)
			if action == "delete":
				found = True
				break
		self.assertEqual(found, True)



class ActivityIdFilterTestCase(SimpleTestCase):
	def test_supported_filters(self):
		for params, expected in [
			({'id': '7'}, (7, 7, 0)),
			({'first_id': '2', 'last_id': '9'}, (2, 9, 0)),
			({'first_id': '8'}, (8, 0, 0)),
			({'atomic_edit_id': '4'}, (0, 0, 4)),
			({}, None),
		]:
			with self.subTest(params=params):
				self.assertEqual(rv.activity_id_filters(params), expected)

	def test_invalid_filters(self):
		for params in [
			{'id': '-1'}, {'id': '0'}, {'id': str(2**63)},
			{'first_id': '9', 'last_id': '2'}, {'atomic_edit_id': 'x'},
			{'id': '3', 'since': '2026-10-03T00:00:00Z'},
			{'id': '3', 'first_id': '2'},
		]:
			with self.subTest(params=params), self.assertRaises(ValueError):
				rv.activity_id_filters(params)
