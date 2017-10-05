# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.test import TestCase
from django.test import Client
from django.urls import reverse
from django.contrib.auth.models import User

from querymap.views import p
import pgmap
import random

def DecodeOsmdataResponse(xml):
	data = pgmap.OsmData()
	dec = pgmap.OsmXmlDecodeString()
	dec.output = data
	for chunk in xml:
		dec.DecodeSubString(chunk, len(chunk), False)
	dec.DecodeSubString(b"", 0, True)
	return data

def GetObjectIdDicts(data):
	nodeIdDict = {}
	for nodeNum in range(len(data.nodes)):
		node2 = data.nodes[nodeNum]
		nodeIdDict[node2.objId] = node2

	wayIdDict = {}
	for wayNum in range(len(data.ways)):
		way2 = data.ways[wayNum]
		wayIdDict[way2.objId] = way2

	relationIdDict = {}
	for relationNum in range(len(data.relations)):
		relation2 = data.relations[relationNum]
		relationIdDict[relation2.objId] = relation2

	return nodeIdDict, wayIdDict, relationIdDict

def create_node(uid, username, nearbyNode = None, changeset = 1000):
	node = pgmap.OsmNode()
	node.objId = -1
	node.metaData.version = 1
	node.metaData.timestamp = 0
	node.metaData.changeset = changeset
	node.metaData.uid = uid
	node.metaData.username = username.encode("UTF-8")
	node.metaData.visible = True
	node.tags[b"test"] = b"autumn"
	if nearbyNode is None:
		node.lat = 43.0 + random.uniform(-1.0, 1.0)
		node.lon = -70.3 + random.uniform(-1.0, 1.0)
	else:
		node.lat = nearbyNode.lat + random.uniform(-0.00015, 0.00015)
		node.lon = nearbyNode.lon + random.uniform(-0.00015, 0.00015)

	data = pgmap.OsmData()
	data.nodes.append(node)

	createdNodeIds = pgmap.mapi64i64()
	createdWayIds = pgmap.mapi64i64()
	createdRelationIds = pgmap.mapi64i64()
	errStr = pgmap.PgMapError()

	t = p.GetTransaction(b"EXCLUSIVE")
	ok = t.StoreObjects(data, createdNodeIds, createdWayIds, createdRelationIds, errStr)
	if not ok:
		t.Abort()
		print errStr.errStr
		return None
	else:
		t.Commit()
	node.objId = createdNodeIds[-1]
	return node

def create_way(uid, username, refs, changeset = 1000):

	way = pgmap.OsmWay()
	way.objId = -1
	way.metaData.version = 1
	way.metaData.timestamp = 0
	way.metaData.changeset = changeset
	way.metaData.uid = uid
	way.metaData.username = username.encode("UTF-8")
	way.metaData.visible = True
	way.tags[b"test"] = b"spring"
	for ref in refs:
		way.refs.append(ref)

	data = pgmap.OsmData()
	data.ways.append(way)

	createdNodeIds = pgmap.mapi64i64()
	createdWayIds = pgmap.mapi64i64()
	createdRelationIds = pgmap.mapi64i64()
	errStr = pgmap.PgMapError()

	t = p.GetTransaction(b"EXCLUSIVE")
	ok = t.StoreObjects(data, createdNodeIds, createdWayIds, createdRelationIds, errStr)
	if not ok:
		t.Abort()
		print errStr.errStr
		return None
	else:
		t.Commit()
	way.objId = createdWayIds[-1]
	return way

# Create your tests here.

