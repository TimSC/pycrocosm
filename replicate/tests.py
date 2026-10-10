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
		self.assertRegex(unescape(links["edit_activities"]), r"^edit_activities\?first_id=(\d+)&last_id=\1$")
		answer = client.get("/replication/" + links["edit_ids"])
		self.assertEqual(answer.status_code, 200)
		self.assertIn(b"<editids ", answer.content)
		answer = client.get("/replication/" + unescape(links["edit_activities"]))
		self.assertEqual(answer.status_code, 200)
		self.assertIn(b"<editactivities", answer.content)
		# The page is shown even if the map cannot say where its edits have got to
		with patch("replicate.views.get_pgmap", side_effect=RuntimeError("no map")):
			html = client.get("/replication/").content.decode("utf-8")
		self.assertIn("edit_activities?first_id=1&amp;last_id=1", html)

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

	def edit(self, changeset_id, timestamp):
		"""Create a node as an upload does, which is what records edit activity."""
		import xml.etree.ElementTree as ET
		from changeset.views import upload_block
		node = pgmap.OsmNode()
		node.objId = -1
		node.metaData.changeset = changeset_id
		node.lat, node.lon = 43.0, -70.3
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
			# A request by time, or with no filter at all, is limited the same way
			root, ids = self.ask("since=2026-01-31T12:02:00Z")
			self.assertEqual((ids, root.get("truncated"), int(root.get("next_id"))),
				(everything[1:3], "true", everything[2] + 1))
			root, ids = self.ask("")
			self.assertEqual((ids, root.get("truncated")), (everything[:2], "true"))
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
