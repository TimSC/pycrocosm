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
import re
import math
import datetime
import json
import pgmap
import time
import io
from pycrocosm import common
from pycrocosm.mapdb import get_pgmap
from pycrocosm.parsers import DefusedXmlParser, OsmChangeXmlParser
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

def ChangesetCounts(changesetData):
	"""The count attributes of a changeset, in the order they are written.

	The edit counts are those pgmap's GetChangesetChangeCounts found.
	"""
	created = changesetData.created_count
	modified = changesetData.modified_count
	deleted = changesetData.deleted_count
	return [
		# Changeset comments are not stored yet, so there are never any
		("comments_count", 0),
		("changes_count", created + modified + deleted),
		("created_count", created),
		("modified_count", modified),
		("deleted_count", deleted),
	]

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
	for name, count in ChangesetCounts(changesetData):
		changeset.attrib[name] = str(count)

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

def JsonTimestamp(timestamp):
	return datetime.datetime.fromtimestamp(timestamp, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def SerializeChangesetToDict(changesetData, include_discussion=False):
	changeset = {"id": changesetData.objId}
	if changesetData.open_timestamp != 0:
		changeset["created_at"] = JsonTimestamp(changesetData.open_timestamp)
	changeset["open"] = bool(changesetData.is_open)
	if not changesetData.is_open and changesetData.close_timestamp != 0:
		changeset["closed_at"] = JsonTimestamp(changesetData.close_timestamp)
	if changesetData.bbox_set:
		changeset["min_lat"] = changesetData.y1
		changeset["min_lon"] = changesetData.x1
		changeset["max_lat"] = changesetData.y2
		changeset["max_lon"] = changesetData.x2
	if changesetData.uid != 0:
		changeset["uid"] = changesetData.uid
	if len(changesetData.username) > 0:
		changeset["user"] = DecodeIfNotUnicode(changesetData.username)
	changeset.update(ChangesetCounts(changesetData))
	if len(changesetData.tags) > 0:
		changeset["tags"] = {DecodeIfNotUnicode(key): DecodeIfNotUnicode(changesetData.tags[key])
			for key in changesetData.tags}
	if include_discussion:
		# Changeset comments are not stored yet, so there are none to list
		changeset["comments"] = []
	return changeset

def SerializeChangesets(changesetsData, include_discussion=False, request=None, single=False):
	"""Respond with changesets as XML, or as JSON if the request asked for it.

	single selects the JSON layout for one changeset, which is an object
	instead of a list.
	"""
	if request is not None and common.wants_json(request):
		changesets = [SerializeChangesetToDict(c, include_discussion) for c in changesetsData]
		if single:
			return common.json_response({"changeset": changesets[0]})
		return common.json_response({"changesets": changesets})

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
			return HttpResponseBadRequest("WAYNODES_MAXIMUM limit exceeded; maximum is {}, got {}".format(
				settings.WAYNODES_MAXIMUM, len(obj.refs)), content_type="text/plain")
	return None

def upload_check_relation_mems(objs):
	for i in range(objs.size()):
		obj = objs[i]
		if len(obj.members) > settings.RELATION_MEMBERS_MAXIMUM:
			return HttpResponseBadRequest("RELATION_MEMBERS_MAXIMUM limit exceeded; maximum is {}, got {}".format(
				settings.RELATION_MEMBERS_MAXIMUM, len(obj.members)), content_type="text/plain")
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

def calc_bbox_of_nodes(nodes, outerBbox=[]):
	nlats, nlons = [], []
	if len(outerBbox) > 0:
		nlats, nlons = [outerBbox[1], outerBbox[3]], [outerBbox[0], outerBbox[2]]
	for i in range(nodes.size()):
		node = nodes[i]
		nlats.append(node.lat)
		nlons.append(node.lon)
	if len(nlats) > 0:
		outerBbox = [min(nlons), min(nlats), max(nlons), max(nlats)]

	return outerBbox

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

def get_relation_members(relation):
	chNodes, chWays, chRelations = set(), set(), set()
	for member in relation.members:
		refTypeStr, refId = member.TypeName(), member.ref
		if refTypeStr == "node":
			chNodes.add(refId)
		if refTypeStr == "way":
			chWays.add(refId)
		if refTypeStr == "relation":
			chRelations.add(refId)

	return chNodes, chWays, chRelations

def get_multi_relation_members(relations):
	chNodes, chWays, chRelations = set(), set(), set()
	for i in range(relations.size()):
		relation = relations[i]
		n, w, r = get_relation_members(relation)
		chNodes.update(n)
		chWays.update(w)
		chRelations.update(r)

	return chNodes, chWays, chRelations

def get_refed_members(block):
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
	n, w, r = get_multi_relation_members(block.relations)
	refedNodes.update(n)
	refedWays.update(w)
	refedRelations.update(r)

	return refedNodes, refedWays, refedRelations

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
	ret = upload_check_relation_mems(block.relations)
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
	refedNodes, refedWays, refedRelations = get_refed_members(block)
	
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
			for refTypeStr, refId in ((member.TypeName(), member.ref) for member in parent.members):
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
			for refTypeStr, refId in ((member.TypeName(), member.ref) for member in parent.members):
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
			for refTypeStr, refId in ((member.TypeName(), member.ref) for member in parent.members):
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
	relatedNodeIds, relatedWayIds, relatedRelationIds = set(), set(), set()
	knownNodeIds, knownWayIds, knownRelationIds = set(), set(), set()
	for i in range(block.nodes.size()):
		knownNodeIds.add(block.nodes[i].objId)
	for i in range(block.ways.size()):
		knownWayIds.add(block.ways[i].objId)
	for i in range(block.relations.size()):
		knownRelationIds.add(block.relations[i].objId)
	assert affectedParents.nodes.size() == 0 #Nodes are not parents
	for i in range(affectedParents.ways.size()):
		knownWayIds.add(affectedParents.ways[i].objId)
	for i in range(affectedParents.relations.size()):
		knownRelationIds.add(affectedParents.relations[i].objId)

	if action == "create":
		for i in range(refedObjData.nodes.size()):
			node = refedObjData.nodes[i]
			relatedNodeIds.add(node.objId)
		for i in range(refedObjData.ways.size()):
			way = refedObjData.ways[i]
			relatedWayIds.add(way.objId)
		for i in range(refedObjData.relations.size()):
			rel = refedObjData.relations[i]
			relatedRelationIds.add(rel.objId)
	elif action == "modify":
		for i in range(block.ways.size()):
			way = block.ways[i]
			for ref in way.refs:
				relatedNodeIds.add(ref)
		for i in range(affectedParents.ways.size()):
			way = affectedParents.ways[i]
			for ref in way.refs:
				relatedNodeIds.add(ref)

	relatedNodeIds = relatedNodeIds - knownNodeIds
	relatedWayIds = relatedWayIds - knownWayIds
	relatedRelationIds = relatedRelationIds - knownRelationIds
	relatedObjs = pgmap.OsmData()
	t.GetObjectsById("node", list(relatedNodeIds), relatedObjs)
	t.GetObjectsById("way", list(relatedWayIds), relatedObjs)
	t.GetObjectsById("relation", list(relatedRelationIds), relatedObjs)

	#Update changeset bbox based on edits
	# "Nodes: Any change to a node, including deletion, adds the node's old and new location to the bbox.
	# Ways: Any change to a way, including deletion, adds all of the way's nodes to the bbox.
    # Relations:
	#   adding or removing nodes or ways from a relation causes them to be added to the changeset bounding box.
    #   adding a relation as a member or changing tag values causes all node and way members to be added to the bounding box.
    #   this is similar to how the map call does things and is reasonable on the assumption that adding or removing members doesn't materially change the rest of the relation."
	outerBbox = calc_bbox_of_nodes(originalObjData.nodes)
	outerBbox = calc_bbox_of_nodes(block.nodes, outerBbox)
	wayNodeIds = set()
	wayNodes = pgmap.OsmData()
	for i in range(block.ways.size()): #Get unmodified nodes of ways
		way = block.ways[i]
		for ref in way.refs:
			wayNodeIds.add(ref)
	t.GetObjectsById("node", list(wayNodeIds), wayNodes)
	outerBbox = calc_bbox_of_nodes(wayNodes.nodes, outerBbox)

	if action == "modifiy":
		originalObjIndex = GetOsmDataIndex(originalObjData)
		originalRelationIndex = originalObjIndex["relation"]

		for i in range(block.relations.size()):
			relation = block.relations[i]
			assert relation.objId in originalRelationIndex
			originalRel = originalRelationIndex[relation.objId]

			tagsChanged = relation.tags != originalRel.tags

			mchNodes, mchWays, mchRelations = get_relation_members(relation)
			ochNodes, ochWays, ochRelations = get_relation_members(originalRel)

			diffNodes = mchNodes.symmetric_difference(ochNodes)
			diffWays = mchWays.symmetric_difference(ochWays)
			diffRelations = mchRelations.symmetric_difference(ochRelations)
	
			updateWholeRelation = tagsChanged or len(diffRelations) > 0
			
			if not updateWholeRelation:
				#Update added or removed nodes and ways
				chNodes = diffNodes.copy()
				chWayObjs = pgmap.OsmData()
				t.GetObjectsById("way", list(diffWays), chWayObjs)
				for i in range(chWayObjs.ways.size()):
					way = chWayObjs.ways[i]
					for ref in way.refs:
						chNodes.add(ref)

				wayNodes2 = pgmap.OsmData()
				t.GetObjectsById("node", list(chNodes), wayNodes2)
				outerBbox = calc_bbox_of_nodes(wayNodes2.nodes, outerBbox)		

			else:
				#Update all nodes and ways
				allNodes = mchNodes.union(ochNodes)
				allWays = mchWays.union(ochWays)

				chWayObjs = pgmap.OsmData()
				t.GetObjectsById("way", list(allWays), chWayObjs)
				for i in range(chWayObjs.ways.size()):
					way = chWayObjs.ways[i]
					for ref in way.refs:
						allNodes.add(ref)

				wayNodes2 = pgmap.OsmData()
				t.GetObjectsById("node", list(allNodes), wayNodes2)
				outerBbox = calc_bbox_of_nodes(wayNodes2.nodes, outerBbox)		

	elif action in ["create", "delete"]:
		if action == "create":
			chNodes, chWays, chRelations = get_multi_relation_members(block.relations)
		else:
			chNodes, chWays, chRelations = get_multi_relation_members(originalObjData.relations)
		chWayObjs = pgmap.OsmData()
		t.GetObjectsById("way", list(chWays), chWayObjs)
		for i in range(chWayObjs.ways.size()):
			way = chWayObjs.ways[i]
			for ref in way.refs:
				chNodes.add(ref)

		wayNodes2 = pgmap.OsmData()
		t.GetObjectsById("node", list(chNodes), wayNodes2)
		outerBbox = calc_bbox_of_nodes(wayNodes2.nodes, outerBbox)		

	errStr = pgmap.PgMapError()
	if len(outerBbox) > 0:
		ok = t.ExpandChangesetBbox(int(changesetId),
			outerBbox,
			errStr)
		if not ok:
			print (errStr.errStr)

	#Track edit activity in database
	existingObjTypes, existingObjIdVers = get_object_type_id_vers(originalObjData)
	modifiedObjTypes, modifiedObjIdVers = get_object_type_id_vers(block)
	affectedParentsTypes, affectedParentsIdVers = get_object_type_id_vers(affectedParents)
	relatedObjsTypes, relatedObjsIdVers = get_object_type_id_vers(relatedObjs)

	bbox = pgmap.vectord()
	activity = pgmap.EditActivity()
	# originalObjData was captured before storing this block. Exclude nodes
	# whose requested deletion was skipped by if-unused validation.
	acceptedNodeIds = {node.objId for node in block.nodes}
	beforeNodes = [node for node in originalObjData.nodes if node.objId in acceptedNodeIds]
	activity.syncBefore = json.dumps([
		["node", node.objId, node.metaData.version] for node in beforeNodes])
	activity.bboxBefore = ("GEOMETRYCOLLECTION(" + ",".join(
		"POINT({:.17g} {:.17g})".format(node.lon, node.lat) for node in beforeNodes) + ")"
		if beforeNodes else "GEOMETRYCOLLECTION EMPTY")
	activity.existingType = pgmap.vectorstring(existingObjTypes)
	activity.existingIdVer = pgmap.vectorpairi64i64(existingObjIdVers)
	activity.updatedType = pgmap.vectorstring(modifiedObjTypes)
	activity.updatedIdVer = pgmap.vectorpairi64i64(modifiedObjIdVers)
	activity.affectedparentsType = pgmap.vectorstring(affectedParentsTypes)
	activity.affectedparentsIdVer = pgmap.vectorpairi64i64(affectedParentsIdVers)
	activity.relatedType = pgmap.vectorstring(relatedObjsTypes)
	activity.relatedIdVer = pgmap.vectorpairi64i64(relatedObjsIdVers)
	activity.changeset = int(changesetId)
	activity.timestamp = int(timestamp)
	activity.uid = uid
	activity.bbox = bbox
	activity.action = action
	activity.nodes = len(block.nodes)
	activity.ways = len(block.ways)
	activity.relations = len(block.relations)

	ok = t.InsertEditActivity(activity,
		errStr)
	if not ok:
		return HttpResponseServerError(errStr.errStr, content_type="text/plain")

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

	t = get_pgmap().GetTransaction("EXCLUSIVE")

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
		t = get_pgmap().GetTransaction("ACCESS SHARE")
	else:
		t = get_pgmap().GetTransaction("EXCLUSIVE")
	
	changesetData = pgmap.PgChangeset()
	errStr = pgmap.PgMapError()
	ret = t.GetChangeset(int(changesetId), changesetData, errStr)
	if ret == -1:
		common.abort_transaction(t)
		return HttpResponseNotFound("Changeset not found")
	if ret == 0:	
		common.abort_transaction(t)
		return HttpResponseServerError(errStr.errStr)

	if request.method == 'GET':
		t.GetChangesetChangeCounts(changesetData)
		t.Commit()

		return SerializeChangesets([changesetData], include_discussion, request, single=True)

	if request.method == 'PUT':
		
		if request.user.id != changesetData.uid:
			common.abort_transaction(t)
			return HttpResponse("This changeset belongs to a different user", status=409, content_type="text/plain")

		if not changesetData.is_open:
			err = "The changeset {} was closed at {}.".format(changesetData.objId, 
				datetime.datetime.fromtimestamp(changesetData.close_timestamp).isoformat())
			response = HttpResponse(err, content_type="text/plain")
			response.status_code = 409
			common.abort_transaction(t)
			return response

		unicodeTags = {}
		csIn = request.data.find("changeset")
		for tag in csIn.findall("tag"):
			unicodeTags[tag.attrib["k"]] = tag.attrib["v"]
		if not CheckTags(unicodeTags):
			common.abort_transaction(t)
			return HttpResponseBadRequest("Invalid tags")

		changesetData.tags = pgmap.mapstringstring()
		for tag in unicodeTags:
			changesetData.tags[tag] = unicodeTags[tag]

		ok = t.UpdateChangeset(changesetData, errStr)
		if not ok:
			t.Abort()
			return HttpResponseServerError(errStr.errStr)

		t.GetChangesetChangeCounts(changesetData)
		t.Commit()

		return SerializeChangesets([changesetData])

@csrf_exempt
@api_view(['PUT'])
@permission_classes((IsAuthenticated, ))
def close(request, changesetId):
	t = get_pgmap().GetTransaction("EXCLUSIVE")

	changesetData = pgmap.PgChangeset()
	errStr = pgmap.PgMapError()
	ret = t.GetChangeset(int(changesetId), changesetData, errStr)
	if ret == -1:
		common.abort_transaction(t)
		return HttpResponseNotFound("Changeset not found")
	if ret == 0:	
		common.abort_transaction(t)
		return HttpResponseServerError(errStr.errStr)

	if not changesetData.is_open:
		err = "The changeset {} was closed at {}.".format(changesetData.objId, 
			datetime.datetime.fromtimestamp(changesetData.close_timestamp).isoformat())
		response = HttpResponse(err, content_type="text/plain")
		response.status_code = 409
		common.abort_transaction(t)
		return response

	if request.user.id != changesetData.uid:
		common.abort_transaction(t)
		return HttpResponse("This changeset belongs to a different user", status=409, content_type="text/plain")

	t.CloseChangeset(int(changesetId), int(time.time()), errStr)
	t.GetChangesetChangeCounts(changesetData)
	t.Commit()

	return SerializeChangesets([changesetData])

@api_view(['GET'])
@permission_classes((IsAuthenticatedOrReadOnly, ))
def download(request, changesetId):
	t = get_pgmap().GetTransaction("ACCESS SHARE")
	
	osmChange = pgmap.OsmChange()
	errStr = pgmap.PgMapError()
	ret = t.GetChangesetOsmChange(int(changesetId), osmChange, errStr)
	if ret == -1:
		common.abort_transaction(t)
		return HttpResponseNotFound("Changeset not found")
	if ret == 0:	
		common.abort_transaction(t)
		return HttpResponseServerError(errStr.errStr)

	t.Commit()

	#print (changesetData.data.empty())
	sio = io.BytesIO()
	outBufWrapped = pgmap.CPyOutbuf(sio)
	pgmap.SaveToOsmChangeXml(osmChange, outBufWrapped, True)

	return HttpResponse(sio.getvalue(), content_type='text/xml')

@csrf_exempt
@api_view(['POST'])
@permission_classes((IsAuthenticated, ))
@parser_classes((DefusedXmlParser, ))
def expand_bbox(request, changesetId):

	return HttpResponse("Depricated December 2019", status=410, content_type="text/plain")

def get_changeset_query_limits():
	"""The default and the greatest number of changesets one query returns."""
	maximum = getattr(settings, 'CHANGESETS_MAXIMUM_QUERY_LIMIT', 100)
	return min(getattr(settings, 'CHANGESETS_DEFAULT_QUERY_LIMIT', 100), maximum), maximum

def ParseQueryTime(text):
	"""Seconds since 1970 for a time given to a changeset query.

	The time is an ISO 8601 date or date and time, taken as UTC unless it
	names an offset, or a whole number of seconds. Raises ValueError otherwise.
	"""
	text = text.strip()
	if re.match(r"^[0-9]{9,}$", text):
		return int(text)
	if text.endswith(("Z", "z")):
		text = text[:-1] + "+00:00"
	parsed = datetime.datetime.fromisoformat(text)
	if parsed.tzinfo is None:
		parsed = parsed.replace(tzinfo=datetime.timezone.utc)
	return int(parsed.timestamp())

@api_view(['GET'])
def list_changesets(request):
	query = pgmap.PgChangesetQuery()
	defaultLimit, maximumLimit = get_changeset_query_limits()

	try:
		query.user_uid = int(request.GET.get('user', 0))
	except ValueError:
		return HttpResponseBadRequest("Invalid user", content_type="text/plain")
	display_name = request.GET.get('display_name', None)
	if display_name is not None and 'user' in request.GET:
		return HttpResponseBadRequest("Provide either the user or the display_name, but not both",
			content_type="text/plain")
	query.is_open_only = request.GET.get('open', 'false') == 'true'
	query.is_closed_only = request.GET.get('closed', 'false') == 'true'

	#Check display_name actually exists
	if display_name is not None:
		try:
			user = User.objects.get(username=display_name)
			query.user_uid = user.id
		except ObjectDoesNotExist:
			return HttpResponseNotFound("User not found")

	bbox = request.GET.get('bbox', None) #min_lon,min_lat,max_lon,max_lat
	if bbox is not None:
		try:
			bbox = [float(v) for v in bbox.split(",")]
		except ValueError:
			bbox = []
		if len(bbox) != 4 or not all(math.isfinite(v) for v in bbox) or \
			bbox[0] > bbox[2] or bbox[1] > bbox[3]:
			return HttpResponseBadRequest("Invalid bbox", content_type="text/plain")
		query.bbox = pgmap.vectord(bbox)

	changesetsToGet = request.GET.get('changesets', None)
	if changesetsToGet is not None:
		try:
			ids = [int(v) for v in changesetsToGet.split(",")]
		except ValueError:
			ids = []
		if len(ids) == 0:
			return HttpResponseBadRequest("No changesets were given to search for", content_type="text/plain")
		query.ids = pgmap.vectori64(ids)

	order = request.GET.get('order', 'newest')
	if order not in ('newest', 'oldest'):
		return HttpResponseBadRequest("Invalid order", content_type="text/plain")
	query.oldestFirst = order == 'oldest'

	try:
		# time=T1 finds changesets closed after T1; time=T1,T2 those closed
		# after T1 and created before T2
		timearg = request.GET.get('time', None)
		if timearg is not None:
			if query.oldestFirst:
				return HttpResponseBadRequest("Cannot use order=oldest with time", content_type="text/plain")
			timeargSplit = timearg.split(",")
			if len(timeargSplit) > 2:
				raise ValueError(timearg)
			query.closedAfterTimestamp = ParseQueryTime(timeargSplit[0])
			if len(timeargSplit) == 2:
				query.openedBeforeTimestamp = ParseQueryTime(timeargSplit[1])

		# from=T1 finds changesets created at or after T1, and to=T2 limits
		# them to those created before T2. A to without a from has no effect.
		if 'from' in request.GET:
			query.openedFromTimestamp = ParseQueryTime(request.GET['from'])
			if 'to' in request.GET:
				openedBefore = ParseQueryTime(request.GET['to'])
				if query.openedBeforeTimestamp == -1 or openedBefore < query.openedBeforeTimestamp:
					query.openedBeforeTimestamp = openedBefore
	except (ValueError, OverflowError):
		return HttpResponseBadRequest("Invalid time", content_type="text/plain")

	try:
		limit = int(request.GET.get('limit', defaultLimit))
	except ValueError:
		limit = 0
	if limit < 1 or limit > maximumLimit:
		return HttpResponseBadRequest("Changeset limit must be between 1 and {}".format(maximumLimit),
			content_type="text/plain")
	query.limit = limit

	changesets = pgmap.vectorchangeset()
	errStr = pgmap.PgMapError()
	t = get_pgmap().GetTransaction("ACCESS SHARE")
	try:
		ok = t.GetChangesets(changesets, query, errStr)
		if not ok:
			common.abort_transaction(t)
			return HttpResponseServerError(errStr.errStr)
		t.GetChangesetChangeCounts(changesets)
		t.Commit()
	except Exception:
		common.abort_transaction(t)
		raise

	changesetLi = []
	for i in range(len(changesets)):
		changesetLi.append(changesets[i])
	return SerializeChangesets(changesetLi, request=request)

@csrf_exempt
@api_view(['POST'])
@permission_classes((IsAuthenticated, ))
@parser_classes((OsmChangeXmlParser, ))
def upload(request, changesetId):

	#Check changeset is open and for this user
	t = get_pgmap().GetTransaction("EXCLUSIVE")
	
	changesetData = pgmap.PgChangeset()
	errStr = pgmap.PgMapError()
	ret = t.GetChangeset(int(changesetId), changesetData, errStr)
	if ret == -1:
		common.abort_transaction(t)
		return HttpResponseNotFound("Changeset not found")
	if ret == 0:	
		common.abort_transaction(t)
		return HttpResponseServerError(errStr.errStr)

	if not changesetData.is_open:
		err = "The changeset {} was closed at {}.".format(changesetData.objId,
			datetime.datetime.fromtimestamp(changesetData.close_timestamp).isoformat())
		response = HttpResponse(err, content_type="text/plain")
		response.status_code = 409
		common.abort_transaction(t)
		return response

	if request.user.id != changesetData.uid:
		common.abort_transaction(t)
		return HttpResponse("This changeset belongs to a different user", status=409, content_type="text/plain")

	#Prepare diff result xml
	responseRoot = ET.Element('diffResult')
	doc = ET.ElementTree(responseRoot)
	responseRoot.attrib["version"] = str(settings.API_VERSION)
	responseRoot.attrib["generator"] = settings.GENERATOR

	createdNodeIds = pgmap.mapi64i64()
	createdWayIds = pgmap.mapi64i64()
	createdRelationIds = pgmap.mapi64i64()

	elementCount = 0
	for i in range(request.data.blocks.size()):
		changeBlock = request.data.blocks[i]
		action = changeBlock.action
		block = changeBlock.data
		ifunused = changeBlock.ifUnused
		timestamp = time.time()
		elementCount += block.nodes.size() + block.ways.size() + block.relations.size()
		if elementCount > settings.CHANGESETS_MAXIMUM_ELEMENTS:
			common.abort_transaction(t)
			return HttpResponseBadRequest("CHANGESETS_MAXIMUM_ELEMENTS limit exceeded; maximum is {}, got {}".format(
				settings.CHANGESETS_MAXIMUM_ELEMENTS, elementCount), content_type="text/plain")

		ret = upload_block(action, block, changesetId, t, responseRoot, 
			request.user.id, request.user.username, timestamp,
			createdNodeIds, createdWayIds, createdRelationIds, ifunused)
		if ret != True:
			print (ret)
			common.abort_transaction(t)
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