class QueryMapTestCase(TestCase):
	def setUp(self):
		self.username = "john"
		self.password = "glass onion"
		self.email = 'jlennon@beatles.com'
		self.user = User.objects.create_user(self.username, self.email, self.password)
		self.client = Client()
		self.client.login(username=self.username, password=self.password)
		self.roi = [-1.0684204,50.8038735,-1.0510826,50.812877]
		errStr = pgmap.PgMapError()
		t = p.GetTransaction(b"EXCLUSIVE")
		ok = t.ResetActiveTables(errStr)
		if not ok:
			t.Abort()
			print errStr.errStr
		else:
			t.Commit()
		self.assertEqual(ok, True)

	def create_relation(self, refs):

		relation = pgmap.OsmRelation()
		relation.objId = -1
		relation.metaData.version = 1
		relation.metaData.timestamp = 0
		relation.metaData.changeset = 1000
		relation.metaData.uid = self.user.id
		relation.metaData.username = self.user.username.encode("UTF-8")
		relation.metaData.visible = True
		relation.tags[b"test"] = b"moon"
		for refTypeStr, refId, refRole in refs:
			relation.refTypeStrs.append(refTypeStr.encode("UTF-8"))
			relation.refIds.append(refId)
			relation.refRoles.append(refRole.encode("UTF-8"))

		data = pgmap.OsmData()
		data.relations.append(relation)

		createdNodeIds = pgmap.mapi64i64()
		createdWayIds = pgmap.mapi64i64()
		createdRelationIds = pgmap.mapi64i64()
		errStr = pgmap.PgMapError()

		t = p.GetTransaction(b"EXCLUSIVE")
		ok = t.StoreObjects(data, createdNodeIds, createdWayIds, createdRelationIds, errStr)
		if not ok:
			t.Abort()
			print errStr.errStr
		else:
			t.Commit()
		self.assertEqual(ok, True)
		relation.objId = createdRelationIds[-1]
		return relation

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

		t = p.GetTransaction(b"EXCLUSIVE")
		ok = t.StoreObjects(data, createdNodeIds, createdWayIds, createdRelationIds, errStr)
		if not ok:
			t.Abort()
			print errStr.errStr
		else:
			t.Commit()
		self.assertEqual(ok, True)
		return node

	def modify_way(self, wayIn, refsIn, tagsIn):
		way = pgmap.OsmWay()
		way.objId = wayIn.objId
		way.metaData.version = wayIn.metaData.version + 1
		way.metaData.timestamp = 0
		way.metaData.changeset = 1000
		way.metaData.uid = self.user.id
		way.metaData.username = self.user.username.encode("UTF-8")
		way.metaData.visible = True
		for k in tagsIn:
			way.tags[k.encode("UTF-8")] = tagsIn[k].encode("UTF-8")
		for ref in refsIn:
			way.refs.append(ref)

		data = pgmap.OsmData()
		data.ways.append(way)

		createdNodeIds = pgmap.mapi64i64()
		createdWayIds = pgmap.mapi64i64()
		createdRelationIds = pgmap.mapi64i64()
		errStr = pgmap.PgMapError()

		t = p.GetTransaction(b"EXCLUSIVE")
		ok = t.StoreObjects(data, createdNodeIds, createdWayIds, createdRelationIds, errStr)
		if not ok:
			t.Abort()
			print errStr.errStr
		else:
			t.Commit()
		self.assertEqual(ok, True)
		return way

	def modify_relation(self, relationIn, refsIn, tagsIn):
		relation = pgmap.OsmRelation()
		relation.objId = relationIn.objId
		relation.metaData.version = relationIn.metaData.version + 1
		relation.metaData.timestamp = 0
		relation.metaData.changeset = 1000
		relation.metaData.uid = self.user.id
		relation.metaData.username = self.user.username.encode("UTF-8")
		relation.metaData.visible = True
		for k in tagsIn:
			relation.tags[k.encode("UTF-8")] = tagsIn[k].encode("UTF-8")
		for refTypeStr, refId, refRole in refsIn:
			relation.refTypeStrs.append(refTypeStr.encode("UTF-8"))
			relation.refIds.append(refId)
			relation.refRoles.append(refRole.encode("UTF-8"))

		data = pgmap.OsmData()
		data.relations.append(relation)

		createdNodeIds = pgmap.mapi64i64()
		createdWayIds = pgmap.mapi64i64()
		createdRelationIds = pgmap.mapi64i64()
		errStr = pgmap.PgMapError()

		t = p.GetTransaction(b"EXCLUSIVE")
		ok = t.StoreObjects(data, createdNodeIds, createdWayIds, createdRelationIds, errStr)
		if not ok:
			t.Abort()
			print errStr.errStr
		else:
			t.Commit()
		self.assertEqual(ok, True)
		return relation
		
	def delete_object(self, objIn):
		if isinstance(objIn, pgmap.OsmNode):
			obj = pgmap.OsmNode()
		elif isinstance(objIn, pgmap.OsmWay):
			obj = pgmap.OsmWay()
		elif isinstance(objIn, pgmap.OsmRelation):
			obj = pgmap.OsmRelation()

		obj.objId = objIn.objId
		obj.metaData.version = objIn.metaData.version + 1
		obj.metaData.timestamp = 0
		obj.metaData.changeset = 1000
		obj.metaData.uid = self.user.id
		obj.metaData.username = self.user.username.encode("UTF-8")
		obj.metaData.visible = False
		if isinstance(objIn, pgmap.OsmNode):
			obj.lat = objIn.lat
			obj.lon = objIn.lon

		data = pgmap.OsmData()

		if isinstance(objIn, pgmap.OsmNode):
			data.nodes.append(obj)
		elif isinstance(objIn, pgmap.OsmWay):
			data.ways.append(obj)
		elif isinstance(objIn, pgmap.OsmRelation):
			data.relations.append(obj)

		createdNodeIds = pgmap.mapi64i64()
		createdWayIds = pgmap.mapi64i64()
		createdRelationIds = pgmap.mapi64i64()
		errStr = pgmap.PgMapError()

		t = p.GetTransaction(b"EXCLUSIVE")
		ok = t.StoreObjects(data, createdNodeIds, createdWayIds, createdRelationIds, errStr)
		if not ok:
			t.Abort()
			print errStr.errStr
		else:
			t.Commit()
		self.assertEqual(ok, True)

	def find_object_ids(self, data):
		nodeIdSet = set()
		for nodeNum in range(len(data.nodes)):
			node2 = data.nodes[nodeNum]
			nodeIdSet.add(node2.objId)

		wayIdSet = set()
		nodeMems = set()
		wayMems = set()
		relationMems = set()
		for wayNum in range(len(data.ways)):
			way2 = data.ways[wayNum]
			wayIdSet.add(way2.objId)

			for mem in way2.refs:
				nodeMems.add(mem)

		relationIdSet = set()
		for relationNum in range(len(data.relations)):
			relation2 = data.relations[relationNum]
			relationIdSet.add(relation2.objId)

			for memId, memType in zip(relation2.refIds, relation2.refTypeStrs):
				if memType == "node":
					nodeMems.add(memId)
				if memType == "way":
					wayMems.add(memId)
				if memType == "relation":
					relationMems.add(memId)

		return nodeIdSet, wayIdSet, relationIdSet, nodeMems, wayMems, relationMems

	def check_node_in_query(self, node, expected=True):

		anonClient = Client()
		bbox = [node.lon-0.0001, node.lat-0.0001, node.lon+0.0001, node.lat+0.0001]
		response = anonClient.get(reverse('index') + "?bbox={},{},{},{}".format(*bbox))
		self.assertEqual(response.status_code, 200)

		data = DecodeOsmdataResponse(response.streaming_content)

		nodeIdDict, wayIdDict, relationIdDict = GetObjectIdDicts(data)
		self.assertEqual(node.objId in nodeIdDict, expected)

		if expected:
			qNode = nodeIdDict[node.objId]
			self.assertEqual(dict(node.tags) == dict(qNode.tags), True)

			self.assertEqual(abs(node.lat - qNode.lat)<1e-6, True)
			self.assertEqual(abs(node.lon - qNode.lon)<1e-6, True)

	def get_bbox_for_nodes(self, nodes):
		bbox = [None, None, None, None]
		for node in nodes:
			if bbox[0] is None or node.lon < bbox[0]:
				bbox[0] = node.lon
			if bbox[1] is None or node.lat < bbox[1]:
				bbox[1] = node.lat
			if bbox[2] is None or node.lon > bbox[2]:
				bbox[2] = node.lon
			if bbox[3] is None or node.lat > bbox[3]:
				bbox[3] = node.lat
		return bbox

	def check_way_in_query(self, way, bbox, expected):
		
		anonClient = Client()
		response = anonClient.get(reverse('index') + "?bbox={},{},{},{}".format(*bbox))
		self.assertEqual(response.status_code, 200)

		data = DecodeOsmdataResponse(response.streaming_content)

		nodeIdDict, wayIdDict, relationIdDict = GetObjectIdDicts(data)
		self.assertEqual(way.objId in wayIdDict, expected)
		
		if expected:
			self.assertEqual(dict(way.tags) == dict(wayIdDict[way.objId].tags), True)
			self.assertEqual(list(way.refs) == list(wayIdDict[way.objId].refs), True)

	def check_relation_in_query(self, relation, bbox, expected):
		
		anonClient = Client()
		response = anonClient.get(reverse('index') + "?bbox={},{},{},{}".format(*bbox))
		self.assertEqual(response.status_code, 200)

		data = DecodeOsmdataResponse(response.streaming_content)

		nodeIdDict, wayIdDict, relationIdDict = GetObjectIdDicts(data)
		self.assertEqual(relation.objId in relationIdDict, expected)
		
		if expected:
			self.assertEqual(dict(relation.tags) == dict(relationIdDict[relation.objId].tags), True)
			self.assertEqual(list(relation.refIds) == list(relationIdDict[relation.objId].refIds), True)

	def test_query_active_node(self):
		node = create_node(self.user.id, self.user.username)

		self.check_node_in_query(node, True)

	def test_modify_active_node(self):
		node = create_node(self.user.id, self.user.username)

		modNode = self.modify_node(node, 1)
		self.check_node_in_query(modNode, True)
		self.check_node_in_query(node, False)

	def test_delete_active_node(self):
		node = create_node(self.user.id, self.user.username)

		self.delete_object(node)
		self.check_node_in_query(node, False)

	def test_delete_static_node(self):

		#Find a node that is not part of any other object
		anonClient = Client()
		response = anonClient.get(reverse('index') + "?bbox={},{},{},{}".format(*self.roi))
		self.assertEqual(response.status_code, 200)

		data = DecodeOsmdataResponse(response.streaming_content)
		nodeIdSet, wayIdSet, relationIdSet, nodeMems, wayMems, relationMems = self.find_object_ids(data)
		candidateIds = list(nodeIdSet.difference(nodeMems))

		if len(candidateIds) > 0:
			nodeIdDict, wayIdDict, relationIdDict = GetObjectIdDicts(data)
			nodeObjToDelete = nodeIdDict[candidateIds[0]]

			self.delete_object(nodeObjToDelete)
			self.check_node_in_query(nodeObjToDelete, False)
		else:
			print "No free nodes in ROI for testing"

	def test_modify_static_node(self):

		#Find a static node
		anonClient = Client()
		response = anonClient.get(reverse('index') + "?bbox={},{},{},{}".format(*self.roi))
		self.assertEqual(response.status_code, 200)

		data = DecodeOsmdataResponse(response.streaming_content)
		nodeIdSet, wayIdSet, relationIdSet, nodeMems, wayMems, relationMems = self.find_object_ids(data)
		nodeIdSet = list(nodeIdSet)

		if len(nodeIdSet) > 0:
			nodeIdDict, wayIdDict, relationIdDict = GetObjectIdDicts(data)
			nodeObjToModify = nodeIdDict[nodeIdSet[0]]

			modNode = self.modify_node(nodeObjToModify, 1)
			self.check_node_in_query(modNode, True)
			self.check_node_in_query(nodeObjToModify, False)

		else:
			print "No nodes in ROI for testing"

	def test_query_active_way(self):
		node = create_node(self.user.id, self.user.username)
		node2 = create_node(self.user.id, self.user.username, node)
		way = create_way(self.user.id, self.user.username, [node.objId, node2.objId])

		bbox = self.get_bbox_for_nodes([node, node2])
		self.check_way_in_query(way, bbox, True)

	def test_modify_active_way(self):
		node = create_node(self.user.id, self.user.username)
		node2 = create_node(self.user.id, self.user.username, node)
		way = create_way(self.user.id, self.user.username, [node.objId, node2.objId])

		modWay = self.modify_way(way, [node.objId, node2.objId, node.objId], {"foo": "eggs"})
		
		bbox = self.get_bbox_for_nodes([node, node2])
		self.check_way_in_query(modWay, bbox, True)

	def test_delete_active_way(self):
		node = create_node(self.user.id, self.user.username)
		node2 = create_node(self.user.id, self.user.username, node)
		way = create_way(self.user.id, self.user.username, [node.objId, node2.objId])

		self.delete_object(way)
		
		bbox = self.get_bbox_for_nodes([node, node2])
		self.check_way_in_query(way, bbox, False)

	def test_modify_static_way(self):

		#Find a way that is not part of any other relation
		anonClient = Client()
		response = anonClient.get(reverse('index') + "?bbox={},{},{},{}".format(*self.roi))
		self.assertEqual(response.status_code, 200)

		data = DecodeOsmdataResponse(response.streaming_content)
		nodeIdSet, wayIdSet, relationIdSet, nodeMems, wayMems, relationMems = self.find_object_ids(data)
		candidateIds = list(wayIdSet.difference(wayMems))

		if len(candidateIds) > 0:
			nodeIdDict, wayIdDict, relationIdDict = GetObjectIdDicts(data)
			wayObjToMod = wayIdDict[candidateIds[0]]

			refs = list(wayObjToMod.refs)
			refs.append(refs[0])
			modWay = self.modify_way(wayObjToMod, refs, {"foo": "bacon"})
			self.check_way_in_query(modWay, self.roi, True)
		else:
			print "No free ways in ROI for testing"

	def test_delete_static_way(self):

		#Find a way that is not part of any other relation
		anonClient = Client()
		response = anonClient.get(reverse('index') + "?bbox={},{},{},{}".format(*self.roi))
		self.assertEqual(response.status_code, 200)

		data = DecodeOsmdataResponse(response.streaming_content)
		nodeIdSet, wayIdSet, relationIdSet, nodeMems, wayMems, relationMems = self.find_object_ids(data)
		candidateIds = list(wayIdSet.difference(wayMems))

		if len(candidateIds) > 0:
			nodeIdDict, wayIdDict, relationIdDict = GetObjectIdDicts(data)
			wayObjToDelete = wayIdDict[candidateIds[0]]

			self.delete_object(wayObjToDelete)
			self.check_way_in_query(wayObjToDelete, self.roi, False)
		else:
			print "No free ways in ROI for testing"

	def test_query_active_relation(self):
		node = create_node(self.user.id, self.user.username)
		node2 = create_node(self.user.id, self.user.username, node)
		way = create_way(self.user.id, self.user.username, [node.objId, node2.objId])
		relation = self.create_relation([("node", node.objId, "parrot"), ("node", node2.objId, "dead")])

		bbox = self.get_bbox_for_nodes([node, node2])
		self.check_relation_in_query(relation, bbox, True)

	def test_query_active_relation_with_way_member(self):
		node = create_node(self.user.id, self.user.username)
		node2 = create_node(self.user.id, self.user.username, node)
		way = create_way(self.user.id, self.user.username, [node.objId, node2.objId])
		relation = self.create_relation([("way", way.objId, "parrot")])

		bbox = self.get_bbox_for_nodes([node, node2])
		self.check_relation_in_query(relation, bbox, True)

	def test_modify_active_relation(self):
		node = create_node(self.user.id, self.user.username)
		node2 = create_node(self.user.id, self.user.username, node)
		node3 = create_node(self.user.id, self.user.username, node)
		relation = self.create_relation([("node", node.objId, "parrot")])

		modRelation = self.modify_relation(relation, 
			[("node", node.objId, "parrot"), ("node", node2.objId, "dead"), ("node", node3.objId, "")], 
			{"foo": "bar"})
		
		bbox = self.get_bbox_for_nodes([node, node2, node3])
		self.check_relation_in_query(modRelation, bbox, True)

	def test_delete_active_relation(self):
		node = create_node(self.user.id, self.user.username)
		node2 = create_node(self.user.id, self.user.username, node)
		way = create_way(self.user.id, self.user.username, [node.objId, node2.objId])
		relation = self.create_relation([("node", node.objId, "parrot"), ("way", way.objId, "dead")])

		self.delete_object(relation)
		
		bbox = self.get_bbox_for_nodes([node, node2])
		self.check_relation_in_query(way, bbox, False)

	def test_modify_static_relation(self):

		#Find a relation that is not part of any other relation
		anonClient = Client()
		response = anonClient.get(reverse('index') + "?bbox={},{},{},{}".format(*self.roi))
		self.assertEqual(response.status_code, 200)

		data = DecodeOsmdataResponse(response.streaming_content)
		nodeIdSet, wayIdSet, relationIdSet, nodeMems, wayMems, relationMems = self.find_object_ids(data)
		candidateIds = list(relationIdSet.difference(relationMems))

		#TODO Improve check for parents of relation?

		if len(candidateIds) > 0:
			nodeIdDict, wayIdDict, relationIdDict = GetObjectIdDicts(data)
			relationObjToMod = relationIdDict[candidateIds[0]]

			refTypeStrs = list(relationObjToMod.refTypeStrs)
			refTypeStrs.append(refTypeStrs[0])
			refIds = list(relationObjToMod.refIds)
			refIds.append(refIds[0])
			refRoles = list(relationObjToMod.refRoles)
			refRoles.append(refRoles[0])
			modRelation = self.modify_relation(relationObjToMod, zip(refTypeStrs, refIds, refRoles), {"foo": "bacon"})
			self.check_relation_in_query(modRelation, self.roi, True)
		else:
			print "No free relations in ROI for testing"

	def test_delete_static_relation(self):

		#Find a relation that is not part of any other relation
		anonClient = Client()
		response = anonClient.get(reverse('index') + "?bbox={},{},{},{}".format(*self.roi))
		self.assertEqual(response.status_code, 200)

		data = DecodeOsmdataResponse(response.streaming_content)
		nodeIdSet, wayIdSet, relationIdSet, nodeMems, wayMems, relationMems = self.find_object_ids(data)
		candidateIds = list(relationIdSet.difference(relationMems))

		#TODO Improve check for parents of relation?

		if len(candidateIds) > 0:
			nodeIdDict, wayIdDict, relationIdDict = GetObjectIdDicts(data)
			relationObjToDel = relationIdDict[candidateIds[0]]

			self.delete_object(relationObjToDel)
			self.check_relation_in_query(relationObjToDel, self.roi, False)
		else:
			print "No free relations in ROI for testing"


	def tearDown(self):
		u = User.objects.get(username = self.username)
		u.delete()
		errStr = pgmap.PgMapError()
		t = p.GetTransaction(b"EXCLUSIVE")
		ok = t.ResetActiveTables(errStr)
		if not ok:
			print errStr.errStr
		self.assertEqual(ok, True)

