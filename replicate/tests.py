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



class ReplicationIndexTestCase(TestCase):
	def test_index_links_to_the_edit_activity_api(self):
		import re
		from html import unescape
		from unittest.mock import patch
		client = Client()
		response = client.get("/replication/")
		self.assertEqual(response.status_code, 200)
		html = response.content.decode("utf-8")
		for name in ("minute/", "hour/", "day/"):
			self.assertIn("href='{}'".format(name), html)
		# The lists of what can be downloaded, and the recent changesets, are reached from here
		for ident, target in (("changesets", reverse("objectinfo:history")), ("extracts", "extracts"), ("planet", "planet")):
			self.assertIn("<a id='{}' href='{}'>".format(ident, target), html)
			path = target if target.startswith("/") else "/replication/" + target
			self.assertEqual(client.get(path).status_code, 200, path)
		front = client.get(reverse("frontpage:index")).content.decode("utf-8")
		self.assertIn('href="/replication/"', front)
		for moved in ("Recent changesets in html format", "Map extracts to download", "Planet dumps to download"):
			self.assertIn(moved, html)
			self.assertNotIn(moved, front)
		# Both links are relative to the page, and both work
		links = dict(re.findall(r"<a id='(\w+)' href='([^']*)'", html))
		self.assertEqual(links["edit_ids"], "edit_ids")
		# The records link needs no arguments: it shows the latest
		self.assertEqual(links["edit_activities"], "edit_activities")
		answer = client.get("/replication/" + links["edit_ids"])
		self.assertEqual(answer.status_code, 200)
		self.assertIn(b"<editids ", answer.content)
		answer = client.get("/replication/" + unescape(links["edit_activities"]))
		self.assertEqual(answer.status_code, 200)
		self.assertIn(b"<editactivities", answer.content)
		# The page is shown even if the map cannot say where its edits have got to
		with patch("replicate.views.get_pgmap", side_effect=RuntimeError("no map")):
			html = client.get("/replication/").content.decode("utf-8")
		self.assertIn("href='edit_activities'", html)

def o5m_objects(document):
	"""The objects of an o5m document, as (type, ID, version, tags, and the
	position, nodes or members, as the case may be)."""
	data = pgmap.OsmData()
	pgmap.LoadFromO5mBytes(document, data)
	return objects_of(data)

def objects_of(data):
	out = []
	for i in range(data.nodes.size()):
		n = data.nodes[i]
		out.append(("node", n.objId, n.metaData.version, dict(n.tags), round(n.lon, 7), round(n.lat, 7)))
	for i in range(data.ways.size()):
		w = data.ways[i]
		out.append(("way", w.objId, w.metaData.version, dict(w.tags), list(w.refs)))
	for i in range(data.relations.size()):
		r = data.relations[i]
		out.append(("relation", r.objId, r.metaData.version, dict(r.tags),
			[(int(m.type), m.ref, m.role) for m in r.members]))
	return out

def xml_record_as_msgpack(row):
	"""An <editactivity> element as the msgpack format gives the record, with
	each list's objects as o5m_objects gives them, for comparing the two."""
	import calendar
	import xml.etree.ElementTree as ET
	record = {"id": int(row.get("id")), "action": row.get("action")}
	for name in ("changeset", "atomic_edit_id", "block_index"):
		if row.get(name) is not None:
			record[name] = int(row.get(name))
	if row.get("timestamp") is not None:
		record["timestamp"] = calendar.timegm(datetime.datetime.strptime(row.get("timestamp"), "%Y-%m-%dT%H:%M:%SZ").timetuple())
	for child in row:
		if child.get("null") == "true":
			record[child.tag] = None
		elif child.tag.endswith("_bbox"):
			record[child.tag] = [[b.get("type"), int(b.get("id"))] + ([] if b.get("null") == "true" else
				[float(b.get(k)) for k in ("minlon", "minlat", "maxlon", "maxlat")]) for b in child]
		else:
			data = pgmap.OsmData()
			pgmap.LoadFromOsmXml("<osm version='0.6'>" + "".join(ET.tostring(o, encoding="unicode") for o in child) + "</osm>", data)
			record[child.tag] = objects_of(data)
	return record

