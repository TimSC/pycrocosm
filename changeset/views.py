# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from __future__ import print_function

from django.shortcuts import render
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseNotFound, HttpResponseServerError, HttpResponseForbidden
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ObjectDoesNotExist
from rest_framework.permissions import IsAuthenticated, IsAuthenticatedOrReadOnly
from rest_framework.decorators import api_view, permission_classes, parser_classes

import xml.etree.ElementTree as ET
import sys
import datetime
import pgmap
import time
import io
from pycrocosm import common
from querymap.views import p
from pycrocosm.parsers import DefusedXmlParser, OsmChangeXmlParser
from migrateusers.models import LegacyAccount
PY3 = sys.version_info > (3, 0)

# Create your views here.

def CheckTags(tags):
	for k in tags:
		if len(k) > settings.MAX_TAG_LENGTH:
			return False
		if len(tags[k]) > settings.MAX_TAG_LENGTH:
			return False
	return True

def DecodeIfNotUnicode(s):
	if PY3:
		unicodeType = str
	else:
		unicodeType = unicode
	if isinstance(s, unicodeType):
		return s
	return s.decode('utf-8')

def SerializeChangesetToElement(changesetData, include_discussion=False):

	changeset = ET.Element("changeset")
	changeset.attrib["id"] = str(changesetData.objId)
	if len(changesetData.username) > 0:
		changeset.attrib["user"] = DecodeIfNotUnicode(changesetData.username)
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
		tag.attrib["k"] = DecodeIfNotUnicode(tagKey)
		tag.attrib["v"] = DecodeIfNotUnicode(changesetData.tags[tagKey])

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
	for key, value in zip(["generator", "copyright", "attribution", "license"], 
		[settings.GENERATOR, settings.COPYRIGHT, settings.ATTRIBUTION, settings.LICENSE]):
		if len(value) > 0:
			root.attrib[key] = value

	for changesetData in changesetsData:
		root.append(SerializeChangesetToElement(changesetData, include_discussion))

	doc = ET.ElementTree(root)
	sio = io.BytesIO()
	doc.write(sio, str("UTF-8")) # str work around https://bugs.python.org/issue15811
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
		if obj.metaData.version != 0 and obj.metaData.version != 1:
			return HttpResponseBadRequest("Version for created objects must be null, zero or one", content_type="text/plain")
		if isinstance(obj, pgmap.OsmNode):
			if obj.lat < -90.0 or obj.lat > 90 or obj.lon < -180.0 or obj.lon > 180.0:
				return HttpResponseBadRequest("Node outside valid range", content_type="text/plain")
		for k in obj.tags:
			if len(k) > 255:
				return HttpResponseBadRequest("Tag key is too long", content_type="text/plain")
			if len(obj.tags[k]) > 255:
				return HttpResponseBadRequest("Tag value is too long", content_type="text/plain")

	return None

def upload_check_way_mems(action, objs):
	for i in range(objs.size()):
		obj = objs[i]
		if action != "delete" and len(obj.refs) < 2:
			return HttpResponseBadRequest("Way has too few nodes", content_type="text/plain")
		if len(obj.refs) > settings.WAYNODES_MAXIMUM:
			return HttpResponseBadRequest("Way has too many nodes", content_type="text/plain")
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

def upload_update_diff_result(action, objType, objs, createdIds):

	diffs = []
	for i in range(objs.size()):
		obj = objs[i]
		diff = {'objType': objType}
		diff["old_id"] = str(obj.objId)
		if action == "create":
			diff["new_id"] = str(createdIds[obj.objId])
			diff["new_version"] = str(obj.metaData.version)
			obj.objId = createdIds[obj.objId]
		if action == "modify":
			diff["new_id"] = str(obj.objId)
			diff["new_version"] = str(obj.metaData.version)
		diffs.append(diff)
		
	return diffs

