# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from __future__ import print_function

from django.shortcuts import render
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseNotFound, HttpResponseServerError, HttpResponseForbidden
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from rest_framework.permissions import IsAuthenticated, IsAuthenticatedOrReadOnly
from rest_framework.decorators import api_view, permission_classes, parser_classes

import xml.etree.ElementTree as ET
import sys
if sys.version_info.major < 3: 
	import cStringIO as StringIO
else:
	from io import StringIO
import datetime
import pgmap
import time
import io
from querymap.views import p
from pycrocosm.parsers import DefusedXmlParser, OsmChangeXmlParser

# Create your views here.

def CheckTags(tags):
	for k in tags:
		if len(k) > settings.MAX_TAG_LENGTH:
			return False
		if len(tags[k]) > settings.MAX_TAG_LENGTH:
			return False
	return True

def SerializeChangesetToElement(changesetData, include_discussion=False):

	changeset = ET.Element("changeset")
	changeset.attrib["id"] = str(changesetData.objId)
	if len(changesetData.username) > 0:
		changeset.attrib["user"] = str(changesetData.username)
	if changesetData.uid != 0:
		changeset.attrib["uid"] = str(changesetData.uid)
	if changesetData.open_timestamp != 0:
		changeset.attrib["created_at"] = datetime.datetime.fromtimestamp(changesetData.open_timestamp).isoformat()
	if not changesetData.is_open and changesetData.close_timestamp != 0:
		changeset.attrib["closed_at"] = datetime.datetime.fromtimestamp(changesetData.close_timestamp).isoformat()
	changeset.attrib["open"] = str(changesetData.is_open).lower()
	if changesetData.bbox_set:
		changeset.attrib["min_lon"] = str(changesetData.x1)
		changeset.attrib["min_lat"] = str(changesetData.y1)
		changeset.attrib["max_lon"] = str(changesetData.x2)
		changeset.attrib["max_lat"] = str(changesetData.y2)

	for tagKey in changesetData.tags:
		tag = ET.SubElement(changeset, "tag")
		tag.attrib["k"] = tagKey.decode("utf-8")
		tag.attrib["v"] = changesetData.tags[tagKey].decode("utf-8")

	if include_discussion:

		discussion = ET.SubElement(changeset, "discussion")

		comment = ET.SubElement(discussion, "comment")
		comment.attrib["data"] = "2015-01-01T18:56:48Z"
		comment.attrib["uid"] = "1841"
		comment.attrib["user"] = "metaodi"

		text = ET.SubElement(comment, "text")
		text.text = "Did you verify those street names?"

	return changeset

def SerializeChangesets(changesetsData, include_discussion=False):
	root = ET.Element('osm')
	root.attrib["version"] = str(settings.API_VERSION)
	root.attrib["generator"] = settings.GENERATOR

	for changesetData in changesetsData:
		root.append(SerializeChangesetToElement(changesetData, include_discussion))

	doc = ET.ElementTree(root)
	sio = StringIO.StringIO()
	doc.write(sio, "utf-8")
	return HttpResponse(sio.getvalue(), content_type='text/xml')

def GetOsmDataIndex(osmData):
	
	nodeDict = {}
	for i in range(osmData.nodes.size()):
		node = osmData.nodes[i]
		nodeDict[node.objId] = node
	wayDict = {}
	for i in range(osmData.ways.size()):
		way = osmData.ways[i]
		wayDict[way.objId] = way
	relationDict = {}
	for i in range(osmData.relations.size()):
		relation = osmData.relations[i]
		relationDict[relation.objId] = relation

	out = {'node':nodeDict, 'way':wayDict, 'relation':relationDict}
	return out

def GetAnyKeyValue(d):
	for k in d:
		return k, d[k]
	return None, None

