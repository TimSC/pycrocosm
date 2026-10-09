import json

from django.test import TestCase
from django.test import Client
from django.urls import reverse
from django.contrib.auth.models import User

from pycrocosm.mapdb import get_pgmap
from querymap.tests import create_node, create_way, DecodeOsmdataResponse
from changeset.tests import CreateTestChangeset
import gc
import sys
from changeset.views import GetOsmDataIndex

# Create your tests here.

class ElementsTestCase(TestCase):
	def setUp(self):
		self.username = "john"
		self.password = "glass onion"
		self.email = 'jlennon@beatles.com'
		self.user = User.objects.create_user(self.username, self.email, self.password)
		self.client = Client()
		self.client.login(username=self.username, password=self.password)

	#def test_put_node(self):

	#	response = self.client.put(reverse('elements:element', args=['node', '5']), "", content_type='text/xml')

	#	self.assertEqual(response.status_code, 200)

	def test_create_node(self):
		cs = CreateTestChangeset(self.user, tags={"foo": "me"}, is_open=True)

		createXml = """<osm>
			 <node changeset="{}" lat="51.0" lon="2.2">
			   <tag k="note" v="Just a node"/>
			 </node>
			</osm>""".format(cs.objId)
		response = self.client.put(reverse('elements:create', args=['node']), createXml, content_type='text/xml')
		if response.status_code != 200:
			print (response.content)

		self.assertEqual(response.status_code, 200)

	def test_create_node_invalid_xml(self):
		cs = CreateTestChangeset(self.user, tags={"foo": "me"}, is_open=True)

		createXml = """<osm>
			 <node changeset="{}" lat="51.0" lon="2.2">
			   <tag k="note" v="Just a node"/>
			</osm>""".format(cs.objId)
		response = self.client.put(reverse('elements:create', args=['node']), createXml, content_type='text/xml')

		self.assertEqual(response.status_code, 400)

	def create_xml(self, cs):
		return """<osm>
			 <node changeset="{}" lat="51.5" lon="2.25">
			   <tag k="note" v="Just a node"/>
			 </node>
			</osm>""".format(cs.objId)

	def test_create_node_by_post(self):
		cs = CreateTestChangeset(self.user, tags={"foo": "me"}, is_open=True)

		# The current form: POST to the plural collection, which answers with the new ID
		response = self.client.post("/api/0.6/nodes", self.create_xml(cs), content_type='text/xml')
		self.assertEqual(response.status_code, 200, response.content)
		self.assertEqual(response["Content-Type"], "text/plain")
		newId = int(response.content)
		self.assertGreater(newId, 0)

		# The deprecated form still works and answers the same way
		response = self.client.put(reverse('elements:create', args=['node']), self.create_xml(cs), content_type='text/xml')
		self.assertEqual(response.status_code, 200, response.content)
		secondId = int(response.content)
		self.assertGreater(secondId, 0)
		self.assertNotEqual(secondId, newId)

		# Both nodes exist as uploaded
		for objId in (newId, secondId):
			response = self.client.get("/api/0.6/node/{}".format(objId))
			self.assertEqual(response.status_code, 200)
			data = DecodeOsmdataResponse([response.content])
			self.assertEqual(len(data.nodes), 1)
			self.assertEqual(data.nodes[0].objId, objId)
			self.assertEqual((data.nodes[0].lat, data.nodes[0].lon), (51.5, 2.25))
			self.assertEqual(dict(data.nodes[0].tags), {"note": "Just a node"})
			self.assertEqual(data.nodes[0].metaData.version, 1)

		# Fetching several at once is still a GET on the same path
		response = self.client.get("/api/0.6/nodes?nodes={},{}".format(newId, secondId))
		self.assertEqual(response.status_code, 200)
		both = DecodeOsmdataResponse([response.content])
		self.assertEqual({n.objId for n in both.nodes}, {newId, secondId})

	def test_create_by_post_errors(self):
		cs = CreateTestChangeset(self.user, tags={"foo": "me"}, is_open=True)

		# A node sent to the ways collection, and a body with no object
		response = self.client.post("/api/0.6/ways", self.create_xml(cs), content_type='text/xml')
		self.assertEqual(response.status_code, 400)
		response = self.client.post("/api/0.6/nodes", "<osm></osm>", content_type='text/xml')
		self.assertEqual(response.status_code, 400)
		response = self.client.post("/api/0.6/nodes", "<osm><node", content_type='text/xml')
		self.assertEqual(response.status_code, 400)

		# Creating needs a login
		response = Client().post("/api/0.6/nodes", self.create_xml(cs), content_type='text/xml')
		self.assertIn(response.status_code, (401, 403))
		# Other methods are refused
		self.assertEqual(self.client.put("/api/0.6/nodes", self.create_xml(cs), content_type='text/xml').status_code, 405)

	def test_read_elements_as_json(self):
		node = create_node(self.user.id, self.user.username)
		node2 = create_node(self.user.id, self.user.username, node)
		way = create_way(self.user.id, self.user.username, [node.objId, node2.objId])

		response = self.client.get("/api/0.6/node/{}.json".format(node.objId))
		self.assertEqual(response.status_code, 200)
		self.assertEqual(response["Content-Type"], "application/json")
		doc = json.loads(response.content)
		self.assertEqual(doc["version"], "0.6")
		self.assertIn("generator", doc)
		self.assertEqual(len(doc["elements"]), 1)
		element = doc["elements"][0]
		self.assertEqual((element["type"], element["id"]), ("node", node.objId))
		self.assertAlmostEqual(element["lat"], node.lat, places=7)
		self.assertAlmostEqual(element["lon"], node.lon, places=7)
		self.assertEqual(element["tags"], dict(node.tags))
		self.assertEqual(element["version"], 1)
		self.assertEqual(element["uid"], self.user.id)
		self.assertEqual(element["user"], self.user.username)
		self.assertRegex(element["timestamp"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")

		# The same request with an Accept header, and its XML equivalent
		byHeader = self.client.get("/api/0.6/node/{}".format(node.objId), HTTP_ACCEPT="application/json")
		self.assertEqual(json.loads(byHeader.content), doc)
		xml = DecodeOsmdataResponse([self.client.get("/api/0.6/node/{}".format(node.objId)).content])
		self.assertEqual(xml.nodes[0].objId, element["id"])

		# Each of the element reading calls
		wayUrl = "/api/0.6/way/{}".format(way.objId)
		expected = {
			wayUrl + ".json": {("way", way.objId)},
			wayUrl + "/full.json": {("way", way.objId), ("node", node.objId), ("node", node2.objId)},
			wayUrl + "/history.json": {("way", way.objId)},
			wayUrl + "/1.json": {("way", way.objId)},
			"/api/0.6/node/{}/ways.json".format(node.objId): {("way", way.objId)},
			"/api/0.6/node/{}/relations.json".format(node.objId): set(),
			"/api/0.6/nodes.json?nodes={},{}".format(node.objId, node2.objId):
				{("node", node.objId), ("node", node2.objId)},
		}
		for url in expected:
			response = self.client.get(url)
			self.assertEqual(response.status_code, 200, url)
			self.assertEqual(response["Content-Type"], "application/json", url)
			found = {(e["type"], e["id"]) for e in json.loads(response.content)["elements"]}
			self.assertEqual(found, expected[url], url)

		wayDoc = json.loads(self.client.get(wayUrl + ".json").content)["elements"][0]
		self.assertEqual(wayDoc["nodes"], [node.objId, node2.objId])

		# A missing element is not found in either format
		self.assertEqual(self.client.get("/api/0.6/node/999999999.json").status_code, 404)

	def node_xml(self, cs, objId, version, lat=51.6, lon=2.3, tag="changed"):
		return """<osm>
			 <node id="{}" version="{}" changeset="{}" lat="{}" lon="{}">
			   <tag k="note" v="{}"/>
			 </node>
			</osm>""".format(objId, version, cs.objId, lat, lon, tag)

	def create_test_node(self, cs):
		response = self.client.post("/api/0.6/nodes", self.create_xml(cs), content_type='text/xml')
		self.assertEqual(response.status_code, 200, response.content)
		return int(response.content)

	def read_node(self, objId):
		response = self.client.get("/api/0.6/node/{}".format(objId))
		if response.status_code != 200:
			return None
		return DecodeOsmdataResponse([response.content])

	def test_modify_node(self):
		cs = CreateTestChangeset(self.user, tags={"foo": "me"}, is_open=True)
		objId = self.create_test_node(cs)
		url = "/api/0.6/node/{}".format(objId)

		# The answer is the new version number
		response = self.client.put(url, self.node_xml(cs, objId, 1), content_type='text/xml')
		self.assertEqual(response.status_code, 200, response.content)
		self.assertEqual(response["Content-Type"], "text/plain")
		self.assertEqual(response.content, b"2")

		data = self.read_node(objId)
		self.assertEqual(len(data.nodes), 1)
		node = data.nodes[0]
		self.assertEqual(node.objId, objId)
		self.assertEqual(node.metaData.version, 2)
		self.assertEqual((node.lat, node.lon), (51.6, 2.3))
		self.assertEqual(dict(node.tags), {"note": "changed"})
		self.assertEqual(node.metaData.uid, self.user.id)

		# A second change builds on the first
		response = self.client.put(url, self.node_xml(cs, objId, 2, tag="again"), content_type='text/xml')
		self.assertEqual(response.status_code, 200, response.content)
		self.assertEqual(response.content, b"3")
		again = self.read_node(objId)
		self.assertEqual(dict(again.nodes[0].tags), {"note": "again"})

		# Both versions are in the history, with the original
		history = DecodeOsmdataResponse([self.client.get(url + "/history").content])
		self.assertEqual(sorted(n.metaData.version for n in history.nodes), [1, 2, 3])

	def test_modify_node_refused(self):
		cs = CreateTestChangeset(self.user, tags={"foo": "me"}, is_open=True)
		objId = self.create_test_node(cs)
		url = "/api/0.6/node/{}".format(objId)

		def unchanged():
			# Keep the document alive while its node is in use
			data = self.read_node(objId)
			node = data.nodes[0]
			return node.metaData.version == 1 and dict(node.tags) == {"note": "Just a node"}

		# A version that is not the current one conflicts
		for version in (2, 5):
			response = self.client.put(url, self.node_xml(cs, objId, version), content_type='text/xml')
			self.assertEqual(response.status_code, 409, response.content)
		# The ID in the body must be the one in the URL
		response = self.client.put(url, self.node_xml(cs, objId + 1, 1), content_type='text/xml')
		self.assertEqual(response.status_code, 400, response.content)
		# The body must hold exactly one element, of the right kind, and parse
		for body in ("<osm></osm>", "<osm><way id='{}' version='1' changeset='{}'/></osm>".format(objId, cs.objId), "<osm><node"):
			response = self.client.put(url, body, content_type='text/xml')
			self.assertEqual(response.status_code, 400, response.content)
		# An element that does not exist
		response = self.client.put("/api/0.6/node/999999999", self.node_xml(cs, 999999999, 1), content_type='text/xml')
		self.assertEqual(response.status_code, 404, response.content)
		# A changeset that is closed, or someone else's
		closed = CreateTestChangeset(self.user, tags={"foo": "me"}, is_open=False)
		response = self.client.put(url, self.node_xml(closed, objId, 1), content_type='text/xml')
		self.assertEqual(response.status_code, 409, response.content)
		self.assertIn("The changeset {} was closed at".format(closed.objId), response.content.decode("UTF-8"))
		# Creating in a closed changeset is refused the same way
		response = self.client.post("/api/0.6/nodes", self.create_xml(closed), content_type='text/xml')
		self.assertEqual(response.status_code, 409, response.content)
		other = User.objects.create_user("paul", "paul@beatles.com", "blackbird")
		theirs = CreateTestChangeset(other, tags={"foo": "them"}, is_open=True)
		response = self.client.put(url, self.node_xml(theirs, objId, 1), content_type='text/xml')
		self.assertEqual(response.status_code, 409, response.content)
		# Editing needs a login
		response = Client().put(url, self.node_xml(cs, objId, 1), content_type='text/xml')
		self.assertIn(response.status_code, (401, 403))

		self.assertTrue(unchanged())

	def test_delete_node(self):
		cs = CreateTestChangeset(self.user, tags={"foo": "me"}, is_open=True)
		objId = self.create_test_node(cs)
		url = "/api/0.6/node/{}".format(objId)

		# Refused requests leave the node in place
		response = self.client.delete(url, self.node_xml(cs, objId, 4), content_type='text/xml')
		self.assertEqual(response.status_code, 409, response.content)
		response = self.client.delete(url, self.node_xml(cs, objId + 1, 1), content_type='text/xml')
		self.assertEqual(response.status_code, 400, response.content)
		response = Client().delete(url, self.node_xml(cs, objId, 1), content_type='text/xml')
		self.assertIn(response.status_code, (401, 403))
		self.assertIsNotNone(self.read_node(objId))

		response = self.client.delete(url, self.node_xml(cs, objId, 1), content_type='text/xml')
		self.assertEqual(response.status_code, 200, response.content)
		self.assertEqual(response.content, b"2")

		# It can no longer be read, and its history records the deletion
		self.assertIsNone(self.read_node(objId))
		history = DecodeOsmdataResponse([self.client.get(url + "/history").content])
		versions = {n.metaData.version: n.metaData.visible for n in history.nodes}
		self.assertEqual(versions, {1: True, 2: False})

		# Deleting or changing it again is refused
		response = self.client.delete(url, self.node_xml(cs, objId, 2), content_type='text/xml')
		self.assertIn(response.status_code, (404, 410), response.content)
		response = self.client.put(url, self.node_xml(cs, objId, 2), content_type='text/xml')
		self.assertIn(response.status_code, (404, 410), response.content)

	def test_modify_and_delete_way(self):
		cs = CreateTestChangeset(self.user, tags={"foo": "me"}, is_open=True)
		nodeIds = [self.create_test_node(cs) for _ in range(3)]

		def way_xml(objId, version, refs):
			return "<osm><way id='{}' version='{}' changeset='{}'>{}<tag k='highway' v='path'/></way></osm>".format(
				objId, version, cs.objId, "".join("<nd ref='{}'/>".format(ref) for ref in refs))

		response = self.client.post("/api/0.6/ways", way_xml(-1, 0, nodeIds[:2]), content_type='text/xml')
		self.assertEqual(response.status_code, 200, response.content)
		wayId = int(response.content)
		url = "/api/0.6/way/{}".format(wayId)

		response = self.client.put(url, way_xml(wayId, 1, nodeIds), content_type='text/xml')
		self.assertEqual(response.status_code, 200, response.content)
		self.assertEqual(response.content, b"2")
		wayData = DecodeOsmdataResponse([self.client.get(url).content])
		way = wayData.ways[0]
		self.assertEqual(list(way.refs), nodeIds)
		self.assertEqual(way.metaData.version, 2)

		# A node the way uses cannot be deleted
		nodeUrl = "/api/0.6/node/{}".format(nodeIds[0])
		response = self.client.delete(nodeUrl, self.node_xml(cs, nodeIds[0], 1), content_type='text/xml')
		self.assertEqual(response.status_code, 412, response.content)
		self.assertIsNotNone(self.read_node(nodeIds[0]))

		response = self.client.delete(url, way_xml(wayId, 2, nodeIds), content_type='text/xml')
		self.assertEqual(response.status_code, 200, response.content)
		self.assertEqual(response.content, b"3")
		self.assertEqual(self.client.get(url).status_code, 404)

		# With the way gone its node can be deleted
		response = self.client.delete(nodeUrl, self.node_xml(cs, nodeIds[0], 1), content_type='text/xml')
		self.assertEqual(response.status_code, 200, response.content)

	def tearDown(self):
		u = User.objects.get(username = self.username)
		u.delete()

class ElementsGetParentsTestCase(TestCase):
	def setUp(self):
		self.username = "john"
		self.password = "glass onion"
		self.email = 'jlennon@beatles.com'
		self.user = User.objects.create_user(self.username, self.email, self.password)

	def test_get_ways_for_node(self):
		anonClient = Client()

		node = create_node(self.user.id, self.user.username)
		node2 = create_node(self.user.id, self.user.username, node)
		way = create_way(self.user.id, self.user.username, [node.objId, node2.objId])

		response = anonClient.get(reverse('elements:ways_for_node', args=['node', str(node.objId)]))
		if response.status_code != 200:
			print (response.content)
		self.assertEqual(response.status_code, 200)

		osmData = DecodeOsmdataResponse([response.content])
		wayIdDict = GetOsmDataIndex(osmData)['way']

		self.assertEqual(len(wayIdDict), 1)
		self.assertEqual(way.objId in wayIdDict, True)

	def tearDown(self):

		u = User.objects.get(username = self.username)
		u.delete()