def upload_update_diff_result2(diffs, responseRoot):
	for diff in diffs:

		comment = ET.SubElement(responseRoot, diff['objType'])
		comment.attrib["old_id"] = str(diff['old_id'])
		if 'new_id' in diff:
			comment.attrib["new_id"] = str(diff['new_id'])
		if 'new_version' in diff:
			comment.attrib["new_version"] = str(diff['new_version'])

def track_bboxes_step2(action, block, t, affectedParents):
	errStr = pgmap.PgMapError()
	ok = True

	affectedWayIds = pgmap.seti64()
	for i in range(block.ways.size()):
		way = block.ways[i]
		affectedWayIds.add(way.objId)
	affectedRelIds = pgmap.seti64()
	for i in range(block.relations.size()):
		rel = block.relations[i]
		affectedRelIds.add(rel.objId)

	if action in ["modify", "delete"]:
		#Ensure active tables have copies of any affected parents
		unusedNodeIds = pgmap.mapi64i64()
		unusedWayIds = pgmap.mapi64i64()
		unusedRelationIds = pgmap.mapi64i64()

		ok = t.StoreObjects(affectedParents, unusedNodeIds, unusedWayIds, unusedRelationIds, False, errStr)
		if not ok:
			return HttpResponseServerError(errStr.errStr, content_type='text/plain')

		#Update bbox of any affected parents
		for i in range(affectedParents.ways.size()):
			way = affectedParents.ways[i]
			affectedWayIds.add(way.objId)

		for i in range(affectedParents.relations.size()):
			rel = affectedParents.relations[i]
			affectedRelIds.add(rel.objId)

	t.UpdateObjectBboxesById("way", affectedWayIds, False, False, errStr)

	t.UpdateObjectBboxesById("relation", affectedRelIds, False, False, errStr)

	return ok, errStr

def store_objects_with_bbox_tracking(action, block, t, createdNodeIds, createdWayIds, createdRelationIds):

	#Get complete set of query objects based on modified objects (unless action is create)
	affectedParents = pgmap.OsmData()

	if action in ["modify", "delete"]:
		#Get complete set of query objects for original data
		t.GetAffectedParents(block, affectedParents)

	errStr = pgmap.PgMapError()
	ok = t.StoreObjects(block, createdNodeIds, createdWayIds, createdRelationIds, False, errStr)
	if not ok:
		return False, None, affectedParents, errStr

	diffs = upload_update_diff_result(action, "node", block.nodes, createdNodeIds)
	diffs.extend(upload_update_diff_result(action, "way", block.ways, createdWayIds))
	diffs.extend(upload_update_diff_result(action, "relation", block.relations, createdRelationIds))

	#Update affected bounding boxes
	track_bboxes_step2(action, block, t, affectedParents)

	return ok, diffs, affectedParents, errStr