def upload_check_create(objs):
	for i in range(objs.size()):
		obj = objs[i]
		if obj.objId > 0:
			return HttpResponseBadRequest("Created object IDs must be zero or negative", content_type="text/plain")
		if obj.metaData.version != 0:
			return HttpResponseBadRequest("Version for created objects must be null or zero", content_type="text/plain")
		if isinstance(obj, pgmap.OsmNode):
			if obj.lat < -90.0 or obj.lat > 90 or obj.lon < -180.0 or obj.lon > 180.0:
				return HttpResponseBadRequest("Node outside valid range", content_type="text/plain")
		for k in obj.tags:
			if len(k) > 255:
				return HttpResponseBadRequest("Tag key is too long", content_type="text/plain")
			if len(obj.tags[k]) > 255:
				return HttpResponseBadRequest("Tag value is too long", content_type="text/plain")

	return None

def upload_check_modify(objs):
	for i in range(objs.size()):
		obj = objs[i]
		if obj.objId <= 0:
			return HttpResponseBadRequest("Modified object IDs must be positive", content_type="text/plain")
		if obj.metaData.version <= 0:
			return HttpResponseBadRequest("Version for modified objects must be specified and positive", content_type="text/plain")
		if isinstance(obj, pgmap.OsmNode):
			if obj.lat < -90.0 or obj.lat > 90 or obj.lon < -180.0 or obj.lon > 180.0:
				return HttpResponseBadRequest("Node outside valid range", content_type="text/plain")
		for k in obj.tags:
			if len(k) > 255:
				return HttpResponseBadRequest("Tag key is too long", content_type="text/plain")
			if len(obj.tags[k]) > 255:
				return HttpResponseBadRequest("Tag value is too long", content_type="text/plain")
	return None

def upload_update_diff_result(action, objType, objs, createdIds, responseRoot):
	for i in range(objs.size()):
		obj = objs[i]
		comment = ET.SubElement(responseRoot, objType)
		comment.attrib["old_id"] = str(obj.objId)
		if action == "create":
			comment.attrib["new_id"] = str(createdIds[obj.objId])
			comment.attrib["new_version"] = str(obj.metaData.version)
		if action == "modify":
			comment.attrib["new_id"] = str(obj.objId)
			comment.attrib["new_version"] = str(obj.metaData.version)

