# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from __future__ import print_function

from django.test import TestCase, override_settings
from django.test import Client
from django.urls import reverse
from django.contrib.auth.models import User

import xml.etree.ElementTree as ET
from defusedxml.ElementTree import parse, fromstring
import sys

import json
import pgmap
import gc
import sys
import time
import datetime
import random
from changeset import views
from pycrocosm.mapdb import get_pgmap
from xml.sax.saxutils import escape
from querymap.tests import create_node, create_way, create_relation, modify_node, modify_way, modify_relation
from changeset.management.commands import closeoldchangesets
from django.conf import settings

def ParseOsmDiffToDict(xml):
	out = {'node':{}, 'way':{}, 'relation':{}}
	for diff in xml:
		old_id, new_id, new_version = None, None, None
		if "old_id" in diff.attrib:
			old_id = int(diff.attrib["old_id"])
		if "new_id" in diff.attrib:
			new_id = int(diff.attrib["new_id"])
		if "new_version" in diff.attrib:
			new_version = int(diff.attrib["new_version"])

		out[diff.tag][old_id] = (new_id, new_version)
	return out

def GetObj(p, objType, objId):
	t = p.GetTransaction("ACCESS SHARE")
	osmData = pgmap.OsmData() #Watch out, this goes out of scope!
	t.GetObjectsById(objType, [objId], osmData)
	del t
	objs = None
	if objType == "node":
		objs = osmData.nodes
		if len(objs) == 0:
			return None
		return pgmap.OsmNode(objs[0])
	if objType == "way":
		objs = osmData.ways
		if len(objs) == 0:
			return None
		return pgmap.OsmWay(objs[0])
	if objType == "relation":
		objs = osmData.relations
		if len(objs) == 0:
			return None
		return pgmap.OsmRelation(objs[0])
	return None

def CreateTestChangeset(user, tags=None, is_open=True, bbox=None, open_timestamp=None, close_timestamp=None):
	if tags is None:
		tags = {'foo': 'bar'}
	t = get_pgmap().GetTransaction("EXCLUSIVE")
	cs = pgmap.PgChangeset()
	errStr = pgmap.PgMapError()
	for k in tags:
		cs.tags[k] = tags[k]
	cs.username = user.username
	cs.uid = user.id
	cs.is_open = is_open
	if open_timestamp is None:
		cs.open_timestamp = int(time.time())
	else:
		cs.open_timestamp = int(open_timestamp)
	if not is_open:
		if close_timestamp is None:
			cs.close_timestamp = int(time.time())
		else:
			cs.close_timestamp = int(close_timestamp)
	if bbox is not None:
		cs.bbox_set=True
		cs.x1=bbox[0]
		cs.y1=bbox[1]
		cs.x2=bbox[2]
		cs.y2=bbox[3]
	cid = t.CreateChangeset(cs, errStr);
	cs.objId = cid
	t.Commit()
	del t
	return cs

def CheckChangesetListContainsId(obj, xml, csId, expected):
	obj.assertEqual(xml.tag, "osm")
	found = False
	for cs in xml:
		obj.assertEqual(cs.tag, "changeset")
		if int(cs.attrib["id"]) == csId:
			found = True
			break
	obj.assertEqual(found, expected)

# Create your tests here.
# alter user microcosm with createdb;
# python manage.py test changeset --keep