def get_object_type_id_vers(block):
	objTypes, objIdVers = [], []

	for i in range(block.nodes.size()):
		obj = block.nodes[i]
		objTypes.append("node")
		objIdVers.append((obj.objId, obj.metaData.version))
	for i in range(block.ways.size()):
		obj = block.ways[i]
		objTypes.append("way")
		objIdVers.append((obj.objId, obj.metaData.version))
	for i in range(block.relations.size()):
		obj = block.relations[i]
		objTypes.append("relation")
		objIdVers.append((obj.objId, obj.metaData.version))

	return objTypes, objIdVers

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

	ret = upload_check_way_mems(action, block.ways)
	if ret is not None: return ret

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

	#Get list of pre-existing objects that are modified
	preexistingNodeIds, preexistingWayIds, preexistingRelationIds = set(), set(), set()
	for i in range(block.nodes.size()):
		node = block.nodes[i]
		if node.objId > 0:
			preexistingNodeIds.add(node.objId)
	for i in range(block.ways.size()):
		way = block.ways[i]
		if way.objId > 0:
			preexistingWayIds.add(way.objId)
	for i in range(block.relations.size()):
		relation = block.relations[i]
		if relation.objId > 0:
			preexistingRelationIds.add(relation.objId)

	#Get original positions of modified objects.
	originalObjData = pgmap.OsmData()
	t.GetObjectsById("node", list(preexistingNodeIds), originalObjData)
	t.GetObjectsById("way", list(preexistingWayIds), originalObjData)
	t.GetObjectsById("relation", list(preexistingRelationIds), originalObjData)

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

	refedObjData = pgmap.OsmData()
	t.GetObjectsById("node", posRefedNodes, refedObjData)
	t.GetObjectsById("way", posRefedWays, refedObjData)
	t.GetObjectsById("relation", posRefedRelations, refedObjData)

	refedObjIndex = GetOsmDataIndex(refedObjData)
	
	foundNodeIndex = refedObjIndex["node"]
	if set(posRefedNodes) != set(foundNodeIndex.keys()):
		return HttpResponseNotFound("Referenced node(s) not found")

	foundWayIndex = refedObjIndex["way"]
	if set(posRefedWays) != set(foundWayIndex.keys()):
		return HttpResponseNotFound("Referenced way(s) not found")

	foundRelationIndex = refedObjIndex["relation"]
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
		t.GetRelationsForObjs("relation", list(relationObjsById.keys()), parentRelationsForRelations)
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
				err = "The relation #{} is used in relation #{}.".format(k, v)
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
		t.GetRelationsForObjs("way", list(wayObjsById.keys()), parentRelationsForWays)
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
				err = "Way #{} still used by relation #{}.".format(k, v)
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
		t.GetWaysForNodes(list(nodeObjsById.keys()), parentWayForNodes)
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
				err = "#{} is still used by way #{}.".format(k, v)
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
		t.GetRelationsForObjs("node", list(nodeObjsById.keys()), parentRelationsForNodes)
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
				err = "Node #{} is still used by relation #{}.".format(k, v)
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
		block.nodes[i].metaData.username = username
		block.nodes[i].metaData.timestamp = int(timestamp)
	for i in range(block.ways.size()):
		block.ways[i].metaData.uid = uid
		block.ways[i].metaData.username = username
		block.ways[i].metaData.timestamp = int(timestamp)
	for i in range(block.relations.size()):
		block.relations[i].metaData.uid = uid
		block.relations[i].metaData.username = username
		block.relations[i].metaData.timestamp = int(timestamp)

	ok, diffs, affectedParents, errStr = store_objects_with_bbox_tracking(action, block, t, createdNodeIds, createdWayIds, createdRelationIds)
	if not ok:
		raise HttpResponseServerError(errStr, content_type='text/plain')

	#Update diff result	
	upload_update_diff_result2(diffs, responseRoot)
	
	#Get related objects (children of affected parents that remain unmodified)
	relatedNodeIds = set()
	knownNodeIds = set()
	for i in range(block.nodes.size()):
		knownNodeIds.add(block.nodes[i].objId)
	for i in range(block.ways.size()):
		way = block.ways[i]
		for ref in way.refs:
			relatedNodeIds.add(ref)
	for i in range(affectedParents.ways.size()):
		way = affectedParents.ways[i]
		for ref in way.refs:
			relatedNodeIds.add(ref)
	relatedNodeIds = relatedNodeIds - knownNodeIds
	relatedObjs = pgmap.OsmData()
	t.GetObjectsById("node", list(relatedNodeIds), relatedObjs)

	#Update changeset bbox based on edits
	# "Nodes: Any change to a node, including deletion, adds the node's old and new location to the bbox.
	# Ways: Any change to a way, including deletion, adds all of the way's nodes to the bbox.
    # Relations:
	#   adding or removing nodes or ways from a relation causes them to be added to the changeset bounding box.
    #   adding a relation member or changing tag values causes all node and way members to be added to the bounding box.
    #   this is similar to how the map call does things and is reasonable on the assumption that adding or removing members doesn't materially change the rest of the relation."
	#TODO

	existingObjTypes, existingObjIdVers = get_object_type_id_vers(originalObjData)
	modifiedObjTypes, modifiedObjIdVers = get_object_type_id_vers(block)
	affectedParentsTypes, affectedParentsIdVers = get_object_type_id_vers(affectedParents)
	relatedObjsTypes, relatedObjsIdVers = get_object_type_id_vers(relatedObjs)

	#Track edit volumes
	errStr = pgmap.PgMapError()
	bbox = pgmap.vectord()
	ok = t.InsertEditActivity(int(changesetId),
		int(timestamp),
		uid,
		bbox,
		action,
		len(block.nodes),
		len(block.ways),
		len(block.relations),
		existingObjTypes, existingObjIdVers,
		modifiedObjTypes, modifiedObjIdVers,
		affectedParentsTypes, affectedParentsIdVers,
		relatedObjsTypes, relatedObjsIdVers,
		errStr)
	if not ok: print (errStr.errStr)

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
		changeset.tags[tag] = unicodeTags[tag]
	changeset.uid = request.user.id
	changeset.username = request.user.username

	t = p.GetTransaction("EXCLUSIVE")

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
		t = p.GetTransaction("ACCESS SHARE")
	else:
		t = p.GetTransaction("EXCLUSIVE")
	
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
			changesetData.tags[tag] = unicodeTags[tag]

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
	t = p.GetTransaction("EXCLUSIVE")

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
	t = p.GetTransaction("ACCESS SHARE")
	
	osmChange = pgmap.OsmChange()
	errStr = pgmap.PgMapError()
	ret = t.GetChangesetOsmChange(int(changesetId), osmChange, errStr)
	if ret == -1:
		return HttpResponseNotFound("Changeset not found")
	if ret == 0:	
		return HttpResponseServerError(errStr.errStr)

	t.Commit()

	#print (changesetData.data.empty())
	sio = io.BytesIO()
	outBufWrapped = pgmap.CPyOutbuf(sio)
	pgmap.SaveToOsmChangeXml(osmChange, True, outBufWrapped)

	return HttpResponse(sio.getvalue(), content_type='text/xml')