def upload_block(action, block, changesetId, t, responseRoot, 
	uid, username, timestamp,
	createdNodeIds, createdWayIds, createdRelationIds, ifunused = False):

	if action == "create":
		ret = upload_check_create(block.nodes)
		if ret is not None: return ret
		ret = upload_check_create(block.ways)
		if ret is not None: return ret
		ret = upload_check_create(block.relations)
		if ret is not None: return ret

		for i in range(block.nodes.size()):
			block.nodes[i].metaData.version = 1
		for i in range(block.ways.size()):
			block.ways[i].metaData.version = 1
		for i in range(block.relations.size()):
			block.relations[i].metaData.version = 1

	elif action in ["modify", "delete"]:
		ret = upload_check_modify(block.nodes)
		if ret is not None: return ret
		ret = upload_check_modify(block.ways)
		if ret is not None: return ret
		ret = upload_check_modify(block.relations)
		if ret is not None: return ret

		#Increment version numbers for modified objects
		for i in range(block.nodes.size()):
			block.nodes[i].metaData.version += 1
		for i in range(block.ways.size()):
			block.ways[i].metaData.version += 1
		for i in range(block.relations.size()):
			block.relations[i].metaData.version += 1

	else:
		return True #Skip this block

	#Check changeset value is consistent
	for i in range(block.nodes.size()):
		if block.nodes[i].metaData.changeset != int(changesetId):
			return HttpResponseBadRequest("Changeset does not match expected value", content_type="text/plain")
	for i in range(block.ways.size()):
		if block.ways[i].metaData.changeset != int(changesetId):
			return HttpResponseBadRequest("Changeset does not match expected value", content_type="text/plain")
	for i in range(block.relations.size()):
		if block.relations[i].metaData.changeset != int(changesetId):
			return HttpResponseBadRequest("Changeset does not match expected value", content_type="text/plain")

	#Get list of modified objects, check they are unique
	modNodeIdSet, modWayIdSet, modRelationIdSet = set(), set(), set()
	for i in range(block.nodes.size()):
		node = block.nodes[i]
		if node.objId in modNodeIdSet:
			return HttpResponseBadRequest("Modified object ID is not unique", content_type="text/plain")
		modNodeIdSet.add(node.objId)
	for i in range(block.ways.size()):
		way = block.ways[i]
		if way.objId in modWayIdSet:
			return HttpResponseBadRequest("Modified object ID is not unique", content_type="text/plain")
		modWayIdSet.add(way.objId)
	for i in range(block.relations.size()):
		relation = block.relations[i]
		if relation.objId in modRelationIdSet:
			return HttpResponseBadRequest("Modified object ID is not unique", content_type="text/plain")
		modRelationIdSet.add(relation.objId)

	#Get list of referenced objects
	refedNodes, refedWays, refedRelations = set(), set(), set()
	for i in range(block.nodes.size()):
		node = block.nodes[i]
		refedNodes.add(node.objId)
	for i in range(block.ways.size()):
		way = block.ways[i]
		refedWays.add(way.objId)
		for ref in way.refs:
			refedNodes.add(ref)
	for i in range(block.relations.size()):
		relation = block.relations[i]
		refedRelations.add(relation.objId)
		for i, refId in enumerate(relation.refIds):
			refTypeStr = relation.refTypeStrs[i]
			if refTypeStr == "node":
				refedNodes.add(refId)
			if refTypeStr == "way":
				refedWays.add(refId)
			if refTypeStr == "relation":
				refedRelations.add(refId)

	#Check referenced positive ID objects already exist (to ensure
	#non existent nodes or ways are not added to ways or relations).
	posRefedNodes = [objId for objId in refedNodes if objId>0]
	posRefedWays = [objId for objId in refedWays if objId>0]
	posRefedRelations = [objId for objId in refedRelations if objId>0]

	foundNodeData = pgmap.OsmData()
	t.GetObjectsById(b"node", posRefedNodes, foundNodeData)
	foundNodeIndex = GetOsmDataIndex(foundNodeData)["node"]
	if set(posRefedNodes) != set(foundNodeIndex.keys()):
		return HttpResponseNotFound("Referenced node(s) not found")

	foundWayData = pgmap.OsmData()
	t.GetObjectsById(b"way", posRefedWays, foundWayData)
	foundWayIndex = GetOsmDataIndex(foundWayData)["way"]
	if set(posRefedWays) != set(foundWayIndex.keys()):
		return HttpResponseNotFound("Referenced way(s) not found")

	foundRelationData = pgmap.OsmData()
	t.GetObjectsById(b"relation", posRefedRelations, foundRelationData)
	foundRelationIndex = GetOsmDataIndex(foundRelationData)["relation"]
	if set(posRefedRelations) != set(foundRelationIndex.keys()):
		return HttpResponseNotFound("Referenced relation(s) not found")
	
	#Check versions of updated/deleted objects match what we expect
	dataIndex = GetOsmDataIndex(block)
	nodeObjsById, wayObjsById, relationObjsById = dataIndex['node'], dataIndex['way'], dataIndex['relation']

	for objId in nodeObjsById:
		if nodeObjsById[objId].metaData.version > 1 and nodeObjsById[objId].metaData.version != foundNodeIndex[objId].metaData.version+1:
			return HttpResponse("Node has wrong version", status=409, content_type="text/plain")
	for objId in wayObjsById:
		if wayObjsById[objId].metaData.version > 1 and wayObjsById[objId].metaData.version != foundWayIndex[objId].metaData.version+1:
			return HttpResponse("Way has wrong version", status=409, content_type="text/plain")
	for objId in relationObjsById:
		if relationObjsById[objId].metaData.version > 1 and relationObjsById[objId].metaData.version != foundRelationIndex[objId].metaData.version+1:
			return HttpResponse("Relation has wrong version", status=409, content_type="text/plain")

	if action == "delete":

		#Check that deleting objects doesn't break anything

		parentRelationsForRelations = pgmap.OsmData()
		t.GetRelationsForObjs(b"relation", relationObjsById.keys(), parentRelationsForRelations)
		parentRelationsForRelationsIndex = GetOsmDataIndex(parentRelationsForRelations)["relation"]
		referencedChildren = {}
		for parentId in parentRelationsForRelationsIndex:
			if parentId in relationObjsById.keys():
				continue #This object is being deleted anyway
			parent = parentRelationsForRelationsIndex[parentId]
			for refTypeStr, refId in zip(parent.refTypeStrs, parent.refIds):
				if refTypeStr != "relation":
					continue
				if refId in relationObjsById.keys():
					referencedChildren[refId] = parent.objId
		if len(referencedChildren) > 0:
			if not ifunused:
				k, v = GetAnyKeyValue(referencedChildren)
				err = b"The relation #{} is used in relation #{}.".format(k, v)
				return HttpResponse(err, status=412, content_type="text/plain")
			else:
				filtered = pgmap.OsmData()
				for i in range(len(block.relations)):
					relation = block.relations[i]
					if relation.objId in referencedChildren:
						continue
					filtered.relations.append(relation)
				block.relations = filtered.relations
				relationsObjsById = GetOsmDataIndex(block)['relation']

		parentRelationsForWays = pgmap.OsmData()
		t.GetRelationsForObjs(b"way", wayObjsById.keys(), parentRelationsForWays)
		parentRelationsForWaysIndex = GetOsmDataIndex(parentRelationsForWays)["relation"]
		referencedChildren = {}
		for parentId in parentRelationsForWaysIndex:
			if parentId in relationObjsById.keys():
				continue #This object is being deleted anyway
			parent = parentRelationsForWaysIndex[parentId]
			for refTypeStr, refId in zip(parent.refTypeStrs, parent.refIds):
				if refTypeStr != "way":
					continue
				if refId in wayObjsById.keys():
					referencedChildren[refId] = parent.objId
		if len(referencedChildren) > 0:
			if not ifunused:
				k, v = GetAnyKeyValue(referencedChildren)
				err = b"Way #{} still used by relation #{}.".format(k, v)
				return HttpResponse(err, status=412, content_type="text/plain")
			else:
				filtered = pgmap.OsmData()
				for i in range(len(block.ways)):
					way = block.ways[i]
					if way.objId in referencedChildren:
						continue
					filtered.ways.append(way)
				block.ways = filtered.ways
				wayObjsById = GetOsmDataIndex(block)['way']

		parentWayForNodes = pgmap.OsmData()
		t.GetWaysForNodes(nodeObjsById.keys(), parentWayForNodes)
		parentWayForNodesIndex = GetOsmDataIndex(parentWayForNodes)["way"]
		referencedChildren = {}
		for parentId in parentWayForNodesIndex:
			if parentId in wayObjsById.keys():
				continue #This object is being deleted anyway
			parent = parentWayForNodesIndex[parentId]
			for ref in parent.refs:
				if ref in nodeObjsById.keys():
					referencedChildren[ref] = parent.objId
		if len(referencedChildren) > 0:
			if not ifunused:
				k, v = GetAnyKeyValue(referencedChildren)
				err = b"#{} is still used by way #{}.".format(k, v)
				return HttpResponse(err, status=412, content_type="text/plain")
			else:
				filtered = pgmap.OsmData()
				for i in range(len(block.nodes)):
					node = block.nodes[i]
					if node.objId in referencedChildren:
						continue
					filtered.nodes.append(node)
				block.nodes = filtered.nodes
				nodeObjsById = GetOsmDataIndex(block)['node']

		parentRelationsForNodes = pgmap.OsmData()
		t.GetRelationsForObjs(b"node", nodeObjsById.keys(), parentRelationsForNodes)
		parentRelationsForNodesIndex = GetOsmDataIndex(parentRelationsForNodes)["relation"]
		referencedChildren = {}
		for parentId in parentRelationsForNodesIndex:
			parent = parentRelationsForNodesIndex[parentId]
			for refTypeStr, refId in zip(parent.refTypeStrs, parent.refIds):
				if refTypeStr != "node":
					continue
				if refId in nodeObjsById.keys():
					referencedChildren[refId] = parent.objId
		if len(referencedChildren) > 0:
			if not ifunused:
				k, v = GetAnyKeyValue(referencedChildren)
				err = b"Node #{} is still used by relation #{}.".format(k, v)
				return HttpResponse(err, status=412, content_type="text/plain")
			else:
				filtered = pgmap.OsmData()
				for i in range(len(block.nodes)):
					node = block.nodes[i]
					if node.objId in referencedChildren:
						continue
					filtered.nodes.append(node)
				block.nodes = filtered.nodes
				nodeObjsById = GetOsmDataIndex(block)['node']


	#Get complete set of query objects based on modified objects
	#TODO
	if action in ["modify", "delete"]:
		#Get complete set of query objects for original data
		existingAffectedObjects = pgmap.OsmData()
		t.GetAffectedObjects(block, existingAffectedObjects)

	#Set visiblity flag
	visible = action != "delete"
	for i in range(block.nodes.size()):
		block.nodes[i].metaData.visible = visible
	for i in range(block.ways.size()):
		block.ways[i].metaData.visible = visible
	for i in range(block.relations.size()):
		block.relations[i].metaData.visible = visible

	#Set user info
	for i in range(block.nodes.size()):
		block.nodes[i].metaData.uid = uid
		block.nodes[i].metaData.username = username.encode("UTF-8")
		block.nodes[i].metaData.timestamp = int(timestamp)
	for i in range(block.ways.size()):
		block.ways[i].metaData.uid = uid
		block.ways[i].metaData.username = username.encode("UTF-8")
		block.ways[i].metaData.timestamp = int(timestamp)
	for i in range(block.relations.size()):
		block.relations[i].metaData.uid = uid
		block.relations[i].metaData.username = username.encode("UTF-8")
		block.relations[i].metaData.timestamp = int(timestamp)

	errStr = pgmap.PgMapError()
	ok = t.StoreObjects(block, createdNodeIds, createdWayIds, createdRelationIds, False, errStr)
	if not ok:
		return HttpResponseServerError(errStr.errStr, content_type='text/plain')

	#Update diff result	
	upload_update_diff_result(action, "node", block.nodes, createdNodeIds, responseRoot)
	upload_update_diff_result(action, "way", block.ways, createdWayIds, responseRoot)
	upload_update_diff_result(action, "relation", block.relations, createdRelationIds, responseRoot)
	
	#Update changeset bbox based on edits
	#TODO

	return True