def msgpack_record_decoded(record):
	"""A msgpack format record with its o5m documents decoded by o5m_objects."""
	return {k: o5m_objects(v) if isinstance(v, bytes) else v for k, v in record.items()}

class EditActivityApiTestCase(TestCase):
	"""Limits and times of the edit activity API."""

	def setUp(self):
		self.user = User.objects.create_user("activist", "activist@example.com", "password")
		# Five edits, two minutes apart, starting at 2026-01-31T12:00:00Z
		self.start = 1769860800
		from changeset.tests import CreateTestChangeset
		changeset = CreateTestChangeset(self.user)
		for i in range(5):
			self.edit(changeset.objId, self.start + 120 * i)

	def edit(self, changeset_id, timestamp, lon=-70.3, lat=43.0):
		"""Create a node as an upload does, which is what records edit activity."""
		import xml.etree.ElementTree as ET
		from changeset.views import upload_block
		node = pgmap.OsmNode()
		node.objId = -1
		node.metaData.changeset = changeset_id
		node.lat, node.lon = lat, lon
		data = pgmap.OsmData()
		data.nodes.append(node)
		t = get_pgmap().GetTransaction("EXCLUSIVE")
		try:
			result = upload_block("create", data, changeset_id, t, ET.Element("diffResult"),
				self.user.id, self.user.username, timestamp,
				pgmap.mapi64i64(), pgmap.mapi64i64(), pgmap.mapi64i64())
			self.assertIs(result, True, getattr(result, "content", result))
			t.Commit()
		except BaseException:
			t.Abort()
			raise

	def tearDown(self):
		errStr = pgmap.PgMapError()
		t = get_pgmap().GetTransaction("EXCLUSIVE")
		t.ResetActiveTables(errStr)
		t.Commit()

	def ask(self, query):
		import xml.etree.ElementTree as ET
		response = Client().get("/replication/edit_activities?" + query)
		self.assertEqual(response.status_code, 200, response.content)
		root = ET.fromstring(response.content)
		return root, [int(row.get("id")) for row in root.findall("editactivity")]

	def test_times_do_not_depend_on_the_servers_zone(self):
		import calendar, datetime, os, time
		from replicate.views import utc_epoch, utc_text
		naive = datetime.datetime(2026, 7, 1, 12, 0, 0)
		offset = datetime.datetime(2026, 7, 1, 14, 0, 0, tzinfo=datetime.timezone(datetime.timedelta(hours=2)))
		previous = os.environ.get("TZ")
		try:
			# A zone that is neither UTC nor on UTC in July
			for zone in ("UTC", "Europe/London", "America/New_York"):
				os.environ["TZ"] = zone
				time.tzset()
				self.assertEqual(utc_epoch(naive), calendar.timegm(naive.timetuple()), zone)
				self.assertEqual(utc_epoch(offset), utc_epoch(naive), zone)
				self.assertEqual(utc_text(1769860800), "2026-01-31T12:00:00Z", zone)
		finally:
			if previous is None:
				del os.environ["TZ"]
			else:
				os.environ["TZ"] = previous
			time.tzset()

	def test_since_and_until(self):
		root, everything = self.ask("first_id=1")
		self.assertEqual(len(everything), 5)
		# Each answer is a whole XML document, starting with its declaration
		header = b"<?xml version='1.0' encoding='UTF-8'?>\n<"
		for path in ("/replication/edit_activities?first_id=1", "/replication/edit_activity/{}".format(everything[0]),
			"/replication/edit_ids"):
			response = Client().get(path)
			self.assertEqual(response.status_code, 200, path)
			self.assertTrue(response.content.startswith(header), path)
			self.assertEqual(response["Content-Type"], "text/xml")
		# Each record says when, in UTC and marked as such
		self.assertEqual([row.get("timestamp") for row in root.findall("editactivity")], [
			"2026-01-31T12:00:00Z", "2026-01-31T12:02:00Z", "2026-01-31T12:04:00Z",
			"2026-01-31T12:06:00Z", "2026-01-31T12:08:00Z"])
		# since is inclusive and until exclusive; without a zone they are UTC
		self.assertEqual(self.ask("since=2026-01-31T12:04:00Z")[1], everything[2:])
		self.assertEqual(self.ask("since=2026-01-31T12:04:00")[1], everything[2:])
		self.assertEqual(self.ask("since=2026-01-31T12:02:00Z&until=2026-01-31T12:06:00Z")[1], everything[1:3])
		self.assertEqual(self.ask("until=2026-01-31T12:02:00Z")[1], everything[:1])
		# A time with an offset means that moment: 13:04 at +01:00 is 12:04 UTC
		self.assertEqual(self.ask("since=2026-01-31T13:04:00%2B01:00")[1], everything[2:])
		self.assertEqual(self.ask("since=2026-01-31T12:10:00Z")[1], [])
		for bad in ("since=yesterday", "until=2026-13-45T00:00:00Z", "since="):
			response = Client().get("/replication/edit_activities?" + bad)
			self.assertEqual(response.status_code, 400, bad)

	def test_msgpack_format(self):
		import gzip
		import msgpack
		from changeset.tests import CreateTestChangeset
		changeset = CreateTestChangeset(self.user)
		self.edit(changeset.objId, self.start + 900, 10.5, 20.5)
		for query in ("first_id=1", "first_id=1&limit=2", "bbox=10,20,11,21", "since=2026-01-31T12:04:00Z&limit=1"):
			root, ids = self.ask(query)
			response = Client().get("/replication/edit_activities?format=msgpack.gz&" + query)
			self.assertEqual(response.status_code, 200, query)
			self.assertEqual(response["Content-Type"], "application/x-gzip")
			document = msgpack.unpackb(gzip.decompress(response.content))
			# The same records, with the same objects and boxes
			self.assertEqual([msgpack_record_decoded(r) for r in document["editactivities"]],
				[xml_record_as_msgpack(row) for row in root.findall("editactivity")], query)
			# Where to go on from, as values
			self.assertEqual({k: v for k, v in document.items() if k != "editactivities"},
				{"truncated": True, "next_id": int(root.get("next_id"))} if root.get("truncated") else {}, query)
		# Objects are o5m encoded, and an edit's records hold some
		record = document["editactivities"][0]
		self.assertTrue(record["updated"].startswith(b"\xff"))
		self.assertEqual([o[0] for o in o5m_objects(record["updated"])], ["node"])
		# One record by ID too
		response = Client().get("/replication/edit_activity/{}?format=msgpack.gz".format(ids[0]))
		self.assertEqual(response.status_code, 200)
		self.assertEqual([r["id"] for r in msgpack.unpackb(gzip.decompress(response.content))["editactivities"]], ids[:1])
		self.assertEqual(Client().get("/replication/edit_activity/{}?format=xml".format(ids[0]))["Content-Type"], "text/xml")
		# Only gzipped
		for path in ("/replication/edit_activities?format=msgpack", "/replication/edit_activities?format=json",
				"/replication/edit_activities?format=",
				"/replication/edit_activity/{}?format=o5m".format(ids[0])):
			self.assertEqual(Client().get(path).status_code, 400, path)

	def test_latest_records_by_default(self):
		root, everything = self.ask("first_id=1")
		# Fewer than the default number: all of them
		self.assertEqual(self.ask("")[1], everything)
		with self.settings(EDIT_ACTIVITY_DEFAULT_RECORDS=2):
			root, ids = self.ask("")
			self.assertEqual((ids, root.get("truncated"), root.get("next_id")), (everything[-2:], None, None))
			# A range starts from its beginning and is not held to the default
			self.assertEqual(self.ask("first_id=1")[1], everything)
			self.assertEqual(self.ask("since=2026-01-31T12:00:00Z")[1], everything)
		# limit says how many: the latest, or the first of a range with where to go on
		self.assertEqual(self.ask("limit=3")[1], everything[-3:])
		self.assertEqual(self.ask("limit=100")[1], everything)
		root, ids = self.ask("first_id=1&limit=2")
		self.assertEqual((ids, root.get("truncated"), int(root.get("next_id"))), (everything[:2], "true", everything[1] + 1))
		root, ids = self.ask("since=2026-01-31T12:02:00Z&limit=1")
		self.assertEqual((ids, root.get("truncated")), (everything[1:2], "true"))
		for bad in ("limit=0", "limit=101", "limit=-1", "limit=many", "limit="):
			self.assertEqual(Client().get("/replication/edit_activities?" + bad).status_code, 400, bad)
		with self.settings(EDIT_ACTIVITY_MAXIMUM_RECORDS=2):
			self.assertEqual(Client().get("/replication/edit_activities?limit=3").status_code, 400)
			self.assertEqual(self.ask("")[1], everything[-2:])

	def test_bbox_filter(self):
		from changeset.tests import CreateTestChangeset
		root, home = self.ask("first_id=1")
		changeset = CreateTestChangeset(self.user)
		self.edit(changeset.objId, self.start + 900, 10.5, 20.5)
		self.edit(changeset.objId, self.start + 960)
		root, everything = self.ask("first_id=1")
		away = [everything[5]]
		self.assertEqual(self.ask("first_id=1&bbox=10,20,11,21")[1], away)
		self.assertEqual(self.ask("first_id=1&bbox=-71,42,-70,44")[1], home + everything[6:])
		self.assertEqual(self.ask("first_id=1&bbox=-180,-90,180,90")[1], everything)
		self.assertEqual(self.ask("first_id=1&bbox=100,0,101,1")[1], [])
		# An area as small as the point itself
		self.assertEqual(self.ask("first_id=1&bbox=10.5,20.5,10.5,20.5")[1], away)
		# With the other ways of choosing: the latest, times, a limit to carry on from
		self.assertEqual(self.ask("bbox=10,20,11,21")[1], away)
		self.assertEqual(self.ask("bbox=-71,42,-70,44&limit=2")[1], [everything[4], everything[6]])
		self.assertEqual(self.ask("since=2026-01-31T12:08:00Z&bbox=-71,42,-70,44")[1], [everything[4], everything[6]])
		root, ids = self.ask("first_id=1&bbox=-71,42,-70,44&limit=5")
		self.assertEqual((ids, root.get("truncated"), int(root.get("next_id"))), (home, "true", home[-1] + 1))
		self.assertEqual(self.ask("first_id={}&bbox=-71,42,-70,44&limit=5".format(root.get("next_id")))[1], everything[6:])
		for bad in ("bbox=1,2,3", "bbox=1,2,3,4,5", "bbox=a,b,c,d", "bbox=", "bbox=3,2,1,4", "bbox=1,4,3,2",
			"bbox=-181,0,1,1", "bbox=0,0,1,91", "bbox=nan,0,1,1", "bbox=0,0,inf,1"):
			self.assertEqual(Client().get("/replication/edit_activities?" + bad).status_code, 400, bad)

	def test_answers_are_limited(self):
		root, everything = self.ask("first_id=1")
		self.assertIsNone(root.get("truncated"))
		with self.settings(EDIT_ACTIVITY_MAXIMUM_RECORDS=2):
			# The first two, and where to go on from
			root, ids = self.ask("first_id=1")
			self.assertEqual(ids, everything[:2])
			self.assertEqual((root.get("truncated"), int(root.get("next_id"))), ("true", everything[1] + 1))
			root, ids = self.ask("first_id={}".format(root.get("next_id")))
			self.assertEqual(ids, everything[2:4])
			root, ids = self.ask("first_id={}".format(root.get("next_id")))
			self.assertEqual((ids, root.get("truncated"), root.get("next_id")), (everything[4:], None, None))
			# A request by time is limited the same way
			root, ids = self.ask("since=2026-01-31T12:02:00Z")
			self.assertEqual((ids, root.get("truncated"), int(root.get("next_id"))),
				(everything[1:3], "true", everything[2] + 1))
			# With no range at all the latest are sent, and that is not cut short
			root, ids = self.ask("")
			self.assertEqual((ids, root.get("truncated")), (everything[-2:], None))
			# Exactly as many as allowed is not cut short
			root, ids = self.ask("first_id={}&last_id={}".format(everything[0], everything[1]))
			self.assertEqual((ids, root.get("truncated")), (everything[:2], None))

			# Something following the records goes on until it has the whole range
			from replicate.remoteupdate import RemoteServer, RemoteUpdateError
			asked = []
			def fetch(url):
				asked.append(url.split("?", 1)[1])
				return Client().get(url[len("http://remote.test"):]).content
			rows = RemoteServer("http://remote.test/", fetch).activities(everything[0], everything[4])
			self.assertEqual([int(row.get("id")) for row in rows], everything)
			self.assertEqual(len(asked), 3)
			with self.assertRaisesRegex(RemoteUpdateError, "without saying where to continue"):
				RemoteServer("http://remote.test/", lambda url: b'<editactivities truncated="true"/>').activities(1, 9)

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