@csrf_exempt
@api_view(['POST'])
@permission_classes((IsAuthenticated, ))
@parser_classes((DefusedXmlParser, ))
def expand_bbox(request, changesetId):

	return HttpResponse("Depricated December 2019", status=410, content_type="text/plain")

@api_view(['GET'])
def list_changesets(request):
	bbox = request.GET.get('bbox', None) #min_lon,min_lat,max_lon,max_lat
	user_uid = int(request.GET.get('user', 0))
	display_name = request.GET.get('display_name', None)
	timearg = request.GET.get('time', None)
	isOpenOnly = request.GET.get('open', 'false') == 'true'
	isClosedOnly = request.GET.get('closed', 'false') == 'true'
	changesetsToGet = request.GET.get('changesets', None)

	#Check display_name or uid actually exists
	if display_name is not None:
		try:
			user = User.objects.get(username=display_name)
			user_uid = user.id
		except ObjectDoesNotExist:
			try:
				user = LegacyAccount.objects.get(username=display_name)
				user_uid = user.uid			
			except ObjectDoesNotExist:
				return HttpResponseNotFound("User not found")

	closedAfter = -1
	openedBefore = -1
	if timearg is not None:
		timeargSplit = timearg.split(",")
		if len(timeargSplit) >= 1:
			closedAfter = int(timeargSplit[0])
		if len(timeargSplit) >= 2:
			openedBefore = int(timeargSplit[1])

	changesets = pgmap.vectorchangeset()
	errStr = pgmap.PgMapError()
	t = p.GetTransaction("ACCESS SHARE")
	ok = t.GetChangesets(changesets, int(user_uid), closedAfter, openedBefore, 
		isOpenOnly, isClosedOnly, errStr)

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
	t = p.GetTransaction("EXCLUSIVE")
	
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

	sio = io.BytesIO()
	doc.write(sio, str("UTF-8")) # str work around https://bugs.python.org/issue15811
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