@csrf_exempt
@api_view(['PUT'])
@permission_classes((IsAuthenticated, ))
@parser_classes((DefusedXmlParser, ))
def create(request):

	userRecord = request.user

	changeset = pgmap.PgChangeset()
	errStr = pgmap.PgMapError()

	unicodeTags = {}
	csIn = request.data.find("changeset")
	for tag in csIn.findall("tag"):
		unicodeTags[tag.attrib["k"]] = tag.attrib["v"]
	if not CheckTags(unicodeTags):
		return HttpResponseBadRequest("Invalid tags")

	for tag in unicodeTags:
		changeset.tags[tag.encode("utf-8")] = unicodeTags[tag].encode("utf-8")
	changeset.uid = request.user.id
	changeset.username = request.user.username.encode("utf-8")

	t = p.GetTransaction(b"EXCLUSIVE")

	changeset.open_timestamp = int(time.time())

	cid = t.CreateChangeset(changeset, errStr)
	if cid == 0:
		t.Abort()
		return HttpResponseServerError(errStr.errStr)

	t.Commit()
	return HttpResponse(cid, content_type='text/plain')

@csrf_exempt
@api_view(['GET', 'PUT'])
@permission_classes((IsAuthenticatedOrReadOnly, ))
@parser_classes((DefusedXmlParser, ))
def changeset(request, changesetId):
	include_discussion = request.GET.get('include_discussion', 'false') == "true"

	if request.method == 'GET':
		t = p.GetTransaction(b"ACCESS SHARE")
	else:
		t = p.GetTransaction(b"EXCLUSIVE")
	
	changesetData = pgmap.PgChangeset()
	errStr = pgmap.PgMapError()
	ret = t.GetChangeset(int(changesetId), changesetData, errStr)
	if ret == -1:
		return HttpResponseNotFound("Changeset not found")
	if ret == 0:	
		return HttpResponseServerError(errStr.errStr)

	if request.method == 'GET':
		t.Commit()

		return SerializeChangesets([changesetData], include_discussion)

	if request.method == 'PUT':
		
		if request.user.id != changesetData.uid:
			return HttpResponse("This changeset belongs to a different user", status=409, content_type="text/plain")

		if not changesetData.is_open:
			err = "The changeset {} was closed at {}.".format(changesetData.objId, 
				datetime.datetime.fromtimestamp(changesetData.close_timestamp).isoformat())
			response = HttpResponse(err, content_type="text/plain")
			response.status_code = 409
			return response

		unicodeTags = {}
		csIn = request.data.find("changeset")
		for tag in csIn.findall("tag"):
			unicodeTags[tag.attrib["k"]] = tag.attrib["v"]
		if not CheckTags(unicodeTags):
			return HttpResponseBadRequest("Invalid tags")

		changesetData.tags = pgmap.mapstringstring()
		for tag in unicodeTags:
			changesetData.tags[tag.encode("utf-8")] = unicodeTags[tag].encode("utf-8")

		ok = t.UpdateChangeset(changesetData, errStr)
		if not ok:
			t.Abort()
			return HttpResponseServerError(errStr.errStr)

		t.Commit()

		return SerializeChangesets([changesetData])