class ChangedSinceTestCase(TestCase):
	"""Whether an area has been edited since an atomic edit (replication/changed),
	from the edit IDs of the objects themselves. Edits are made as uploads make
	them, which number them and record their activity."""

	def setUp(self):
		from changeset.tests import CreateTestChangeset
		self.user = User.objects.create_user("cartographer", "cartographer@example.com", "password")
		self.reset()
		self.changeset = CreateTestChangeset(self.user).objId

	def tearDown(self):
		self.reset()

	def reset(self):
		errStr = pgmap.PgMapError()
		t = get_pgmap().GetTransaction("EXCLUSIVE")
		self.assertTrue(t.ResetActiveTables(errStr), errStr.errStr)
		t.Commit()

	def upload(self, action, *objects):
		"""Uploads objects (nodes, ways and relations) as one block; returns the
		IDs given to new ones, by type."""
		import xml.etree.ElementTree as ET
		from changeset.views import upload_block
		data = pgmap.OsmData()
		for obj in objects:
			obj.metaData.changeset = self.changeset
			{pgmap.OsmNode: data.nodes, pgmap.OsmWay: data.ways, pgmap.OsmRelation: data.relations}[type(obj)].append(obj)
		created = {"node": pgmap.mapi64i64(), "way": pgmap.mapi64i64(), "relation": pgmap.mapi64i64()}
		t = get_pgmap().GetTransaction("EXCLUSIVE")
		try:
			result = upload_block(action, data, self.changeset, t, ET.Element("diffResult"), self.user.id,
				self.user.username, int(time.time()), created["node"], created["way"], created["relation"])
			self.assertIs(result, True, getattr(result, "content", result))
			t.Commit()
		except BaseException:
			t.Abort()
			raise
		return created

	def node(self, lon, lat, objId=-1, version=1):
		node = pgmap.OsmNode()
		node.objId, node.metaData.version = objId, version
		node.lat, node.lon = lat, lon
		return node

	def new_node(self, lon, lat):
		return self.upload("create", self.node(lon, lat))["node"][-1]

	def way(self, refs, objId=-1, version=1, tags=None):
		way = pgmap.OsmWay()
		way.objId, way.metaData.version = objId, version
		for ref in refs:
			way.refs.append(ref)
		for k, v in (tags or {}).items():
			way.tags[k] = v
		return way

	def relation(self, members, objId=-1, version=1, tags=None):
		relation = pgmap.OsmRelation()
		relation.objId, relation.metaData.version = objId, version
		for member in members:
			relation.AddMember(*member)
		for k, v in (tags or {}).items():
			relation.tags[k] = v
		return relation

	def latest(self):
		"""The map's latest atomic edit."""
		t = get_pgmap().GetTransaction("ACCESS SHARE")
		try:
			return t.GetLatestEditIds()[1]
		finally:
			t.Abort()

	def changed(self, bbox, since):
		import xml.etree.ElementTree as ET
		response = Client().get("/replication/changed?bbox={},{},{},{}&since={}".format(*bbox, since))
		self.assertEqual(response.status_code, 200, response.content)
		self.assertEqual(response["Content-Type"], "text/xml")
		root = ET.fromstring(response.content)
		self.assertEqual(root.tag, "changed")
		self.assertEqual(int(root.get("since")), since)
		self.assertEqual(int(root.get("atomic_edit_id")), self.latest())
		return root.get("changed") == "true"

	@staticmethod
	def around(lon, lat, d=0.001):
		return [lon - d, lat - d, lon + d, lat + d]

	def test_a_new_node(self):
		before = self.latest()
		self.new_node(-70.3, 43.0)
		self.assertGreater(self.latest(), before)
		here = self.around(-70.3, 43.0)
		self.assertTrue(self.changed(here, before))
		# Not since the edit that made it, nor anywhere else
		self.assertFalse(self.changed(here, self.latest()))
		self.assertFalse(self.changed(self.around(-70.0, 43.0), before))

	def test_a_changed_way(self):
		a, b = self.new_node(-70.3, 43.0), self.new_node(-70.299, 43.0)
		way = self.upload("create", self.way([a, b]))["way"][-1]
		before = self.latest()
		self.upload("modify", self.way([a, b], way, 1, {"highway": "track"}))
		self.assertTrue(self.changed(self.around(-70.2995, 43.0), before))
		self.assertFalse(self.changed(self.around(-70.2995, 43.0), self.latest()))

	def test_a_node_moved_away(self):
		node = self.new_node(-70.3, 43.0)
		before = self.latest()
		self.upload("modify", self.node(-70.1, 43.1, node, 1))
		# Where it was, which only its old version says, and where it is
		self.assertTrue(self.changed(self.around(-70.3, 43.0), before))
		self.assertTrue(self.changed(self.around(-70.1, 43.1), before))

	def test_a_deleted_node(self):
		node = self.new_node(-70.3, 43.0)
		before = self.latest()
		self.upload("delete", self.node(-70.3, 43.0, node, 1))
		self.assertTrue(self.changed(self.around(-70.3, 43.0), before))

	def test_a_deleted_way(self):
		# Its nodes stay, untouched: only the old way says where it was
		a, b = self.new_node(-70.3, 43.0), self.new_node(-70.299, 43.0)
		way = self.upload("create", self.way([a, b]))["way"][-1]
		before = self.latest()
		self.upload("delete", self.way([a, b], way, 1))
		self.assertTrue(self.changed(self.around(-70.2995, 43.0), before))

	def test_a_changed_relation(self):
		node = self.new_node(-70.3, 43.0)
		relation = self.upload("create", self.relation([("node", node, "label")]))["relation"][-1]
		before = self.latest()
		self.upload("modify", self.relation([("node", node, "label")], relation, 1, {"type": "site"}))
		self.assertTrue(self.changed(self.around(-70.3, 43.0), before))

	def test_a_node_of_the_imported_map_taken_over(self):
		# The edit goes to the active tables; the imported map keeps the old version
		roi = [-1.0684204, 50.8038735, -1.0510826, 50.812877]
		data = qmt.DecodeOsmdataResponse(Client().get(reverse('querymap:querymap') +
			"?bbox={},{},{},{}".format(*roi)).streaming_content)
		members = {data.ways[i].refs[j] for i in range(data.ways.size()) for j in range(data.ways[i].refs.size())}
		free = [data.nodes[i] for i in range(data.nodes.size()) if data.nodes[i].objId not in members]
		if not free:
			self.skipTest("No node of the imported map, in no way, in the test area")
		node = free[0]
		before = self.latest()
		self.upload("modify", self.node(node.lon + 0.2, node.lat + 0.1, node.objId, node.metaData.version))
		self.assertTrue(self.changed(self.around(node.lon, node.lat), before))

	def test_a_way_changed_only_by_a_node_outside_is_not_seen(self):
		# A known gap: the way crossing the area is copied by the edit, with
		# no edit ID of its own, and the node that moved was never in the area
		a, b = self.new_node(-70.31, 43.0), self.new_node(-70.29, 43.0)
		self.upload("create", self.way([a, b]))
		before = self.latest()
		self.upload("modify", self.node(-70.29, 43.002, b, 1))
		self.assertFalse(self.changed(self.around(-70.3, 43.0), before))

	def test_bad_requests(self):
		for query in ("since=0", "bbox=1,2,3&since=0", "bbox=3,2,1,4&since=0", "bbox=-1,50,0,51",
				"bbox=-1,50,0,51&since=-1", "bbox=-1,50,0,51&since=x"):
			response = Client().get("/replication/changed?" + query)
			self.assertEqual(response.status_code, 400, query)