class ChangesetTestCase(TestCase):
	def setUp(self):
		self.username = "john"
		self.password = "glass onion"
		self.email = 'jlennon@beatles.com'
		self.user = User.objects.create_user(self.username, self.email, self.password)
		self.client = Client()
		self.client.login(username=self.username, password=self.password)

		self.createXml = """<?xml version='1.0' encoding='UTF-8'?>
		  <osm>
		  <changeset>
			<tag k="created_by" v="JOSM 1.61"/>
			<tag k="comment" v="Just adding some streetnames"/>
		  </changeset>
		</osm>"""

		# Strings from https://www.cl.cam.ac.uk/~mgk25/ucs/examples/quickbrown.txt
		self.unicodeStr = u"Falsches Üben von Xylophonmusik quält jeden größeren Zwerg, Γαζέες καὶ μυρτιὲς δὲν θὰ βρῶ πιὰ στὸ χρυσαφὶ ξέφωτο, Kæmi ný öxi hér ykist þjófum nú bæði víl og ádrepa, イロハニホヘト チリヌルヲ ワカヨタレソ ツネナラム, В чащах юга жил бы цитрус? Да, но фальшивый экземпляр!"
		self.createXmlUnicodeTags = u"""<?xml version='1.0' encoding='UTF-8'?>
		  <osm>
		  <changeset>
			<tag k="source" v="photomapping"/>
			<tag k="comment" v="{}"/>
		  </changeset>
		</osm>""".format(escape(self.unicodeStr))

		self.overlongString = u"Lorem ipsum dolor sit amet, consectetur adipiscing elit. Etiam vulputate quam sit amet arcu efficitur, eget ullamcorper ligula suscipit. Nunc ullamcorper pellentesque libero at lacinia. Donec ut arcu mauris. Quisque ultrices tincidunt pharetra. Morbi indo."
		self.createXmlOverlong = u"""<?xml version='1.0' encoding='UTF-8'?>
		  <osm>
		  <changeset>
			<tag k="source" v="photomapping"/>
			<tag k="comment" v="{}"/>
		  </changeset>
		</osm>""".format(escape(self.overlongString))

		self.expandBboxXml = """<?xml version='1.0' encoding='UTF-8'?>
			<osm version='0.6' upload='true' generator='JOSM'>
			  <node id='-2190' action='modify' visible='true' lat='51.79852581343' lon='-3.38662147656' />
			  <node id='-2193' action='modify' visible='true' lat='50.71917284205' lon='-5.24880409375' />
			  <node id='-2197' action='modify' visible='true' lat='50.29646268337' lon='-4.07326698438' />
			  <node id='-2199' action='modify' visible='true' lat='50.70178040373' lon='-3.08999061719' />
			  <node id='-2201' action='modify' visible='true' lat='51.08292478386' lon='-3.28225135938' />
			  <way id='-2194' action='modify' visible='true'>
				<nd ref='-2190' />
				<nd ref='-2193' />
				<nd ref='-2197' />
				<nd ref='-2199' />
				<nd ref='-2201' />
			  </way>
			</osm>"""

	def get_test_changeset(self, cid):
		t = get_pgmap().GetTransaction("ACCESS SHARE")
		cs2 = pgmap.PgChangeset()
		errStr = pgmap.PgMapError()
		ret = t.GetChangeset(cid, cs2, errStr)
		t.Commit()
		if ret == 0:
			print (errStr)
		self.assertEqual(ret != 0, True)
		if ret == -1:
			raise KeyError("Changeset not found")
		return cs2

	def test_create_changeset(self):

		response = self.client.put(reverse('changeset:create'), self.createXml, content_type='text/xml')

		if response.status_code != 200:
			print (response.content)
		self.assertEqual(response.status_code, 200)
		cid = int(response.content)
		
		cs = self.get_test_changeset(cid)

		self.assertEqual("created_by" in cs.tags, True)
		self.assertEqual("comment" in cs.tags, True)
		self.assertEqual(cs.tags["created_by"] == "JOSM 1.61", True)
		self.assertEqual(cs.tags["comment"] == "Just adding some streetnames", True)

	def test_anon_create_changeset(self):
		anonClient = Client()
		response = anonClient.put(reverse('changeset:create'), self.createXml, content_type='text/xml')
		if response.status_code != 403:
			print (response.content)
		self.assertEqual(response.status_code, 403)

	def test_create_changeset_unicodetags(self):
		response = self.client.put(reverse('changeset:create'), self.createXmlUnicodeTags, content_type='text/xml')

		if response.status_code != 200:
			print (response.content)
		self.assertEqual(response.status_code, 200)
		cid = int(response.content)
		
		cs = self.get_test_changeset(cid)

		self.assertEqual("comment" in cs.tags, True)
		self.assertEqual(views.DecodeIfNotUnicode(cs.tags["comment"]) == self.unicodeStr, True)

	def test_create_changeset_overlong(self):
		response = self.client.put(reverse('changeset:create'), self.createXmlOverlong, content_type='text/xml')

		self.assertEqual(response.status_code, 400)

	def test_get_changeset(self):
		teststr = u"Съешь же ещё этих мягких французских булок да выпей чаю"
		cs = CreateTestChangeset(self.user, tags={"foo": "bar", 'test': teststr}, bbox=(-1.0893202,50.7942715,-1.0803509,50.7989372))

		anonClient = Client()

		response = anonClient.get(reverse('changeset:changeset', args=(cs.objId,)))
		self.assertEqual(response.status_code, 200)
	
		xml = fromstring(response.content)
		self.assertEqual(xml.tag, "osm")
		csout = xml.find("changeset")
		self.assertEqual(int(csout.attrib["id"]) == cs.objId, True)
		self.assertEqual("uid" in csout.attrib, True)
		self.assertEqual("created_at" in csout.attrib, True)
		self.assertEqual("min_lon" in csout.attrib, True)
		self.assertEqual("max_lon" in csout.attrib, True)
		self.assertEqual("min_lat" in csout.attrib, True)
		self.assertEqual("max_lat" in csout.attrib, True)

		self.assertEqual(csout.attrib["open"], "true")
		self.assertEqual(len(csout.findall("tag")), 2)
		
		foundFirst, foundSecond = False, False
		for tag in csout.findall("tag"):
			if tag.attrib["k"] == "foo":
				self.assertEqual(tag.attrib["v"], "bar")
				foundFirst = True
			if tag.attrib["k"] == "test":
				self.assertEqual(tag.attrib["v"], teststr)
				foundSecond = True
		self.assertEqual(foundFirst, True)
		self.assertEqual(foundSecond, True)
		self.assertEqual(csout.find("discussion"), None)
		
	def test_get_changeset_json(self):
		teststr = u"Съешь же ещё этих мягких французских булок да выпей чаю"
		opened = int(time.time()) - 600
		cs = CreateTestChangeset(self.user, tags={"foo": "bar", 'test': teststr},
			bbox=(-1.0893202,50.7942715,-1.0803509,50.7989372), open_timestamp=opened)

		response = Client().get("/api/0.6/changeset/{}.json".format(cs.objId))
		self.assertEqual(response.status_code, 200)
		self.assertEqual(response["Content-Type"], "application/json")
		doc = json.loads(response.content)
		self.assertEqual(doc["version"], "0.6")
		out = doc["changeset"]
		self.assertEqual(out["id"], cs.objId)
		self.assertEqual(out["uid"], self.user.id)
		self.assertEqual(out["user"], self.user.username)
		self.assertIs(out["open"], True)
		self.assertNotIn("closed_at", out)
		self.assertEqual(out["created_at"],
			datetime.datetime.fromtimestamp(opened, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
		self.assertEqual((out["min_lon"], out["min_lat"], out["max_lon"], out["max_lat"]),
			(-1.0893202,50.7942715,-1.0803509,50.7989372))
		self.assertEqual(out["tags"], {"foo": "bar", "test": teststr})
		self.assertNotIn("comments", out)

		withDiscussion = json.loads(Client().get(
			"/api/0.6/changeset/{}.json?include_discussion=true".format(cs.objId)).content)
		self.assertEqual(withDiscussion["changeset"]["comments"], [])

		# A closed changeset says when, and one without edits has no bbox
		closed = CreateTestChangeset(self.user, is_open=False, open_timestamp=opened, close_timestamp=opened+60)
		out = json.loads(Client().get("/api/0.6/changeset/{}.json".format(closed.objId)).content)["changeset"]
		self.assertIs(out["open"], False)
		self.assertIn("closed_at", out)
		self.assertNotIn("min_lon", out)

		self.assertEqual(Client().get("/api/0.6/changeset/999999999.json").status_code, 404)

	def test_get_changeset_list_json(self):
		cs = CreateTestChangeset(self.user, is_open=True, open_timestamp=int(time.time())-60)
		cs2 = CreateTestChangeset(self.user, is_open=False, open_timestamp=int(time.time())-120)

		response = Client().get("/api/0.6/changesets.json")
		self.assertEqual(response.status_code, 200)
		self.assertEqual(response["Content-Type"], "application/json")
		listed = {c["id"]: c for c in json.loads(response.content)["changesets"]}
		self.assertIn(cs.objId, listed)
		self.assertIn(cs2.objId, listed)
		self.assertIs(listed[cs.objId]["open"], True)
		self.assertIs(listed[cs2.objId]["open"], False)

		# Query parameters still apply with the suffix
		onlyOpen = {c["id"] for c in json.loads(Client().get("/api/0.6/changesets.json?open=true").content)["changesets"]}
		self.assertIn(cs.objId, onlyOpen)
		self.assertNotIn(cs2.objId, onlyOpen)

	def test_get_changeset_missing(self):
		anonClient = Client()
		response = anonClient.get(reverse('changeset:changeset', args=(0,)))
		self.assertEqual(response.status_code, 404)

	def test_put_changeset(self):
		cs = CreateTestChangeset(self.user, tags={"foo": "bar", "man": "child"})

		response = self.client.put(reverse('changeset:changeset', args=(cs.objId,)), self.createXml, content_type='text/xml')
		self.assertEqual(response.status_code, 200)

		xml = fromstring(response.content)
		self.assertEqual(xml.tag, "osm")
		csout = xml.find("changeset")

		self.assertEqual(len(csout.findall("tag")), 2)
		for tag in csout.findall("tag"):
			if tag.attrib["k"] == "comment":
				self.assertEqual(tag.attrib["v"], "Just adding some streetnames")
			if tag.attrib["k"] == "created_by":
				self.assertEqual(tag.attrib["v"], "JOSM 1.61")

	def test_put_changeset_anon(self):
		cs = CreateTestChangeset(self.user, tags={"foo": "bar", "man": "child"})

		anonClient = Client()
		response = anonClient.put(reverse('changeset:changeset', args=(cs.objId,)), self.createXml, content_type='text/xml')
		self.assertEqual(response.status_code, 403)

	def test_close_changeset(self):
		cs = CreateTestChangeset(self.user)

		response = self.client.put(reverse('changeset:close', args=(cs.objId,)))
		self.assertEqual(response.status_code, 200)

		t = get_pgmap().GetTransaction("ACCESS SHARE")
		cs2 = pgmap.PgChangeset()
		errStr = pgmap.PgMapError()
		ret = t.GetChangeset(cs.objId, cs2, errStr)
		t.Commit()
		self.assertEqual(ret != 0, True)
		self.assertEqual(cs2.is_open, False)

	def test_close_changeset_double_close(self):
		cs = CreateTestChangeset(self.user)

		response = self.client.put(reverse('changeset:close', args=(cs.objId,)))
		self.assertEqual(response.status_code, 200)

		t = get_pgmap().GetTransaction("ACCESS SHARE")
		cs2 = pgmap.PgChangeset()
		errStr = pgmap.PgMapError()
		ret = t.GetChangeset(cs.objId, cs2, errStr)
		t.Commit()

		self.assertEqual(cs2.is_open, False)

		response = self.client.put(reverse('changeset:close', args=(cs.objId,)))
		self.assertEqual(response.status_code, 409)

		self.assertEqual(response.content.decode("UTF-8"), "The changeset {} was closed at {}.".format(cs2.objId, 
			datetime.datetime.fromtimestamp(cs2.close_timestamp).isoformat()))

	def test_close_changeset_anon(self):
		cs = CreateTestChangeset(self.user)

		anonClient = Client()
		response = anonClient.put(reverse('changeset:close', args=(cs.objId,)))
		if response.status_code != 403:
			print (response.content)
		self.assertEqual(response.status_code, 403)

		cs2 = self.get_test_changeset(cs.objId)
		self.assertEqual(cs2.is_open, True)

	def tearDown(self):
		u = User.objects.get(username = self.username)
		u.delete()

		errStr = pgmap.PgMapError()
		t = get_pgmap().GetTransaction("EXCLUSIVE")
		ok = t.ResetActiveTables(errStr)
		if not ok:
			print (errStr.errStr)
		t.Commit()

class ChangesetUploadTestCase(TestCase):

	def setUp(self):
		self.username = "john"
		self.password = "glass onion"
		self.email = 'jlennon@beatles.com'
		self.user = User.objects.create_user(self.username, self.email, self.password)
		self.client = Client()
		self.client.login(username=self.username, password=self.password)

		self.username2 = "ringo"
		self.password2 = "penny lane"
		self.email2 = 'rstarr@beatles.com'
		self.user2 = User.objects.create_user(self.username2, self.email2, self.password2)
		self.client2 = Client()
		self.client2.login(username=self.username2, password=self.password2)

	def atomic_activity_for_changeset(self, changeset_id):
		t = get_pgmap().GetTransaction("ACCESS SHARE")
		activity = pgmap.vectorsharedptreditactivity()
		error = pgmap.PgMapError()
		t.QueryEditActivityByTimestamp(0, 0, activity, error)
		self.assertEqual(error.errStr, "")
		rows = [pgmap.EditActivity(row) for row in activity if row.changeset == changeset_id]
		t.Abort()
		return sorted(rows, key=lambda row: row.objId)

	def test_atomic_activity_groups_blocks_but_separates_uploads(self):
		cs = CreateTestChangeset(self.user)
		for count in (2, 1):
			blocks = "".join(
				'<create><node changeset="{}" id="{}" lat="50" lon="-1" /></create>'.format(cs.objId, -i-1)
				for i in range(count))
			response = self.client.post(reverse('changeset:upload', args=(cs.objId,)),
				'<osmChange version="0.6">' + blocks + '</osmChange>', content_type='text/xml')
			self.assertEqual(response.status_code, 200, response.content)
		rows = self.atomic_activity_for_changeset(cs.objId)
		self.assertEqual(len(rows), 3)
		self.assertGreater(rows[0].atomicEditId, 0)
		self.assertEqual(rows[0].atomicEditId, rows[1].atomicEditId)
		self.assertGreater(rows[2].atomicEditId, rows[1].atomicEditId)
		self.assertEqual([row.blockIndex for row in rows], [0, 1, 0])

	def test_atomic_activity_rolls_back_when_later_block_fails(self):
		cs = CreateTestChangeset(self.user)
		xml = ('<osmChange version="0.6">'
			'<create><node changeset="{0}" id="-1" lat="50" lon="-1" /></create>'
			'<create><way changeset="{0}" id="-2"><nd ref="999999999999998" /><nd ref="999999999999999" /></way></create>'
			'</osmChange>').format(cs.objId)
		response = self.client.post(reverse('changeset:upload', args=(cs.objId,)), xml, content_type='text/xml')
		self.assertEqual(response.status_code, 404, response.content)
		self.assertEqual(self.atomic_activity_for_changeset(cs.objId), [])

	def test_node_activity_records_before_version_and_position(self):
		cs = CreateTestChangeset(self.user)
		url = reverse('changeset:upload', args=(cs.objId,))
		create = ('<osmChange version="0.6"><create><node changeset="{}" id="-1" '
			'lat="50" lon="-1" /></create></osmChange>').format(cs.objId)
		response = self.client.post(url, create, content_type='text/xml')
		self.assertEqual(response.status_code, 200, response.content)
		node_id = int(fromstring(response.content)[0].attrib['new_id'])
		row = self.atomic_activity_for_changeset(cs.objId)[-1]
		self.assertEqual(json.loads(row.syncBefore), [])
		self.assertEqual(row.bboxBefore, 'GEOMETRYCOLLECTION EMPTY')
		for action, version, old_lon, old_lat in [('modify', 1, -1, 50), ('delete', 2, -2, 51)]:
			xml = ('<osmChange version="0.6"><{0}><node changeset="{1}" id="{2}" '
				'version="{3}" lat="51" lon="-2" /></{0}></osmChange>').format(action, cs.objId, node_id, version)
			response = self.client.post(url, xml, content_type='text/xml')
			self.assertEqual(response.status_code, 200, response.content)
			row = self.atomic_activity_for_changeset(cs.objId)[-1]
			self.assertEqual(json.loads(row.syncBefore), [['node', node_id, version]])
			self.assertEqual(row.bboxBefore, 'GEOMETRYCOLLECTION(POINT({} {}))'.format(old_lon, old_lat))

	def test_upload_create_single_node(self):

		cs = CreateTestChangeset(self.user, tags={"foo": "invade"}, is_open=True)

		xml = """<osmChange generator="JOSM" version="0.6">
		<create>
		  <node changeset="{}" id="-5393" lat="50.79046578105" lon="-1.04971367626" />
		</create>
		</osmChange>""".format(cs.objId)

		response = self.client.post(reverse('changeset:upload', args=(cs.objId,)), xml, 
			content_type='text/xml')
		if response.status_code != 200:
			print (response.content)
		self.assertEqual(response.status_code, 200)

		xml = fromstring(response.content)
		self.assertEqual(len(xml), 1)
		ndiff = xml[0]
		self.assertEqual(int(ndiff.attrib["old_id"]), -5393)
		self.assertEqual(int(ndiff.attrib["new_version"]), 1)
		self.assertEqual(int(ndiff.attrib["new_id"])>0, True)
		
		idOnServer = int(ndiff.attrib["new_id"])
		dbNode = GetObj(get_pgmap(), "node", idOnServer)

		self.assertEqual(dbNode is not None, True)
		self.assertEqual(dbNode.metaData.username, self.user.username)
		self.assertEqual(dbNode.metaData.uid, self.user.id)
		self.assertEqual(abs(dbNode.metaData.timestamp - time.time())<60, True)

		# Check xml download is reasonable
		response2 = self.client.get(reverse('changeset:download', args=(cs.objId,)))
		xml2 = fromstring(response2.content)
		for ch in xml2:
			self.assertEqual(ch.tag, "create")
			for ch2 in ch:
				self.assertEqual(ch2.tag, "node")

		#Check changeset bbox
		response2b = self.client.get(reverse('changeset:changeset', args=(cs.objId,)))
		xml2b = fromstring(response2b.content)
		self.assertEqual(abs(float(xml2b[0].attrib['min_lon'])+1.04971367626)<1e-6, True)
		self.assertEqual(abs(float(xml2b[0].attrib['max_lon'])+1.04971367626)<1e-6, True)
		self.assertEqual(abs(float(xml2b[0].attrib['min_lat'])-50.79046578105)<1e-6, True)
		self.assertEqual(abs(float(xml2b[0].attrib['max_lat'])-50.79046578105)<1e-6, True)

		#Check bbox of object
		response3 = self.client.get(reverse('elements:object_bbox', args=("node", idOnServer,)))
		xml3 = fromstring(response3.content)
		self.assertEqual(len(xml3), 1)
		el = xml3[0]
		self.assertEqual(abs(float(el.attrib['minlon'])+1.04971367626)<1e-6, True)
		self.assertEqual(abs(float(el.attrib['maxlon'])+1.04971367626)<1e-6, True)
		self.assertEqual(abs(float(el.attrib['minlat'])-50.79046578105)<1e-6, True)
		self.assertEqual(abs(float(el.attrib['maxlat'])-50.79046578105)<1e-6, True)

	def test_upload_modify_single_node(self):

		cs = CreateTestChangeset(self.user, tags={"foo": "interstellar"}, is_open=True)
		node = create_node(self.user.id, self.user.username)

		xml = """<osmChange generator="JOSM" version="0.6">
		<modify>
		  <node changeset="{}" id="{}" lat="50.80" lon="-1.05" version="{}">
			<tag k="note" v="Just a node"/>
		  </node>
		</modify>
		</osmChange>""".format(cs.objId, node.objId, node.metaData.version)

		response = self.client.post(reverse('changeset:upload', args=(cs.objId,)), xml, 
			content_type='text/xml')
		if response.status_code != 200:
			print (response.content)
		self.assertEqual(response.status_code, 200)

		xml = fromstring(response.content)
		self.assertEqual(len(xml), 1)
		ndiff = xml[0]
		self.assertEqual(int(ndiff.attrib["old_id"]), node.objId)
		self.assertEqual(int(ndiff.attrib["new_version"]), node.metaData.version+1)
		self.assertEqual(int(ndiff.attrib["new_id"]), node.objId)

		dbNode = GetObj(get_pgmap(), "node", node.objId)
		self.assertEqual(abs(dbNode.lat-50.80)<1e-6, True)
		self.assertEqual(abs(dbNode.lon+1.05)<1e-6, True)
		self.assertEqual(len(dbNode.tags), 1)

		# Check xml download is reasonable
		response2 = self.client.get(reverse('changeset:download', args=(cs.objId,)))
		xml2 = fromstring(response2.content)
		for ch in xml2:
			self.assertEqual(ch.tag, "modify")
			for ch2 in ch:
				self.assertEqual(ch2.tag, "node")

		#Check changeset bbox
		response2b = self.client.get(reverse('changeset:changeset', args=(cs.objId,)))
		xml2b = fromstring(response2b.content)
		csBboxLats, csBboxLons = [node.lat, 50.80], [node.lon, -1.05]
		self.assertEqual(abs(float(xml2b[0].attrib['min_lon'])-min(csBboxLons))<1e-6, True)
		self.assertEqual(abs(float(xml2b[0].attrib['max_lon'])-max(csBboxLons))<1e-6, True)
		self.assertEqual(abs(float(xml2b[0].attrib['min_lat'])-min(csBboxLats))<1e-6, True)
		self.assertEqual(abs(float(xml2b[0].attrib['max_lat'])-max(csBboxLats))<1e-6, True)

		#Check bbox of object
		response3 = self.client.get(reverse('elements:object_bbox', args=("node", dbNode.objId,)))
		xml3 = fromstring(response3.content)
		self.assertEqual(len(xml3), 1)
		el = xml3[0]
		self.assertEqual(abs(float(el.attrib['minlon'])+1.05)<1e-6, True)
		self.assertEqual(abs(float(el.attrib['maxlon'])+1.05)<1e-6, True)
		self.assertEqual(abs(float(el.attrib['minlat'])-50.80)<1e-6, True)
		self.assertEqual(abs(float(el.attrib['maxlat'])-50.80)<1e-6, True)


	def test_upload_modify_single_node_wrong_version(self):

		cs = CreateTestChangeset(self.user, tags={"foo": "interstellar"}, is_open=True)
		node = create_node(self.user.id, self.user.username)

		xml = """<osmChange generator="JOSM" version="0.6">
		<modify>
		  <node changeset="{}" id="{}" lat="50.80" lon="-1.05" version="{}">
			<tag k="note" v="Just a node"/>
		  </node>
		</modify>
		</osmChange>""".format(cs.objId, node.objId, node.metaData.version+1)

		response = self.client.post(reverse('changeset:upload', args=(cs.objId,)), xml, 
			content_type='text/xml')
		self.assertEqual(response.status_code, 409)

	def test_upload_modify_single_node_wrong_user(self):

		cs = CreateTestChangeset(self.user, tags={"foo": "apollo"}, is_open=True)
		node = create_node(self.user.id, self.user.username)

		xml = """<osmChange generator="JOSM" version="0.6">
		<modify>
		  <node changeset="{}" id="{}" lat="50.80" lon="-1.05" version="{}">
			<tag k="note" v="Just a node"/>
		  </node>
		</modify>
		</osmChange>""".format(cs.objId, node.objId, node.metaData.version)

		response = self.client2.post(reverse('changeset:upload', args=(cs.objId,)), xml, 
			content_type='text/xml')
		self.assertEqual(response.status_code, 409)

	def test_upload_delete_undelete_single_node(self):
	
		cs = CreateTestChangeset(self.user, tags={"foo": "interstellar"}, is_open=True)
		node = create_node(self.user.id, self.user.username)

		xml = """<osmChange generator="JOSM" version="0.6">
		<delete>
		  <node changeset="{}" id="{}" lat="50.80" lon="-1.05" version="{}"/>
		</delete>
		</osmChange>""".format(cs.objId, node.objId, node.metaData.version)

		response = self.client.post(reverse('changeset:upload', args=(cs.objId,)), xml, 
			content_type='text/xml')
		if response.status_code != 200:
			print (response.content)
		self.assertEqual(response.status_code, 200)

		xml = fromstring(response.content)
		self.assertEqual(len(xml), 1)
		ndiff = xml[0]
		self.assertEqual(int(ndiff.attrib["old_id"]), node.objId)

		dbNode = GetObj(get_pgmap(), "node", node.objId)
		self.assertEqual(dbNode is None, True)

		# Check xml download is reasonable
		response2 = self.client.get(reverse('changeset:download', args=(cs.objId,)))
		xml2 = fromstring(response2.content)
		for ch in xml2:
			self.assertEqual(ch.tag, "delete")
			for ch2 in ch:
				self.assertEqual(ch2.tag, "node")

		# Undelete node by uploading new version
		ok, node = modify_node(node, node.metaData.version+1, self.user)
		self.assertEqual(ok, True)

		dbNode = GetObj(get_pgmap(), "node", node.objId)
		self.assertEqual(dbNode is not None, True)

		#Check bbox of object
		response3 = self.client.get(reverse('elements:object_bbox', args=("node", node.objId,)))
		xml3 = fromstring(response3.content)
		self.assertEqual(len(xml3), 1)
		el = xml3[0]
		self.assertEqual(abs(float(el.attrib['minlon'])-node.lon)<1e-6, True)
		self.assertEqual(abs(float(el.attrib['maxlon'])-node.lon)<1e-6, True)
		self.assertEqual(abs(float(el.attrib['minlat'])-node.lat)<1e-6, True)
		self.assertEqual(abs(float(el.attrib['maxlat'])-node.lat)<1e-6, True)

	def test_upload_create_long_tag(self):

		cs = CreateTestChangeset(self.user, tags={"foo": "invade"}, is_open=True)

		xml = """<osmChange generator="JOSM" version="0.6">
		<create>
		  <node changeset="{}" id="-5393" lat="50.79046578105" lon="-1.04971367626">
		    <tag k="{}" v="{}"/>
		  </node>
		</create>
		</osmChange>""".format(cs.objId, "x" * settings.MAX_TAG_LENGTH, "y" * settings.MAX_TAG_LENGTH)

		response = self.client.post(reverse('changeset:upload', args=(cs.objId,)), xml, 
			content_type='text/xml')
		if response.status_code != 200:
			print (response.content)
		self.assertEqual(response.status_code, 200)

	def test_upload_create_overlong_tag(self):

		cs = CreateTestChangeset(self.user, tags={"foo": "invade"}, is_open=True)

		xml = """<osmChange generator="JOSM" version="0.6">
		<create>
		  <node changeset="{}" id="-5393" lat="50.79046578105" lon="-1.04971367626">
		    <tag k="{}" v="{}"/>
		  </node>
		</create>
		</osmChange>""".format(cs.objId, "x" * 256, "y" * 256)

		response = self.client.post(reverse('changeset:upload', args=(cs.objId,)), xml, 
			content_type='text/xml')
		self.assertEqual(response.status_code, 400)

	def test_upload_create_way(self):

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
		self.assertEqual(len(xml), 3)
		diffDict = ParseOsmDiffToDict(xml)
		self.assertEqual(-5393 in diffDict["node"], True)
		self.assertEqual(-5394 in diffDict["node"], True)
		self.assertEqual(-434 in diffDict["way"], True)
		
		newWayId, newWayVersion = diffDict["way"][-434]

		self.assertEqual(newWayVersion, 1)
		newWay = GetObj(get_pgmap(), "way", newWayId)
		self.assertEqual(newWay is not None, True)
		for ref in list(newWay.refs):
			self.assertEqual(ref > 0, True)

		#Check changeset bbox
		response2b = self.client.get(reverse('changeset:changeset', args=(cs.objId,)))
		xml2b = fromstring(response2b.content)
		self.assertEqual(abs(float(xml2b[0].attrib['min_lon'])+1.051)<1e-6, True)
		self.assertEqual(abs(float(xml2b[0].attrib['max_lon'])+1.04971367626)<1e-6, True)
		self.assertEqual(abs(float(xml2b[0].attrib['min_lat'])-50.79046578105)<1e-6, True)
		self.assertEqual(abs(float(xml2b[0].attrib['max_lat'])-50.81)<1e-6, True)

		#Check bbox of object
		response3 = self.client.get(reverse('elements:object_bbox', args=("way", newWayId,)))
		xml3 = fromstring(response3.content)
		self.assertEqual(len(xml3), 1)
		el = xml3[0]
		self.assertEqual(abs(float(el.attrib['minlon'])+1.051)<1e-6, True)
		self.assertEqual(abs(float(el.attrib['maxlon'])+1.04971367626)<1e-6, True)
		self.assertEqual(abs(float(el.attrib['minlat'])-50.79046578105)<1e-6, True)
		self.assertEqual(abs(float(el.attrib['maxlat'])-50.81)<1e-6, True)

	def generate_upload_way_with_n_nodes(self, csId, numNodes):
		nids = range(-5393, -5393-numNodes, -1)
		xml = ["""<osmChange generator="JOSM" version="0.6">
		<create>"""]
		for i in nids:
			xml.append("""  <node changeset="{0}" id="{1}" lat="{2}" lon="{3}" />\n"""
				.format(csId, i, 50.79046578105+random.uniform(-1,1), -1.04971367626+random.uniform(-1,1)))
		xml.append("""  <way changeset="{0}" id="-434">
		   <tag k="note" v="Just a way"/>""".format(csId))
		for i in nids:
			xml.append("""   <nd ref="{0}"/>\n""".format(i))
		xml.append("""</way>
		</create>
		</osmChange>""")
		return "".join(xml)

	@override_settings(WAYNODES_MAXIMUM=100, CHANGESETS_MAXIMUM_ELEMENTS=200, XML_UPLOAD_MAXIMUM_BYTES=1000000)
	def test_upload_create_way_with_max_nodes(self):

		cs = CreateTestChangeset(self.user, tags={"foo": "invade"}, is_open=True)
		xml = self.generate_upload_way_with_n_nodes(cs.objId, settings.WAYNODES_MAXIMUM)

		response = self.client.post(reverse('changeset:upload', args=(cs.objId,)), xml, 
			content_type='text/xml')
		self.assertEqual(response.status_code, 200, response.content)

	@override_settings(WAYNODES_MAXIMUM=100, CHANGESETS_MAXIMUM_ELEMENTS=200, XML_UPLOAD_MAXIMUM_BYTES=1000000)
	def test_upload_create_way_too_many_nodes(self):

		cs = CreateTestChangeset(self.user, tags={"foo": "invade"}, is_open=True)
		xml = self.generate_upload_way_with_n_nodes(cs.objId, settings.WAYNODES_MAXIMUM+1)

		response = self.client.post(reverse('changeset:upload', args=(cs.objId,)), xml, 
			content_type='text/xml')
		self.assertEqual(response.status_code, 400)

	def test_upload_to_closed_changeset(self):
		closedAt = int(time.time()) - 30
		cs = CreateTestChangeset(self.user, tags={"foo": "invade"}, is_open=False, close_timestamp=closedAt)
		xml = self.generate_upload_way_with_n_nodes(cs.objId, 2)

		response = self.client.post(reverse('changeset:upload', args=(cs.objId,)), xml,
			content_type='text/xml')
		self.assertEqual(response.status_code, 409, response.content)
		self.assertEqual(response.content.decode("UTF-8"), "The changeset {} was closed at {}.".format(
			cs.objId, datetime.datetime.fromtimestamp(closedAt).isoformat()))

	def test_upload_create_way_empty(self):

		cs = CreateTestChangeset(self.user, tags={"foo": "invade"}, is_open=True)
		xml = self.generate_upload_way_with_n_nodes(cs.objId, 0)

		response = self.client.post(reverse('changeset:upload', args=(cs.objId,)), xml, 
			content_type='text/xml')
		self.assertEqual(response.status_code, 400)

	def test_upload_create_way_too_few_nodes(self):

		cs = CreateTestChangeset(self.user, tags={"foo": "invade"}, is_open=True)
		xml = self.generate_upload_way_with_n_nodes(cs.objId, 1)

		response = self.client.post(reverse('changeset:upload', args=(cs.objId,)), xml, 
			content_type='text/xml')
		self.assertEqual(response.status_code, 400)

	def test_upload_create_complex(self):

		cs = CreateTestChangeset(self.user, tags={"foo": "me"}, is_open=True)

		node = create_node(self.user.id, self.user.username)

		lats = [50.78673385857, 50.7865119298, 50.78724872927]
		lons = [-1.04730886255, -1.04843217891, -1.04808114255]

		xml = """<osmChange version="0.6" generator="JOSM">
		<create>
		  <node id='-3912' changeset='{0}' lat='{2}' lon='{5}'>
			<tag k='abc' v='def' />
		  </node>
		  <node id='-3910' changeset='{0}' lat='{3}' lon='{6}' />
		  <node id='-3909' changeset='{0}' lat='{4}' lon='{7}' />
		  <way id='-3911' changeset='{0}'>
			<nd ref='-3909' />
			<nd ref='-3910' />
			<nd ref='-3912' />
			<nd ref='{1}' />
			<tag k='ghi' v='jkl' />
		  </way>
		  <relation id='-3933' changeset='{0}'>
			<member type='way' ref='-3911' role='lmn' />
			<member type='node' ref='-3909' role='opq' />
			<tag k='rst' v='uvw' />
		  </relation>
		  <relation id='-3934' changeset='{0}'>
			<member type='way' ref='-3911' role='lmn' />
			<member type='relation' ref='-3933' role='opq' />
			<tag k='rst' v='xyz' />
		  </relation>
		</create>
		</osmChange>""".format(cs.objId, node.objId, *lats, *lons)

		response = self.client.post(reverse('changeset:upload', args=(cs.objId,)), xml, 
			content_type='text/xml')
		if response.status_code != 200:
			print (response.content)
		self.assertEqual(response.status_code, 200)

		xml = fromstring(response.content)
		diffDict = ParseOsmDiffToDict(xml)
		
		way = GetObj(get_pgmap(), "way", diffDict["way"][-3911][0])
		wayRefs = list(way.refs)
		for diffId, diffVer in diffDict["node"].values():
			self.assertEqual(diffId in wayRefs, True)
		self.assertEqual(node.objId in wayRefs, True)

		wayTags = dict(way.tags)
		self.assertEqual(wayTags, {'ghi': 'jkl'})

		rel1 = GetObj(get_pgmap(), "relation", diffDict["relation"][-3933][0])
		rel1Refs = [(member.TypeName(), member.ref, member.role) for member in rel1.members]
		self.assertEqual(("way", diffDict["way"][-3911][0], "lmn") in rel1Refs, True)
		self.assertEqual(("node", diffDict["node"][-3909][0], "opq") in rel1Refs, True)

		rel1Tags = dict(rel1.tags)
		self.assertEqual(rel1Tags, {'rst': 'uvw'})

		rel2 = GetObj(get_pgmap(), "relation", diffDict["relation"][-3934][0])
		rel2Refs = [(member.TypeName(), member.ref, member.role) for member in rel2.members]
		self.assertEqual(("way", diffDict["way"][-3911][0], "lmn") in rel2Refs, True)
		self.assertEqual(("relation", diffDict["relation"][-3933][0], "opq") in rel2Refs, True)

		rel2Tags = dict(rel2.tags)
		self.assertEqual(rel2Tags, {'rst': 'xyz'})

		#Check bbox of object
		lats.append(node.lat)
		lons.append(node.lon)
		minlat = min(lats)
		maxlat = max(lats)
		minlon = min(lons)
		maxlon = max(lons)
		response3 = self.client.get(reverse('elements:object_bbox', args=("way", way.objId,)))
		xml3 = fromstring(response3.content)
		self.assertEqual(len(xml3), 1)
		el = xml3[0]
		self.assertEqual(abs(float(el.attrib['minlon'])-minlon)<1e-6, True)
		self.assertEqual(abs(float(el.attrib['maxlon'])-maxlon)<1e-6, True)
		self.assertEqual(abs(float(el.attrib['minlat'])-minlat)<1e-6, True)
		self.assertEqual(abs(float(el.attrib['maxlat'])-maxlat)<1e-6, True)

		response4 = self.client.get(reverse('elements:object_bbox', args=("relation", rel1.objId,)))
		xml4 = fromstring(response3.content)
		self.assertEqual(len(xml4), 1)
		el = xml4[0]
		self.assertEqual(abs(float(el.attrib['minlon'])-minlon)<1e-6, True)
		self.assertEqual(abs(float(el.attrib['maxlon'])-maxlon)<1e-6, True)
		self.assertEqual(abs(float(el.attrib['minlat'])-minlat)<1e-6, True)
		self.assertEqual(abs(float(el.attrib['maxlat'])-maxlat)<1e-6, True)

		response5 = self.client.get(reverse('elements:object_bbox', args=("relation", rel2.objId,)))
		xml5 = fromstring(response3.content)
		self.assertEqual(len(xml5), 1)
		el = xml5[0]
		self.assertEqual(abs(float(el.attrib['minlon'])-minlon)<1e-6, True)
		self.assertEqual(abs(float(el.attrib['maxlon'])-maxlon)<1e-6, True)
		self.assertEqual(abs(float(el.attrib['minlat'])-minlat)<1e-6, True)
		self.assertEqual(abs(float(el.attrib['maxlat'])-maxlat)<1e-6, True)

	def test_change_relation_by_moving_node(self):

		cs = CreateTestChangeset(self.user, tags={"foo": "me"}, is_open=True)

		node = create_node(self.user.id, self.user.username)
		node2 = create_node(self.user.id, self.user.username, node)
		relation = create_relation(self.user.id, self.user.username, [("node", node.objId, "parrot"), ("node", node2.objId, "dead")])

		#Check bbox of object
		lats = [node.lat, node2.lat]
		lons = [node.lon, node2.lon]
		minlat = min(lats)
		maxlat = max(lats)
		minlon = min(lons)
		maxlon = max(lons)
		response = self.client.get(reverse('elements:object_bbox', args=("relation", relation.objId,)))
		xml = fromstring(response.content)
		self.assertEqual(len(xml), 1)
		el = xml[0]
		self.assertEqual(abs(float(el.attrib['minlon'])-minlon)<1e-6, True)
		self.assertEqual(abs(float(el.attrib['maxlon'])-maxlon)<1e-6, True)
		self.assertEqual(abs(float(el.attrib['minlat'])-minlat)<1e-6, True)
		self.assertEqual(abs(float(el.attrib['maxlat'])-maxlat)<1e-6, True)		

		xml = """<osmChange generator="JOSM" version="0.6">
		<modify>
		  <node changeset="{}" id="{}" lat="50.80" lon="-1.05" version="{}">
			<tag k="note" v="Just a node"/>
		  </node>
		</modify>
		</osmChange>""".format(cs.objId, node.objId, node.metaData.version)

		response2 = self.client.post(reverse('changeset:upload', args=(cs.objId,)), xml, 
			content_type='text/xml')
		if response2.status_code != 200:
			print (response2.content)
		self.assertEqual(response2.status_code, 200)

		#Check bbox of object
		lats = [50.80, node2.lat]
		lons = [-1.05, node2.lon]
		minlat = min(lats)
		maxlat = max(lats)
		minlon = min(lons)
		maxlon = max(lons)
		response3 = self.client.get(reverse('elements:object_bbox', args=("relation", relation.objId,)))
		xml3 = fromstring(response3.content)
		self.assertEqual(len(xml3), 1)
		el = xml3[0]
		self.assertEqual(abs(float(el.attrib['minlon'])-minlon)<1e-6, True)
		self.assertEqual(abs(float(el.attrib['maxlon'])-maxlon)<1e-6, True)
		self.assertEqual(abs(float(el.attrib['minlat'])-minlat)<1e-6, True)
		self.assertEqual(abs(float(el.attrib['maxlat'])-maxlat)<1e-6, True)

	def test_change_relation_by_changing_way(self):

		cs = CreateTestChangeset(self.user, tags={"foo": "me"}, is_open=True)

		node = create_node(self.user.id, self.user.username)
		node2 = create_node(self.user.id, self.user.username, node)
		node3 = create_node(self.user.id, self.user.username, node)
		way = create_way(self.user.id, self.user.username, [node.objId, node2.objId, node3.objId])
		relation = create_relation(self.user.id, self.user.username, [("way", way.objId, "parrot")])

		#Check bbox of object
		lats = [node.lat, node2.lat, node3.lat]
		lons = [node.lon, node2.lon, node3.lon]
		minlat = min(lats)
		maxlat = max(lats)
		minlon = min(lons)
		maxlon = max(lons)
		response = self.client.get(reverse('elements:object_bbox', args=("relation", relation.objId,)))
		xml = fromstring(response.content)
		self.assertEqual(len(xml), 1)
		el = xml[0]
		self.assertEqual(abs(float(el.attrib['minlon'])-minlon)<1e-6, True)
		self.assertEqual(abs(float(el.attrib['maxlon'])-maxlon)<1e-6, True)
		self.assertEqual(abs(float(el.attrib['minlat'])-minlat)<1e-6, True)
		self.assertEqual(abs(float(el.attrib['maxlat'])-maxlat)<1e-6, True)		

		xml = """<osmChange generator="JOSM" version="0.6">
		<modify>
		  <way id='{1}' changeset='{0}' version="{2}">
			<nd ref='{3}' />
			<nd ref='{4}' />
			<tag k='ghi' v='jkl' />
		  </way>
		</modify>
		</osmChange>""".format(cs.objId, way.objId, way.metaData.version, node.objId, node2.objId)

		response2 = self.client.post(reverse('changeset:upload', args=(cs.objId,)), xml, 
			content_type='text/xml')
		if response2.status_code != 200:
			print (response2.content)
		self.assertEqual(response2.status_code, 200)

		#Check bbox of object
		lats = [node.lat, node2.lat]
		lons = [node.lon, node2.lon]
		minlat = min(lats)
		maxlat = max(lats)
		minlon = min(lons)
		maxlon = max(lons)
		response3 = self.client.get(reverse('elements:object_bbox', args=("relation", relation.objId,)))
		xml3 = fromstring(response3.content)
		self.assertEqual(len(xml3), 1)
		el = xml3[0]
		self.assertEqual(abs(float(el.attrib['minlon'])-minlon)<1e-6, True)
		self.assertEqual(abs(float(el.attrib['maxlon'])-maxlon)<1e-6, True)
		self.assertEqual(abs(float(el.attrib['minlat'])-minlat)<1e-6, True)
		self.assertEqual(abs(float(el.attrib['maxlat'])-maxlat)<1e-6, True)		

	def test_upload_delete_node_used_by_way(self):

		cs = CreateTestChangeset(self.user, tags={"foo": "me"}, is_open=True)

		node = create_node(self.user.id, self.user.username)
		node2 = create_node(self.user.id, self.user.username, node)
		way = create_way(self.user.id, self.user.username, [node.objId, node2.objId])

		xml = """<osmChange generator="JOSM" version="0.6">
		<delete>
		  <node changeset="{}" id="{}" lat="50.80" lon="-1.05" version="{}"/>
		</delete>
		</osmChange>""".format(cs.objId, node.objId, node.metaData.version)

		response = self.client.post(reverse('changeset:upload', args=(cs.objId,)), xml, 
			content_type='text/xml')
		self.assertEqual(response.status_code, 412)

	def test_upload_delete_node_used_by_relation(self):

		cs = CreateTestChangeset(self.user, tags={"foo": "me"}, is_open=True)

		node = create_node(self.user.id, self.user.username)
		node2 = create_node(self.user.id, self.user.username, node)
		relation = create_relation(self.user.id, self.user.username, [("node", node.objId, "parrot"), ("node", node2.objId, "dead")])

		xml = """<osmChange generator="JOSM" version="0.6">
		<delete>
		  <node changeset="{}" id="{}" lat="50.80" lon="-1.05" version="{}"/>
		</delete>
		</osmChange>""".format(cs.objId, node.objId, node.metaData.version)

		response = self.client.post(reverse('changeset:upload', args=(cs.objId,)), xml, 
			content_type='text/xml')
		#print (response.content)
		self.assertEqual(response.status_code, 412)

	def test_upload_delete_node_used_by_way(self):

		cs = CreateTestChangeset(self.user, tags={"foo": "me"}, is_open=True)

		node = create_node(self.user.id, self.user.username)
		node2 = create_node(self.user.id, self.user.username, node)
		way = create_way(self.user.id, self.user.username, [node.objId, node2.objId])

		xml = """<osmChange generator="JOSM" version="0.6">
		<delete>
		  <node changeset="{}" id="{}" version="{}"/>
		</delete>
		</osmChange>""".format(cs.objId, node.objId, node.metaData.version)

		response = self.client.post(reverse('changeset:upload', args=(cs.objId,)), xml, 
			content_type='text/xml')
		#print (response.content)
		self.assertEqual(response.status_code, 412)

	def test_upload_delete_node_used_by_relation(self):

		cs = CreateTestChangeset(self.user, tags={"foo": "me"}, is_open=True)

		node = create_node(self.user.id, self.user.username)
		node2 = create_node(self.user.id, self.user.username, node)
		relation = create_relation(self.user.id, self.user.username, [("node", node.objId, "parrot"), ("node", node2.objId, "dead")])

		xml = """<osmChange generator="JOSM" version="0.6">
		<delete>
		  <node changeset="{}" id="{}" version="{}"/>
		</delete>
		</osmChange>""".format(cs.objId, node.objId, node.metaData.version)

		response = self.client.post(reverse('changeset:upload', args=(cs.objId,)), xml, 
			content_type='text/xml')
		#print (response.content)
		self.assertEqual(response.status_code, 412)

	def test_upload_delete_undelete_way(self):
		cs = CreateTestChangeset(self.user, tags={"foo": "me"}, is_open=True)

		node = create_node(self.user.id, self.user.username)
		node2 = create_node(self.user.id, self.user.username, node)
		way = create_way(self.user.id, self.user.username, [node.objId, node2.objId])

		xml = """<osmChange generator="JOSM" version="0.6">
		<delete>
		  <way changeset="{}" id="{}" version="{}"/>
		</delete>
		</osmChange>""".format(cs.objId, way.objId, node.metaData.version)

		response = self.client.post(reverse('changeset:upload', args=(cs.objId,)), xml, 
			content_type='text/xml')
		#print (response.content)
		self.assertEqual(response.status_code, 200)

		ok, way = modify_way(way, [node.objId, node2.objId], {}, self.user)
		self.assertEqual(ok, True)

	def test_upload_delete_way_used_by_relation(self):
		cs = CreateTestChangeset(self.user, tags={"foo": "me"}, is_open=True)

		node = create_node(self.user.id, self.user.username)
		node2 = create_node(self.user.id, self.user.username, node)
		way = create_way(self.user.id, self.user.username, [node.objId, node2.objId])
		relation = create_relation(self.user.id, self.user.username, [("node", node.objId, "parrot"), ("way", way.objId, "dead")])

		xml = """<osmChange generator="JOSM" version="0.6">
		<delete>
		  <way changeset="{}" id="{}" version="{}"/>
		</delete>
		</osmChange>""".format(cs.objId, way.objId, node.metaData.version)

		response = self.client.post(reverse('changeset:upload', args=(cs.objId,)), xml, 
			content_type='text/xml')
		#print (response.content)
		self.assertEqual(response.status_code, 412)

	def test_upload_delete_undelete_relation(self):
		cs = CreateTestChangeset(self.user, tags={"foo": "me"}, is_open=True)

		node = create_node(self.user.id, self.user.username)
		node2 = create_node(self.user.id, self.user.username, node)
		refs = [("node", node.objId, "parrot"), ("node", node2.objId, "dead")]
		relation = create_relation(self.user.id, self.user.username, refs)

		xml = """<osmChange generator="JOSM" version="0.6">
		<delete>
		  <relation changeset="{}" id="{}" version="{}"/>
		</delete>
		</osmChange>""".format(cs.objId, relation.objId, relation.metaData.version)

		response = self.client.post(reverse('changeset:upload', args=(cs.objId,)), xml, 
			content_type='text/xml')
		self.assertEqual(response.status_code, 200)

		relation = modify_relation(self.user.id, self.user.username, relation, refs, {})
		self.assertNotEqual(relation, None)

	def test_upload_delete_relation_used_by_relation(self):
		cs = CreateTestChangeset(self.user, tags={"foo": "me"}, is_open=True)

		node = create_node(self.user.id, self.user.username)
		node2 = create_node(self.user.id, self.user.username, node)
		relation = create_relation(self.user.id, self.user.username, [("node", node.objId, "parrot"), ("node", node2.objId, "dead")])
		relation2 = create_relation(self.user.id, self.user.username, [("node", node.objId, "parrot"), ("relation", relation.objId, "dead")])

		xml = """<osmChange generator="JOSM" version="0.6">
		<delete>
		  <relation changeset="{}" id="{}" version="{}"/>
		</delete>
		</osmChange>""".format(cs.objId, relation.objId, relation.metaData.version)

		response = self.client.post(reverse('changeset:upload', args=(cs.objId,)), xml, 
			content_type='text/xml')
		#print (response.content)
		self.assertEqual(response.status_code, 412)

	def test_upload_multi_action(self):

		cs = CreateTestChangeset(self.user, tags={"foo": "me"}, is_open=True)

		node = create_node(self.user.id, self.user.username)
		node2 = create_node(self.user.id, self.user.username, node)
		way = create_way(self.user.id, self.user.username, [node.objId, node2.objId])

		xml = """<osmChange version="0.6" generator="JOSM">
		<create>
		  <node id='-3912' changeset='{0}' lat='50.78673385857' lon='-1.04730886255'>
			<tag k='abc' v='def' />
		  </node>
		</create>
		<modify>
		  <way id='{1}' changeset='{0}' version="{2}">
			<nd ref='-3912' />
			<nd ref='{3}' />
			<nd ref='{4}' />
			<tag k='ghi' v='jkl' />
		  </way>
		</modify>
		</osmChange>""".format(cs.objId, way.objId, way.metaData.version, node.objId, node2.objId)

		response = self.client.post(reverse('changeset:upload', args=(cs.objId,)), xml, 
			content_type='text/xml')
		if response.status_code != 200:
			print (response.content)
		self.assertEqual(response.status_code, 200)

		xml = fromstring(response.content)
		diffDict = ParseOsmDiffToDict(xml)

		#Check changeset bbox
		lats = [50.78673385857, node.lat, node2.lat]
		lons = [-1.04730886255, node.lon, node2.lon]
		minlat = min(lats)
		maxlat = max(lats)
		minlon = min(lons)
		maxlon = max(lons)
		response2b = self.client.get(reverse('changeset:changeset', args=(cs.objId,)))
		xml2b = fromstring(response2b.content)
		self.assertEqual(abs(float(xml2b[0].attrib['min_lon'])-minlon)<1e-6, True)
		self.assertEqual(abs(float(xml2b[0].attrib['max_lon'])-maxlon)<1e-6, True)
		self.assertEqual(abs(float(xml2b[0].attrib['min_lat'])-minlat)<1e-6, True)
		self.assertEqual(abs(float(xml2b[0].attrib['max_lat'])-maxlat)<1e-6, True)

		#Check bbox of object
		response3 = self.client.get(reverse('elements:object_bbox', args=("way", way.objId,)))
		xml3 = fromstring(response3.content)
		self.assertEqual(len(xml3), 1)
		el = xml3[0]
		self.assertEqual(abs(float(el.attrib['minlon'])-minlon)<1e-6, True)
		self.assertEqual(abs(float(el.attrib['maxlon'])-maxlon)<1e-6, True)
		self.assertEqual(abs(float(el.attrib['minlat'])-minlat)<1e-6, True)
		self.assertEqual(abs(float(el.attrib['maxlat'])-maxlat)<1e-6, True)

	def test_upload_delete_node_used_by_way_if_unused(self):

		cs = CreateTestChangeset(self.user, tags={"foo": "me"}, is_open=True)

		node = create_node(self.user.id, self.user.username)
		node2 = create_node(self.user.id, self.user.username, node)
		way = create_way(self.user.id, self.user.username, [node.objId, node2.objId])

		xml = """<osmChange generator="JOSM" version="0.6">
		<delete if-unused="true">
		  <node changeset="{}" id="{}" version="{}"/>
		</delete>
		</osmChange>""".format(cs.objId, node.objId, node.metaData.version)

		response = self.client.post(reverse('changeset:upload', args=(cs.objId,)), xml, 
			content_type='text/xml')
		#print (response.content)
		self.assertEqual(response.status_code, 200)

	def test_upload_delete_node_used_by_relation_if_unused(self):

		cs = CreateTestChangeset(self.user, tags={"foo": "me"}, is_open=True)

		node = create_node(self.user.id, self.user.username)
		node2 = create_node(self.user.id, self.user.username, node)
		relation = create_relation(self.user.id, self.user.username, [("node", node.objId, "parrot"), ("node", node2.objId, "dead")])

		xml = """<osmChange generator="JOSM" version="0.6">
		<delete if-unused="true">
		  <node changeset="{}" id="{}" version="{}"/>
		</delete>
		</osmChange>""".format(cs.objId, node.objId, node.metaData.version)

		response = self.client.post(reverse('changeset:upload', args=(cs.objId,)), xml, 
			content_type='text/xml')
		#print (response.content)
		self.assertEqual(response.status_code, 200)

	def test_upload_delete_way_used_by_relation_if_unused(self):
		cs = CreateTestChangeset(self.user, tags={"foo": "me"}, is_open=True)

		node = create_node(self.user.id, self.user.username)
		node2 = create_node(self.user.id, self.user.username, node)
		way = create_way(self.user.id, self.user.username, [node.objId, node2.objId])
		relation = create_relation(self.user.id, self.user.username, [("node", node.objId, "parrot"), ("way", way.objId, "dead")])

		xml = """<osmChange generator="JOSM" version="0.6">
		<delete if-unused="true">
		  <way changeset="{}" id="{}" version="{}"/>
		</delete>
		</osmChange>""".format(cs.objId, way.objId, way.metaData.version)

		response = self.client.post(reverse('changeset:upload', args=(cs.objId,)), xml, 
			content_type='text/xml')
		#print (response.content)
		self.assertEqual(response.status_code, 200)

	def test_upload_delete_relation_used_by_relation_if_unused(self):
		cs = CreateTestChangeset(self.user, tags={"foo": "me"}, is_open=True)

		node = create_node(self.user.id, self.user.username)
		node2 = create_node(self.user.id, self.user.username, node)
		relation = create_relation(self.user.id, self.user.username, [("node", node.objId, "parrot"), ("node", node2.objId, "dead")])
		relation2 = create_relation(self.user.id, self.user.username, [("node", node.objId, "parrot"), ("relation", relation.objId, "dead")])

		xml = """<osmChange generator="JOSM" version="0.6">
		<delete if-unused="true">
		  <relation changeset="{}" id="{}" version="{}"/>
		</delete>
		</osmChange>""".format(cs.objId, relation.objId, relation.metaData.version)

		response = self.client.post(reverse('changeset:upload', args=(cs.objId,)), xml, 
			content_type='text/xml')
		#print (response.content)
		self.assertEqual(response.status_code, 200)

	def test_upload_delete_interdependent_objects(self):

		cs = CreateTestChangeset(self.user, tags={"foo": "me"}, is_open=True)

		node = create_node(self.user.id, self.user.username)
		node2 = create_node(self.user.id, self.user.username, node)
		way = create_way(self.user.id, self.user.username, [node.objId, node2.objId])

		xml = """<osmChange version="0.6" generator="JOSM">
		<delete>
		  <way id='{}' version='{}' changeset='{}'/>
		  <node id='{}' version='{}' changeset='{}'/>
		  <node id='{}' version='{}' changeset='{}'/>
		</delete>
		</osmChange>""".format(way.objId, way.metaData.version, cs.objId,
			node.objId, node.metaData.version, cs.objId,
			node2.objId, node2.metaData.version, cs.objId)

		response = self.client.post(reverse('changeset:upload', args=(cs.objId,)), xml, 
			content_type='text/xml')
		#print (response.content)
		self.assertEqual(response.status_code, 200)

	def test_upload_delete_relations_with_circular_reference(self):

		cs = CreateTestChangeset(self.user, tags={"foo": "me"}, is_open=True)

		node = create_node(self.user.id, self.user.username)
		relation = create_relation(self.user.id, self.user.username, [("node", node.objId, "parrot")])
		relation2 = create_relation(self.user.id, self.user.username, [("node", node.objId, "parrot"), ("relation", relation.objId, "dead")])
		relation = modify_relation(self.user.id, self.user.username, relation, 
			[("node", node.objId, "parrot"), ("relation", relation2.objId, "dead")], {})

		xml = """<osmChange version="0.6" generator="JOSM">
		<delete>
		  <relation id='{}' version='{}' changeset='{}'/>
		  <relation id='{}' version='{}' changeset='{}'/>
		</delete>
		</osmChange>""".format(relation.objId, relation.metaData.version, cs.objId,
			relation2.objId, relation2.metaData.version, cs.objId)

		response = self.client.post(reverse('changeset:upload', args=(cs.objId,)), xml, 
			content_type='text/xml')
		#print (response.content)
		self.assertEqual(response.status_code, 200)

	def test_upload_create_node_way_version_one(self):

		cs = CreateTestChangeset(self.user, tags={"foo": "me"}, is_open=True)

		xml = """<osmChange version="0.6" generator="acme osm editor">
			<create>
				<node id="-1" changeset="{0}" version="1" lat="-33.9133123" lon="151.1173123" />
				<node id="-2" changeset="{0}" version="1" lat="-33.9233321" lon="151.1173321" />
				<way id="-3" changeset="{0}" version="1">
				    <nd ref="-1"/>
				    <nd ref="-2"/>
				</way>
			</create>
		</osmChange>""".format(cs.objId)

		response = self.client.post(reverse('changeset:upload', args=(cs.objId,)), xml, 
			content_type='text/xml')
		if response.status_code != 200:
			print (response.content)
		self.assertEqual(response.status_code, 200)

		xml = fromstring(response.content)
		self.assertEqual(len(xml), 3)
		diffDict = ParseOsmDiffToDict(xml)

		self.assertEqual(-1 in diffDict["node"], True)
		self.assertEqual(-2 in diffDict["node"], True)
		self.assertEqual(-3 in diffDict["way"], True)

		self.assertEqual(diffDict["node"][-1][1], 1)
		self.assertEqual(diffDict["node"][-2][1], 1)
		self.assertEqual(diffDict["way"][-3][1], 1)

	def test_upload_create_wrong_version(self):

		cs = CreateTestChangeset(self.user, tags={"foo": "me"}, is_open=True)

		xml = """<osmChange version="0.6" generator="acme osm editor">
			<create>
				<node id="-1" changeset="{0}" version="2" lat="-33.9133123" lon="151.1173123" />
			</create>
		</osmChange>""".format(cs.objId)

		response = self.client.post(reverse('changeset:upload', args=(cs.objId,)), xml, 
			content_type='text/xml')
		if response.status_code != 400:
			print (response.content)
		self.assertEqual(response.status_code, 400)

	def test_get_changeset_list(self):
		teststr = u"Съешь же ещё этих мягких французских булок да выпей чаю"
		cs = CreateTestChangeset(self.user, tags={"foo": "bar", 'test': teststr}, bbox=(-1.0893202,50.7942715,-1.0803509,50.7989372),
			is_open=True, open_timestamp=int(time.time())-60)
		cs2 = CreateTestChangeset(self.user, tags={"foo": "bar", 'test': teststr}, bbox=(-1.0893202,50.7942715,-1.0803509,50.7989372),
			is_open=False, open_timestamp=int(time.time())-120)

		anonClient = Client()

		response = anonClient.get(reverse('changeset:list'))
		self.assertEqual(response.status_code, 200)	
		xml = fromstring(response.content)
		CheckChangesetListContainsId(self, xml, cs.objId, True)
		CheckChangesetListContainsId(self, xml, cs2.objId, True)

		response = anonClient.get(reverse('changeset:list')+"?open=true")
		self.assertEqual(response.status_code, 200)	
		xml = fromstring(response.content)
		CheckChangesetListContainsId(self, xml, cs.objId, True)
		CheckChangesetListContainsId(self, xml, cs2.objId, False)

		response = anonClient.get(reverse('changeset:list')+"?closed=true")
		self.assertEqual(response.status_code, 200)	
		xml = fromstring(response.content)
		CheckChangesetListContainsId(self, xml, cs.objId, False)
		CheckChangesetListContainsId(self, xml, cs2.objId, True)

	def tearDown(self):
		u = User.objects.get(username = self.username)
		u.delete()
		u2 = User.objects.get(username = self.username2)
		u2.delete()

		errStr = pgmap.PgMapError()
		t = get_pgmap().GetTransaction("EXCLUSIVE")
		ok = t.ResetActiveTables(errStr)
		if not ok:
			print (errStr.errStr)
		t.Commit()

class ChangesetAutoCloseTestCase(TestCase):

	def setUp(self):
		self.username = "john"
		self.password = "glass onion"
		self.email = 'jlennon@beatles.com'
		self.user = User.objects.create_user(self.username, self.email, self.password)
		self.client = Client()
		self.client.login(username=self.username, password=self.password)

		self.username2 = "ringo"
		self.password2 = "penny lane"
		self.email2 = 'rstarr@beatles.com'
		self.user2 = User.objects.create_user(self.username2, self.email2, self.password2)
		self.client2 = Client()
		self.client2.login(username=self.username2, password=self.password2)

	def test_changeset_auto_close_active(self):
		teststr = u"Съешь же ещё этих мягких французских булок да выпей чаю"
		cs = CreateTestChangeset(self.user, tags={"foo": "bar", 'test': teststr}, bbox=(-1.0893202,50.7942715,-1.0803509,50.7989372),
			is_open=True, open_timestamp=int(time.time())-60)
		cs2 = CreateTestChangeset(self.user, tags={"foo": "bar", 'test': teststr}, bbox=(-1.0893202,50.7942715,-1.0803509,50.7989372),
			is_open=False, open_timestamp=int(time.time())-120)

		cs3 = CreateTestChangeset(self.user, tags={"foo": "bar", 'test': teststr}, bbox=(-1.0893202,50.7942715,-1.0803509,50.7989372),
			is_open=True, open_timestamp=int(time.time())-(36*60*60))
		cs4 = CreateTestChangeset(self.user, tags={"foo": "bar", 'test': teststr}, bbox=(-1.0893202,50.7942715,-1.0803509,50.7989372),
			is_open=False, open_timestamp=int(time.time())-(36*60*60))

		cmd = closeoldchangesets.Command()
		cmd.handle([], {})

		anonClient = Client()

		response = anonClient.get(reverse('changeset:changeset', args=(cs.objId,)))
		self.assertEqual(response.status_code, 200)	
		xml = fromstring(response.content)
		self.assertEqual(xml.tag, "osm")
		csout = xml.find("changeset")
		self.assertEqual(int(csout.attrib["id"]) == cs.objId, True)
		self.assertEqual(csout.attrib["open"], "true")

		response = anonClient.get(reverse('changeset:changeset', args=(cs2.objId,)))
		self.assertEqual(response.status_code, 200)	
		xml = fromstring(response.content)
		self.assertEqual(xml.tag, "osm")
		csout = xml.find("changeset")
		self.assertEqual(int(csout.attrib["id"]) == cs2.objId, True)
		self.assertEqual(csout.attrib["open"], "false")

		response = anonClient.get(reverse('changeset:changeset', args=(cs3.objId,)))
		self.assertEqual(response.status_code, 200)	
		xml = fromstring(response.content)
		self.assertEqual(xml.tag, "osm")
		csout = xml.find("changeset")
		self.assertEqual(int(csout.attrib["id"]) == cs3.objId, True)
		self.assertEqual(csout.attrib["open"], "false")

		response = anonClient.get(reverse('changeset:changeset', args=(cs4.objId,)))
		self.assertEqual(response.status_code, 200)	
		xml = fromstring(response.content)
		self.assertEqual(xml.tag, "osm")
		csout = xml.find("changeset")
		self.assertEqual(int(csout.attrib["id"]) == cs4.objId, True)
		self.assertEqual(csout.attrib["open"], "false")

	def tearDown(self):
		u = User.objects.get(username = self.username)
		u.delete()
		u2 = User.objects.get(username = self.username2)
		u2.delete()

		errStr = pgmap.PgMapError()
		t = get_pgmap().GetTransaction("EXCLUSIVE")
		ok = t.ResetActiveTables(errStr)
		if not ok:
			print (errStr.errStr)
		t.Commit()

class ChangesetQueryAndCountsTestCase(TestCase):

	def setUp(self):
		self.username = "george"
		self.password = "here comes the sun"
		self.user = User.objects.create_user(self.username, 'gharrison@beatles.com', self.password)
		self.client = Client()
		self.client.login(username=self.username, password=self.password)

	def tearDown(self):
		self.user.delete()
		errStr = pgmap.PgMapError()
		t = get_pgmap().GetTransaction("EXCLUSIVE")
		ok = t.ResetActiveTables(errStr)
		if not ok:
			print (errStr.errStr)
		t.Commit()

	def listed_ids(self, query):
		"""IDs of this user's changesets found by a query, in the order listed."""
		response = Client().get("/api/0.6/changesets?user={}&{}".format(self.user.id, query))
		self.assertEqual(response.status_code, 200, response.content)
		return [int(cs.attrib["id"]) for cs in fromstring(response.content)]

	def upload(self, cs, xml, **extra):
		return self.client.post(reverse('changeset:upload', args=(cs.objId,)), xml,
			content_type='text/xml', **extra)

	def test_changeset_counts(self):
		cs = CreateTestChangeset(self.user, is_open=True)
		empty = CreateTestChangeset(self.user, is_open=True)

		response = self.upload(cs, """<osmChange version="0.6"><create>
			<node changeset="{0}" id="-1" lat="50.1" lon="-1.1" />
			<node changeset="{0}" id="-2" lat="50.2" lon="-1.2" />
			<node changeset="{0}" id="-3" lat="50.3" lon="-1.3" />
			</create></osmChange>""".format(cs.objId))
		self.assertEqual(response.status_code, 200, response.content)
		ids = ParseOsmDiffToDict(fromstring(response.content))["node"]
		response = self.upload(cs, """<osmChange version="0.6">
			<modify><node changeset="{0}" id="{1}" version="1" lat="50.15" lon="-1.15" /></modify>
			<delete><node changeset="{0}" id="{2}" version="1" lat="50.2" lon="-1.2" /></delete>
			</osmChange>""".format(cs.objId, ids[-1][0], ids[-2][0]))
		self.assertEqual(response.status_code, 200, response.content)

		expected = {"comments_count": 0, "changes_count": 5, "created_count": 3,
			"modified_count": 1, "deleted_count": 1}
		none = dict.fromkeys(expected, 0)

		xml = fromstring(Client().get("/api/0.6/changeset/{}".format(cs.objId)).content).find("changeset")
		self.assertEqual({k: int(xml.attrib[k]) for k in expected}, expected)
		doc = json.loads(Client().get("/api/0.6/changeset/{}.json".format(cs.objId)).content)["changeset"]
		self.assertEqual({k: doc[k] for k in expected}, expected)
		xml = fromstring(Client().get("/api/0.6/changeset/{}".format(empty.objId)).content).find("changeset")
		self.assertEqual({k: int(xml.attrib[k]) for k in expected}, none)

		# The list reports the same counts for each changeset
		listed = {int(c.attrib["id"]): c for c in fromstring(
			Client().get("/api/0.6/changesets?user={}".format(self.user.id)).content)}
		self.assertEqual({k: int(listed[cs.objId].attrib[k]) for k in expected}, expected)
		self.assertEqual({k: int(listed[empty.objId].attrib[k]) for k in expected}, none)
		listed = {c["id"]: c for c in json.loads(
			Client().get("/api/0.6/changesets.json?user={}".format(self.user.id)).content)["changesets"]}
		self.assertEqual({k: listed[cs.objId][k] for k in expected}, expected)

		# Closing a changeset reports what it held
		response = self.client.put(reverse('changeset:close', args=(cs.objId,)))
		self.assertEqual(response.status_code, 200)
		xml = fromstring(response.content).find("changeset")
		self.assertEqual(int(xml.attrib["changes_count"]), 5)

	def test_changeset_query_order_limit_and_times(self):
		base = 1500000000 # 2017-07-14T02:40:00Z
		def iso(offset):
			return datetime.datetime.fromtimestamp(base + offset, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
		# Opened 1000s apart; all but the newest closed 500s after opening
		css = [CreateTestChangeset(self.user, is_open=(i == 4), open_timestamp=base + i * 1000,
			close_timestamp=base + i * 1000 + 500, bbox=(i, i, i + 0.5, i + 0.5)).objId for i in range(5)]

		self.assertEqual(self.listed_ids(""), css[::-1])
		self.assertEqual(self.listed_ids("order=newest"), css[::-1])
		self.assertEqual(self.listed_ids("limit=2"), [css[4], css[3]])
		self.assertEqual(self.listed_ids("limit=2&order=oldest"), [css[0], css[1]])
		self.assertEqual(self.listed_ids("order=oldest"), css)

		# from is inclusive, to is exclusive, and to alone does nothing
		self.assertEqual(self.listed_ids("from=" + iso(2000)), [css[4], css[3], css[2]])
		self.assertEqual(self.listed_ids("from={}&to={}".format(iso(1000), iso(3000))), [css[2], css[1]])
		self.assertEqual(self.listed_ids("from={}&to={}&order=oldest&limit=1".format(iso(1000), iso(3000))), [css[1]])
		self.assertEqual(self.listed_ids("to=" + iso(2000)), css[::-1])
		# Times may also be whole seconds, or a time with an offset
		self.assertEqual(self.listed_ids("from={}".format(base + 3000)), [css[4], css[3]])
		self.assertEqual(self.listed_ids("from=2017-07-14T04:30:00%2B01:00"), [css[4], css[3]])

		# time=T1 finds changesets closed after T1 or still open; T2 limits by creation
		self.assertEqual(self.listed_ids("time=" + iso(2600)), [css[4], css[3]])
		self.assertEqual(self.listed_ids("time=" + iso(2500)), [css[4], css[3], css[2]])
		self.assertEqual(self.listed_ids("time={},{}".format(iso(1600), iso(3000))), [css[2]])

		# Changesets by ID and by area
		self.assertEqual(self.listed_ids("changesets={},{}".format(css[3], css[0])), [css[3], css[0]])
		self.assertEqual(self.listed_ids("bbox=1.6,1.6,3.2,3.2"), [css[3], css[2]])
		self.assertEqual(self.listed_ids("open=true&order=oldest"), [css[4]])

		for bad in ("limit=0", "limit=-1", "limit=abc", "limit=101", "order=sideways",
			"from=yesterday", "time=1,2,3", "time=" + iso(0) + "&order=oldest",
			"changesets=", "changesets=a,b", "bbox=1,2,3", "bbox=3,3,1,1"):
			response = Client().get("/api/0.6/changesets?" + bad)
			self.assertEqual(response.status_code, 400, bad)
		self.assertEqual(Client().get("/api/0.6/changesets?user=1&display_name=george").status_code, 400)

	@override_settings(CHANGESETS_DEFAULT_QUERY_LIMIT=2, CHANGESETS_MAXIMUM_QUERY_LIMIT=3)
	def test_changeset_query_limit_settings(self):
		css = [CreateTestChangeset(self.user, open_timestamp=1500000000 + i).objId for i in range(4)]
		self.assertEqual(self.listed_ids(""), [css[3], css[2]])
		self.assertEqual(self.listed_ids("limit=3"), [css[3], css[2], css[1]])
		self.assertEqual(Client().get("/api/0.6/changesets?limit=4").status_code, 400)
		xml = fromstring(Client().get("/api/0.6/capabilities").content).find("api/changesets")
		self.assertEqual((xml.attrib["default_query_limit"], xml.attrib["maximum_query_limit"]), ("2", "3"))

	def test_compressed_uploads(self):
		import gzip, zlib
		cs = CreateTestChangeset(self.user, is_open=True)
		def change(ident):
			return """<osmChange version="0.6"><create>
				<node changeset="{}" id="{}" lat="50.1" lon="-1.1" />
				</create></osmChange>""".format(cs.objId, ident).encode("utf-8")

		response = self.upload(cs, gzip.compress(change(-1)), HTTP_CONTENT_ENCODING="gzip")
		self.assertEqual(response.status_code, 200, response.content)
		created = ParseOsmDiffToDict(fromstring(response.content))["node"][-1][0]
		self.assertIsNotNone(GetObj(get_pgmap(), "node", created))

		response = self.upload(cs, zlib.compress(change(-2)), HTTP_CONTENT_ENCODING="deflate")
		self.assertEqual(response.status_code, 200, response.content)
		response = self.upload(cs, change(-3), HTTP_CONTENT_ENCODING="identity")
		self.assertEqual(response.status_code, 200, response.content)

		# A body larger than any one read of the compressed or decompressed data
		nodes = "".join('<node changeset="{}" id="-{}" lat="50.1" lon="-1.1"><tag k="note" v="{}" /></node>'.format(
			cs.objId, i + 10, "%032x" % random.getrandbits(128)) for i in range(3000))
		big = gzip.compress('<osmChange version="0.6"><create>{}</create></osmChange>'.format(nodes).encode("utf-8"))
		self.assertGreater(len(big), 40000)
		response = self.upload(cs, big, HTTP_CONTENT_ENCODING="gzip")
		self.assertEqual(response.status_code, 200, response.content[:300])
		self.assertEqual(len(fromstring(response.content)), 3000)

		# Requests that are not what they claim to be change nothing
		whole = gzip.compress(change(-4))
		for body, encoding, status in ((whole[:len(whole) // 2], "gzip", 400), (change(-4), "gzip", 400),
			(whole, "deflate", 400), (whole, "br", 415)):
			response = self.upload(cs, body, HTTP_CONTENT_ENCODING=encoding)
			self.assertEqual(response.status_code, status, (encoding, response.content))
		xml = fromstring(Client().get("/api/0.6/changeset/{}".format(cs.objId)).content).find("changeset")
		self.assertEqual(int(xml.attrib["created_count"]), 3003)

		# The other XML request bodies are decompressed the same way
		body = gzip.compress(b'<osm><changeset><tag k="comment" v="compressed" /></changeset></osm>')
		response = self.client.put(reverse('changeset:create'), body, content_type='text/xml',
			HTTP_CONTENT_ENCODING="gzip")
		self.assertEqual(response.status_code, 200, response.content)
		xml = fromstring(Client().get("/api/0.6/changeset/{}".format(int(response.content))).content)
		self.assertEqual(xml.find("changeset/tag").attrib["v"], "compressed")
		node = gzip.compress('<osm><node changeset="{}" lat="50.1" lon="-1.1" /></osm>'.format(cs.objId).encode("utf-8"))
		response = self.client.post("/api/0.6/nodes", node, content_type='text/xml', HTTP_CONTENT_ENCODING="gzip")
		self.assertEqual(response.status_code, 200, response.content)

	@override_settings(XML_UPLOAD_MAXIMUM_BYTES=100000)
	def test_compressed_upload_limit_applies_to_decompressed_size(self):
		import gzip
		cs = CreateTestChangeset(self.user, is_open=True)
		# A few kilobytes compressed, far over the limit once decompressed
		bomb = gzip.compress(b'<osmChange version="0.6"><create>' + b" " * 50000000 + b'</create></osmChange>')
		self.assertLess(len(bomb), 100000)
		response = self.upload(cs, bomb, HTTP_CONTENT_ENCODING="gzip")
		self.assertEqual(response.status_code, 400)
		self.assertIn(b"XML_UPLOAD_MAXIMUM_BYTES", response.content)
		body = gzip.compress(b'<osm><changeset>' + b" " * 50000000 + b'</changeset></osm>')
		response = self.client.put(reverse('changeset:create'), body, content_type='text/xml',
			HTTP_CONTENT_ENCODING="gzip")
		self.assertEqual(response.status_code, 400)