@csrf_exempt
@api_view(['PUT'])
@permission_classes((IsAuthenticated, ))
def close(request, changesetId):
	t = p.GetTransaction(b"EXCLUSIVE")

	changesetData = pgmap.PgChangeset()
	errStr = pgmap.PgMapError()
	ret = t.GetChangeset(int(changesetId), changesetData, errStr)
	if ret == -1:
		return HttpResponseNotFound("Changeset not found")
	if ret == 0:	
		return HttpResponseServerError(errStr.errStr)

	if not changesetData.is_open:
		err = "The changeset {} was closed at {}.".format(changesetData.objId, 
			datetime.datetime.fromtimestamp(changesetData.close_timestamp).isoformat())
		response = HttpResponse(err, content_type="text/plain")
		response.status_code = 409
		return response

	if request.user.id != changesetData.uid:
		return HttpResponse("This changeset belongs to a different user", status=409, content_type="text/plain")

	t.CloseChangeset(int(changesetId), int(time.time()), errStr)
	t.Commit()

	return SerializeChangesets([changesetData])

@api_view(['GET'])
@permission_classes((IsAuthenticatedOrReadOnly, ))
def download(request, changesetId):
	t = p.GetTransaction(b"ACCESS SHARE")
	
	osmChange = pgmap.OsmChange()
	errStr = pgmap.PgMapError()
	ret = t.GetChangesetOsmChange(int(changesetId), osmChange, errStr)
	if ret == -1:
		return HttpResponseNotFound("Changeset not found")
	if ret == 0:	
		return HttpResponseServerError(errStr.errStr)

	t.Commit()

	#print (changesetData.data.empty())
	sio = StringIO.StringIO()
	outBufWrapped = pgmap.CPyOutbuf(sio)
	pgmap.SaveToOsmChangeXml(osmChange, outBufWrapped)

	return HttpResponse(sio.getvalue(), content_type='text/xml')

@csrf_exempt
@api_view(['POST'])
@permission_classes((IsAuthenticated, ))
@parser_classes((DefusedXmlParser, ))
def expand_bbox(request, changesetId):
	t = p.GetTransaction(b"EXCLUSIVE")

	changesetData = pgmap.PgChangeset()
	errStr = pgmap.PgMapError()
	ret = t.GetChangeset(int(changesetId), changesetData, errStr)
	if ret == -1:
		return HttpResponseNotFound("Changeset not found")
	if ret == 0:	
		return HttpResponseServerError(errStr.errStr)

	if request.user.id != changesetData.uid:
		return HttpResponse("This changeset belongs to a different user", status=409, content_type="text/plain")

	if not changesetData.is_open:
		err = "The changeset {} was closed at {}.".format(changesetData.objId, 
			datetime.datetime.fromtimestamp(changesetData.close_timestamp).isoformat())
		response = HttpResponse(err, content_type="text/plain")
		response.status_code = 409
		return response

	for node in request.data.findall("node"):
		if not changesetData.bbox_set:
			changesetData.y1 = float(node.attrib["lat"])
			changesetData.y2 = float(node.attrib["lat"])
			changesetData.x1 = float(node.attrib["lon"])
			changesetData.x2 = float(node.attrib["lon"])
			changesetData.bbox_set = True
		else:
			if float(node.attrib["lat"]) < changesetData.y1: changesetData.y1 = float(node.attrib["lat"])
			if float(node.attrib["lat"]) > changesetData.y2: changesetData.y2 = float(node.attrib["lat"])
			if float(node.attrib["lon"]) < changesetData.x1: changesetData.x1 = float(node.attrib["lon"])
			if float(node.attrib["lon"]) > changesetData.x2: changesetData.x2 = float(node.attrib["lon"])

	ok = t.UpdateChangeset(changesetData, errStr)
	if not ok:
		t.Abort()
		return HttpResponseServerError(errStr.errStr)

	t.Commit()

	return SerializeChangesets([changesetData])

@api_view(['GET'])
def list(request):

	bbox = request.GET.get('bbox', None) #min_lon,min_lat,max_lon,max_lat
	user = request.GET.get('user', None)
	display_name = request.GET.get('display_name', None)
	timearg = request.GET.get('time', None)
	openarg = request.GET.get('open', None)
	close = request.GET.get('closed', None)
	changesets = request.GET.get('changesets', None)

	changesets = pgmap.vectorchangeset()
	errStr = pgmap.PgMapError()
	t = p.GetTransaction(b"ACCESS SHARE")
	ok = t.GetChangesets(changesets, errStr)

	t.Commit()

	changesetLi = []
	for i in range(len(changesets)):
		changesetLi.append(changesets[i])
	return SerializeChangesets(changesetLi)

@csrf_exempt
@api_view(['POST'])
@permission_classes((IsAuthenticated, ))
@parser_classes((OsmChangeXmlParser, ))
def upload(request, changesetId):

	#Check changeset is open and for this user
	t = p.GetTransaction(b"EXCLUSIVE")
	
	changesetData = pgmap.PgChangeset()
	errStr = pgmap.PgMapError()
	ret = t.GetChangeset(int(changesetId), changesetData, errStr)
	if ret == -1:
		return HttpResponseNotFound("Changeset not found")
	if ret == 0:	
		return HttpResponseServerError(errStr.errStr)

	if not changesetData.is_open:
		err = "The changeset {} was closed at {}.".format(changesetData.id, changesetData.close_datetime.isoformat())
		response = HttpResponse(err, content_type="text/plain")
		response.status_code = 409
		return response

	if request.user.id != changesetData.uid:
		return HttpResponse("This changeset belongs to a different user", status=409, content_type="text/plain")

	#Prepare diff result xml
	responseRoot = ET.Element('diffResult')
	doc = ET.ElementTree(responseRoot)
	responseRoot.attrib["version"] = str(settings.API_VERSION)
	responseRoot.attrib["generator"] = settings.GENERATOR

	createdNodeIds = pgmap.mapi64i64()
	createdWayIds = pgmap.mapi64i64()
	createdRelationIds = pgmap.mapi64i64()

	for i in range(request.data.blocks.size()):
		action = request.data.actions[i]
		block = request.data.blocks[i]
		ifunused = request.data.ifunused[i]
		timestamp = time.time()

		ret = upload_block(action, block, changesetId, t, responseRoot, 
			request.user.id, request.user.username, timestamp,
			createdNodeIds, createdWayIds, createdRelationIds, ifunused)
		if ret != True:
			print (ret)
			return ret

	t.Commit()

	sio = StringIO.StringIO()
	doc.write(sio, "utf-8")
	return HttpResponse(sio.getvalue(), content_type='text/xml')

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

