# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import json
import time
import xml.etree.ElementTree as ET
from urllib.parse import quote

from django.test import TestCase, SimpleTestCase, Client, override_settings
import pgmap

from changeset.views import store_objects_with_bbox_tracking
from pycrocosm.mapdb import get_pgmap
from querymap.tests import DecodeOsmdataResponse
from . import ql
from .ql import (QueryError, UnsupportedFeature, OsmScript, Query, HasKv, BboxQuery, IdQuery,
	ItemFilter, Item, Union, Recurse, Print, RecurseFilter, Around, User, Newer, Changed, Difference,
	CsvFormat)
from .evaluator import Limits, QueryTimeout
from .interpreter import evaluate

NWR = ("node", "way", "relation")

class ParserTestCase(SimpleTestCase):
	"""What queries mean, and which are refused, without touching the database."""

	def statements(self, text):
		return ql.parse(text).statements

	def test_settings(self):
		script = ql.parse('[out:json][timeout:25];node[amenity=post_box](50.6,7.0,50.8,7.3);out;')
		self.assertEqual((script.output, script.timeout, script.maxsize, script.bbox), ("json", 25, None, None))
		script = ql.parse("[timeout:900][maxsize:1073741824];node(51.15,7.0,51.35,7.3);out;")
		self.assertEqual((script.output, script.timeout, script.maxsize), ("xml", 900, 1073741824))
		script = ql.parse("[bbox:50.6,7.0,50.8,7.3]\n[out:xml]\n;\nnode;out;")
		self.assertEqual(script.bbox, (50.6, 7.0, 50.8, 7.3))
		self.assertFalse(script.bbox_from_url)
		script = ql.parse("[bbox];node[amenity=post_box];out;")
		self.assertTrue(script.bbox_from_url)
		self.assertEqual(ql.parse("[bbox:-23,-43.3,-22.8,-43.1];node;out;").bbox, (-23.0, -43.3, -22.8, -43.1))

		for bad in ("[out:json]node;out;", "[timeout:x];node;out;", "[colour:red];node;out;",
			"[out:yaml];node;out;", "[timeout:1][timeout:2];node;out;", "[bbox:1,2,3];node;out;",
			"[bbox:51,7,50,8];node;out;", "[bbox:50,7,51,200];node;out;", "node;[out:json];out;"):
			with self.assertRaises(QueryError, msg=bad):
				ql.parse(bad)

	def test_query_filters(self):
		self.assertEqual(self.statements('node["amenity"="post_box"](50.6,7.0,50.8,7.3);'), [
			Query(("node",), [HasKv(HasKv.EQUALS, "amenity", "post_box"), BboxQuery(50.6, 7.0, 50.8, 7.3)])])
		self.assertEqual(self.statements("nwr[amenity=restaurant];"),
			[Query(NWR, [HasKv(HasKv.EQUALS, "amenity", "restaurant")])])
		for word, types in (("node", ("node",)), ("way", ("way",)), ("rel", ("relation",)),
			("relation", ("relation",)), ("nwr", NWR), ("nw", ("node", "way")),
			("nr", ("node", "relation")), ("wr", ("way", "relation"))):
			self.assertEqual(self.statements(word + "[name];"), [Query(types, [HasKv(HasKv.EXISTS, "name")])])

		self.assertEqual(self.statements(
			'way[highway][!foot]["name"!="A \\"B\\""][ref~"^E[0-9]+$"][note!~\'x\', i][admin_level=2];')[0].filters, [
			HasKv(HasKv.EXISTS, "highway"),
			HasKv(HasKv.NOT_EXISTS, "foot"),
			HasKv(HasKv.NOT_EQUALS, "name", 'A "B"'),
			HasKv(HasKv.MATCHES, "ref", "^E[0-9]+$"),
			HasKv(HasKv.NOT_MATCHES, "note", "x", True),
			HasKv(HasKv.EQUALS, "admin_level", "2"),
		])
		self.assertEqual(self.statements('node["addr:housenumber"]["name"="M\\u00fcngstener Stra\\u00dfe"];')[0].filters,
			[HasKv(HasKv.EXISTS, "addr:housenumber"), HasKv(HasKv.EQUALS, "name", "Müngstener Straße")])
		self.assertEqual(self.statements("node(3470507586);"), [Query(("node",), [IdQuery([3470507586])])])
		self.assertEqual(self.statements("relation(id:108619,2306823);"),
			[Query(("relation",), [IdQuery([108619, 2306823])])])
		self.assertEqual(self.statements("way._[highway=motorway];"),
			[Query(("way",), [ItemFilter("_"), HasKv(HasKv.EQUALS, "highway", "motorway")])])
		self.assertEqual(self.statements("node.a.b->.c;"),
			[Query(("node",), [ItemFilter("a"), ItemFilter("b")], "c")])
		self.assertEqual(self.statements("node;"), [Query(("node",), [])])

	def test_statements(self):
		# The map call from the Overpass API wiki page
		self.assertEqual(ql.parse("(node(51.249,7.148,51.251,7.152);\n<;\n);\nout meta;"), OsmScript([
			Union([Query(("node",), [BboxQuery(51.249, 7.148, 51.251, 7.152)]), Recurse("up")]),
			Print(mode="meta")]))
		self.assertEqual(self.statements("(._;>;);"), [Union([Item("_"), Recurse("down")])])
		self.assertEqual(self.statements('(node[name="Foo"]; way[name="Foo"];)->.a;'), [Union([
			Query(("node",), [HasKv(HasKv.EQUALS, "name", "Foo")]),
			Query(("way",), [HasKv(HasKv.EQUALS, "name", "Foo")])], "a")])
		self.assertEqual(self.statements("<; <<; >; >>; .a <; < ->.b; .a << ->.b; .a >> ->.b;"), [
			Recurse("up"), Recurse("up-rel"), Recurse("down"), Recurse("down-rel"),
			Recurse("up", "a"), Recurse("up", "_", "b"), Recurse("up-rel", "a", "b"),
			Recurse("down-rel", "a", "b")])
		self.assertEqual(self.statements(".a; .a->.b; ((.a;); .b;);"),
			[Item("a"), Item("a", "b"), Union([Union([Item("a")]), Item("b")])])
		self.assertEqual(self.statements("way[name=x]->.foo; .foo out body;"),
			[Query(("way",), [HasKv(HasKv.EQUALS, "name", "x")], "foo"), Print("foo")])

	def test_linked_spatial_and_meta_filters(self):
		self.assertEqual(self.statements("rel[ref=E61]; node(r); way(r.a:\"outer\"); way(bn); rel(bw.x:stop); node(w); rel(br.y);")[1:], [
			Query(("node",), [RecurseFilter("r")]),
			Query(("way",), [RecurseFilter("r", "a", "outer")]),
			Query(("way",), [RecurseFilter("bn")]),
			Query(("relation",), [RecurseFilter("bw", "x", "stop")]),
			Query(("node",), [RecurseFilter("w")]),
			Query(("relation",), [RecurseFilter("br", "y")])])
		self.assertEqual(self.statements('node(w.w1)(w.w2); nwr(r.relation:"");'), [
			Query(("node",), [RecurseFilter("w", "w1"), RecurseFilter("w", "w2")]),
			Query(NWR, [RecurseFilter("r", "relation", "")])])

		self.assertEqual(self.statements("node(around:10); node(around.stops:100)[highway=bus_stop]; nwr(around:500.5,53.34,-6.25);"), [
			Query(("node",), [Around(10.0)]),
			Query(("node",), [Around(100.0, "stops"), HasKv(HasKv.EQUALS, "highway", "bus_stop")]),
			Query(NWR, [Around(500.5, "_", 53.34, -6.25)])])

		self.assertEqual(self.statements('nwr(user:"Roland Olbricht", bob); node(uid:65282,7)(1,2,3,4);'), [
			Query(NWR, [User(names=["Roland Olbricht", "bob"])]),
			Query(("node",), [User(uids=[65282, 7]), BboxQuery(1.0, 2.0, 3.0, 4.0)])])
		self.assertEqual(self.statements('node(newer:"2026-01-01T00:00:00Z"); '
			'way(changed:"2026-01-01T00:00:00Z"); rel(changed:"2026-01-01T00:00:00Z","2026-02-01T00:00:00+01:00");'), [
			Query(("node",), [Newer(1767225600)]),
			Query(("way",), [Changed(1767225600)]),
			Query(("relation",), [Changed(1767225600, 1769900400)])])

		for bad in ("node(around:-1);", "node(around:10,91,0);", "node(around);", "node(around:10,1);",
			"node(uid:x);", "node(user:);", "node(newer:2026);", 'node(newer:"last week");',
			'node(changed:"2026-01-01","x");', "node(w:);", "node(r.);"):
			with self.assertRaises(QueryError, msg=bad) as caught:
				ql.parse(bad)
			self.assertNotIsInstance(caught.exception, UnsupportedFeature, bad)

	def test_difference(self):
		self.assertEqual(self.statements("(node[name=Foo]; - node(50.0,7.0,51.0,8.0);)->.a;"), [Difference(
			Query(("node",), [HasKv(HasKv.EQUALS, "name", "Foo")]),
			Query(("node",), [BboxQuery(50.0, 7.0, 51.0, 8.0)]), "a")])
		self.assertEqual(self.statements("(.banks; - .near;);"), [Difference(Item("banks"), Item("near"))])
		self.assertEqual(self.statements("((.a; .b;); - (.c; - .d;););"),
			[Difference(Union([Item("a"), Item("b")]), Difference(Item("c"), Item("d")))])
		for bad in ("(- .a;);", "(.a; - .b; .c;);", "(.a; - .b; - .c;);", "(.a; .b; - .c;);", "(.a; -);"):
			with self.assertRaises(QueryError, msg=bad):
				ql.parse(bad)

	def test_csv_setting(self):
		script = ql.parse('[out:csv(::id,::type,"name")];node[a];out;')
		self.assertEqual((script.output, script.csv), ("csv", CsvFormat(["::id", "::type", "name"])))
		self.assertEqual(ql.parse('[out:csv( ::"id", amenity, "contact:phone", ::"count:nodes", ::count:ways, ::count ; '
			'false ; "|" )][timeout:5];node[a];out;').csv,
			CsvFormat(["::id", "amenity", "contact:phone", "::count:nodes", "::count:ways", "::count"], False, "|"))
		self.assertEqual(ql.parse("[out:csv(name;true)];node[a];out;").csv, CsvFormat(["name"], True, "\t"))
		for bad in ("[out:csv];node[a];out;", "[out:csv()];node[a];out;", "[out:csv(::colour)];node[a];out;",
			"[out:csv(name;yes)];node[a];out;", "[out:csv(name;true;|)];node[a];out;"):
			with self.assertRaises(QueryError, msg=bad):
				ql.parse(bad)

	def test_out(self):
		self.assertEqual(self.statements("out;"), [Print()])
		self.assertEqual(self.statements("out;"), self.statements("._ out body asc;"))
		self.assertEqual(self.statements("out geom; out center; out meta bb; out skel qt; out ids 12; out tags; out count;"), [
			Print(geometry="geom"), Print(geometry="center"), Print(mode="meta", geometry="bb"),
			Print(mode="skel", order="qt"), Print(mode="ids", limit=12), Print(mode="tags"),
			Print(mode="count")])
		self.assertEqual(self.statements("out qt 5 geom meta;"),
			[Print(mode="meta", geometry="geom", order="qt", limit=5)])
		for bad in ("out body meta;", "out geom center;", "out sideways;", "out count geom;",
			"out 1 2;", "out", "(node[a]; out;);"):
			with self.assertRaises(QueryError, msg=bad):
				ql.parse(bad)

	def test_comments_and_layout(self):
		text = """/*
		This query looks for nodes, ways and relations
		with the given key/value combination. */
		[out:json][timeout:25];
		// gather results
		nwr["amenity"="post_box"](1,2,3,4); // a // b
		// print results
		out geom;"""
		script = ql.parse(text)
		self.assertEqual(script.statements, [
			Query(NWR, [HasKv(HasKv.EQUALS, "amenity", "post_box"), BboxQuery(1.0, 2.0, 3.0, 4.0)]),
			Print(geometry="geom")])
		self.assertEqual(ql.parse("node [ name = 'a//b' ] ( 1 , 2 , 3 , 4 ) -> . x ;").statements,
			[Query(("node",), [HasKv(HasKv.EQUALS, "name", "a//b"), BboxQuery(1.0, 2.0, 3.0, 4.0)], "x")])

	def test_syntax_errors(self):
		for bad, fragment in (("", "no statements"), ("  // nothing\n", "no statements"),
			("node[name", "expected"), ("node[name=];", "tag value"), ("node[name=a]", 'expected ";"'),
			("node(1,2,3);", "one ID or the four"), ("node(1,2,3,4,5);", "one ID or the four"),
			("node(51,7,50,8);", "south is greater"), ("node(1.5);", 'expected ")"'),
			("fetch[name];", 'unknown statement "fetch"'), ("node(near:5);", 'unknown filter "near"'),
			("(node[a];", "union is not closed"), ("();", "union is empty"),
			('node[name="abc];', "string is not closed"), ("/* never ends", "comment is not closed"),
			("node[name~a,x];", 'only option'), ("node[a]->b;", 'expected "."'), ("};", "expected a statement")):
			with self.assertRaises(QueryError, msg=bad) as caught:
				ql.parse(bad)
			self.assertIn(fragment, str(caught.exception), bad)
			self.assertNotIsInstance(caught.exception, UnsupportedFeature, bad)
		with self.assertRaises(QueryError) as caught:
			ql.parse("node[a];\nout;\n\nnode[b](1,2);")
		self.assertTrue(str(caught.exception).startswith("line 4:"), str(caught.exception))

	def test_unsupported_features_are_named(self):
		cases = (
			('area[name="Troisdorf"]; way(area)[highway][name]; out;', "area queries"),
			("node(1,2,3,4)(if:count_tags() > 0); out;", "if: filters"),
			("way[name=Foo]; foreach { (._; >;); out; }", "foreach loops"),
			("node[a]; for (t[\"name\"]) { out; }", "for loops"),
			("node[a]; if (count(nodes) > 0) { out; }", "if statements"),
			("node(1); complete(100) { nwr[amenity=pub](around:500); }; out;", "complete loops"),
			("way[a]; node(w:1); out;", "by position"),
			("node(around:100,1,2,3,4); out;", "along a line"),
			('nwr(user_touched:"x"); out;', "user_touched filters"),
			("node(poly:\"1 2 3 4 5 6\"); out;", "poly filters"),
			("node(area.a); out;", "area filters"),
			("node(way_link:1); out;", "way_link filters"),
			("[out:custom];node[a];out;", "out:custom output"),
			("node[a]; make stat number=count(ways); out;", "make statements"),
			("node[a]; convert rel ::id = id(); out;", "convert statements"),
			("is_in(50.7,7.2); out;", "is_in"),
			(".a is_in->.b; out;", "is_in"),
			("wr[leisure=golf_course]; map_to_area ->.golf; out;", "map_to_area"),
			("timeline(relation,2632934); out;", "timeline statements"),
			("derived[name=x]; out;", "derived elements"),
			('[date:"2014-05-06T00:00:00Z"];node(1,2,3,4);out;', "the date setting"),
			('[diff:"2012-09-14T15:00:00Z"];node(1,2,3,4);out;', "the diff setting"),
			('nwr[~"^name"~"x"];out;', "regular expression for the key"),
			("node[a]; out geom(1,2,3,4);", "bounding boxes on out"),
			("node[a]; out noids;", "noids"),
			("node(10,170,11,-170); out;", "antimeridian"),
			('<query type="node"><has-kv k="name" v="Bristol"/></query><print/>', "Overpass XML"),
			('<osm-script timeout="900"><bbox-query s="1" w="2" n="3" e="4"/><print/></osm-script>', "Overpass XML"),
		)
		for text, feature in cases:
			with self.assertRaises(UnsupportedFeature, msg=text) as caught:
				ql.parse(text)
			self.assertIn(feature, str(caught.exception), text)
			self.assertIn("not supported by this server", str(caught.exception))
			self.assertEqual(caught.exception.status, 400)

		# Overpass Turbo expands its own shortcuts; unexpanded they are not QL
		with self.assertRaises(QueryError) as caught:
			ql.parse('nwr["amenity"="post_box"]({{bbox}});\nout geom;')
		self.assertIn("Overpass Turbo shortcut", str(caught.exception))
		# A query that starts with recursion is not mistaken for XML
		self.assertEqual(self.statements("<; out;"), [Recurse("up"), Print()])


BBOX = "10,20,10.5,20.5"

@override_settings(OVERPASS_AREA_MAXIMUM=1.0, OVERPASS_RATE_LIMIT_REQUESTS=0)
class InterpreterTestCase(TestCase):
	"""Queries run against a small map:

	n1 pub "The Crown", n2 cafe, n3 untagged, n4 pub "the" in the test bbox; n5 pub far away
	w1 = n1,n2,n3 (primary); w2 = n3,n4 (residential)
	r1 = n1 as "stop" and w1 as "route"; r2 = r1 and w2
	"""

	def setUp(self):
		# Unless a test says otherwise, the map is treated as not storing a box
		# for each way and relation, whatever the test database's own setting.
		from unittest.mock import patch
		from .evaluator import MapSource
		patcher = patch.object(MapSource, "stores_bboxes", return_value=False)
		patcher.start()
		self.addCleanup(patcher.stop)
		self.n1 = self.create_node(10.1, 20.1, amenity="pub", name="The Crown")
		self.n2 = self.create_node(10.2, 20.2, amenity="cafe", name="Café Été", **{"addr:housenumber": "12"})
		self.n3 = self.create_node(10.3, 20.3)
		self.n4 = self.create_node(10.4, 20.4, amenity="pub", name="the")
		self.n5 = self.create_node(12.0, 22.0, amenity="pub", website="http://example.com/pub?a=1")
		self.w1 = self.create_way([self.n1, self.n2, self.n3], highway="primary", name="High Street")
		self.w2 = self.create_way([self.n3, self.n4], highway="residential")
		self.r1 = self.create_relation([("node", self.n1, "stop"), ("way", self.w1, "route")],
			type="route", ref="E61")
		self.r2 = self.create_relation([("relation", self.r1, ""), ("way", self.w2, "")], type="network")
		self.nodes = set(("node", i) for i in (self.n1, self.n2, self.n3, self.n4, self.n5))

	def tearDown(self):
		errStr = pgmap.PgMapError()
		t = get_pgmap().GetTransaction("EXCLUSIVE")
		ok = t.ResetActiveTables(errStr)
		if not ok:
			print(errStr.errStr)
		t.Commit()

	# *** Building the map ***

	def store(self, obj, tags):
		# Tags named _uid, _user and _timestamp set who made the edit, and when
		tags = dict(tags)
		obj.objId = -1
		obj.metaData.version = 1
		obj.metaData.timestamp = tags.pop("_timestamp", 1700000000)
		obj.metaData.changeset = 1000
		obj.metaData.uid = tags.pop("_uid", 7)
		obj.metaData.username = tags.pop("_user", "mapper")
		obj.metaData.visible = True
		for key, value in tags.items():
			obj.tags[key] = value
		data = pgmap.OsmData()
		created = [pgmap.mapi64i64() for i in range(3)]
		if isinstance(obj, pgmap.OsmNode):
			data.nodes.append(obj)
			ids = created[0]
		elif isinstance(obj, pgmap.OsmWay):
			data.ways.append(obj)
			ids = created[1]
		else:
			data.relations.append(obj)
			ids = created[2]
		t = get_pgmap().GetTransaction("EXCLUSIVE")
		ok, diffs, affectedParents, errStr = store_objects_with_bbox_tracking("create", data, t, *created)
		if not ok:
			t.Abort()
			self.fail(errStr.errStr)
		t.Commit()
		return ids[-1]

	def create_node(self, lat, lon, **tags):
		node = pgmap.OsmNode()
		node.lat, node.lon = lat, lon
		return self.store(node, tags)

	def create_way(self, refs, **tags):
		way = pgmap.OsmWay()
		for ref in refs:
			way.refs.append(ref)
		return self.store(way, tags)

	def create_relation(self, members, **tags):
		relation = pgmap.OsmRelation()
		for kind, ref, role in members:
			relation.AddMember(kind, ref, role)
		return self.store(relation, tags)

	# *** Asking ***

	def ask(self, query, **params):
		params["data"] = query
		return Client().post("/api/interpreter", params)

	def elements(self, query, **params):
		response = self.ask("[out:json];" + query, **params)
		self.assertEqual(response.status_code, 200, response.content)
		return json.loads(response.content)["elements"]

	def found(self, query, **params):
		"""The elements a query outputs, as a set of (type, id)."""
		return set((e["type"], e["id"]) for e in self.elements(query, **params))

	def refused(self, query, fragment, status=400, **params):
		response = self.ask(query, **params)
		self.assertEqual(response.status_code, status, response.content)
		self.assertEqual(response["Content-Type"], "text/plain; charset=utf-8")
		self.assertIn(fragment, response.content.decode("utf-8"))
		return response

	def n(self, *ids):
		return set(("node", i) for i in ids)

	def w(self, *ids):
		return set(("way", i) for i in ids)

	def r(self, *ids):
		return set(("relation", i) for i in ids)

	# *** Tests ***

	def test_tag_filters(self):
		box = "({})".format(BBOX)
		self.assertEqual(self.found("node[amenity=pub]{};out;".format(box)), self.n(self.n1, self.n4))
		self.assertEqual(self.found('node["amenity"="pub"]{};out;'.format(box)), self.n(self.n1, self.n4))
		self.assertEqual(self.found("node[amenity]{};out;".format(box)), self.n(self.n1, self.n2, self.n4))
		self.assertEqual(self.found("node[!amenity]{};out;".format(box)), self.n(self.n3))
		# The negative value filters also select elements without the key
		self.assertEqual(self.found("node[amenity!=pub]{};out;".format(box)), self.n(self.n2, self.n3))
		self.assertEqual(self.found('node[name!~"Crown"]{};out;'.format(box)), self.n(self.n2, self.n3, self.n4))
		self.assertEqual(self.found('node[name~"^the"]{};out;'.format(box)), self.n(self.n4))
		self.assertEqual(self.found('node[name~"^the",i]{};out;'.format(box)), self.n(self.n1, self.n4))
		self.assertEqual(self.found('node[name~"e$"]{};out;'.format(box)), self.n(self.n4))
		self.assertEqual(self.found('node[name~"^(The Crown|the)$"][amenity=pub][!shop]{};out;'.format(box)),
			self.n(self.n1, self.n4))
		self.assertEqual(self.found("node[amenity=pub][amenity=cafe]{};out;".format(box)), set())
		self.assertEqual(self.found("node{};out;".format(box)), self.n(self.n1, self.n2, self.n3, self.n4))

	def test_exact_tags_need_no_bbox(self):
		# Limited to this test's nodes: the test database may hold other data
		def mine(query):
			return self.found(query) & self.nodes
		self.assertEqual(mine("node[amenity=pub];out;"), self.n(self.n1, self.n4, self.n5))
		self.assertEqual(mine("node[amenity];out;"), self.n(self.n1, self.n2, self.n4, self.n5))
		self.assertEqual(mine('node[amenity=pub][name~"Crown"];out;'), self.n(self.n1))
		# Text the tag index treats specially: a common word, punctuation, accents, a URL
		self.assertEqual(mine("node[name=the];out;"), self.n(self.n4))
		self.assertEqual(mine('node[name="The Crown"];out;'), self.n(self.n1))
		self.assertEqual(mine('node["addr:housenumber"];out;'), self.n(self.n2))
		self.assertEqual(mine('node["addr:housenumber"=12];out;'), self.n(self.n2))
		self.assertEqual(mine('node[name="Café Été"];out;'), self.n(self.n2))
		self.assertEqual(mine('node[name="Cafe Ete"];out;'), set())
		self.assertEqual(mine('node[website="http://example.com/pub?a=1"];out;'), self.n(self.n5))
		self.assertEqual(mine('node[website="http://example.com/pub"];out;'), set())
		self.assertEqual(mine("node[name=The];out;"), set())
		self.assertEqual(self.found('way[name="High Street"];out;') & self.w(self.w1, self.w2), self.w(self.w1))

	def test_queries_that_would_search_everything_are_refused(self):
		for query in ("node[amenity!=pub];out;", "node[!amenity];out;", 'nwr[name~"^The"];out;',
			'way[name!~"x"];out;', "node;out;"):
			self.refused(query, "needs a bounding box")
		self.refused("node(0,0,50,50);out;", "too large to search")
		self.refused("[bbox:0,0,50,50];node[amenity!=pub];out;", "too large to search")
		# An exact tag makes any area searchable
		self.assertEqual(self.found("node[amenity=pub](0,0,50,50);out;") & self.nodes,
			self.n(self.n1, self.n4, self.n5))

	def test_types_ids_and_sets(self):
		everything = self.n(self.n1, self.n2, self.n3, self.n4) | self.w(self.w1, self.w2) | self.r(self.r1, self.r2)
		self.assertEqual(self.found("nwr({});out;".format(BBOX)), everything)
		self.assertEqual(self.found("wr({});out;".format(BBOX)), self.w(self.w1, self.w2) | self.r(self.r1, self.r2))
		self.assertEqual(self.found("way[highway=primary]({});out;".format(BBOX)), self.w(self.w1))
		self.assertEqual(self.found("rel[type=route];out;") & self.r(self.r1, self.r2), self.r(self.r1))

		self.assertEqual(self.found("node({});out;".format(self.n5)), self.n(self.n5))
		self.assertEqual(self.found("node(id:{},{},999999999999);out;".format(self.n1, self.n3)),
			self.n(self.n1, self.n3))
		self.assertEqual(self.found("node(id:{},{})[amenity];out;".format(self.n1, self.n3)), self.n(self.n1))
		# One ID asked of several types finds whichever of them has it
		self.assertIn(("node", self.n1), self.found("nwr({});out;".format(self.n1)))
		self.assertEqual(set(i for kind, i in self.found("nwr({});out;".format(self.n1))), {self.n1})

		# Named sets, and filtering a set further
		query = ("node[amenity=pub]({0})->.pubs; node[name]({0})->.named; way({0})->.ways;".format(BBOX))
		self.assertEqual(self.found(query + ".pubs out;"), self.n(self.n1, self.n4))
		self.assertEqual(self.found(query + ".named out;"), self.n(self.n1, self.n2, self.n4))
		self.assertEqual(self.found(query + "out;"), set())
		self.assertEqual(self.found(query + 'node.named[amenity=cafe];out;'), self.n(self.n2))
		self.assertEqual(self.found(query + 'node.named.pubs;out;'), self.n(self.n1, self.n4))
		self.assertEqual(self.found(query + 'node.named.pubs[name!=the];out;'), self.n(self.n1))
		self.assertEqual(self.found(query + 'nwr.ways;out;'), self.w(self.w1, self.w2))
		self.assertEqual(self.found(query + 'node.ways;out;'), set())
		self.assertEqual(self.found(query + '.pubs->.x; .x out;'), self.n(self.n1, self.n4))
		self.assertEqual(self.found(query + 'node.pubs({});out;'.format(self.n4)), self.n(self.n4))
		self.assertEqual(self.found(query + '(.pubs; .ways;); out;'), self.n(self.n1, self.n4) | self.w(self.w1, self.w2))
		self.assertEqual(self.found(query + '(.pubs; .ways;)->.both; out; .both out;'),
			self.n(self.n1, self.n4) | self.w(self.w1, self.w2))
		# A later result replaces the default set
		self.assertEqual(self.found("node[amenity=pub]({0}); way({0}); out;".format(BBOX)), self.w(self.w1, self.w2))
		self.assertEqual(self.found("way[highway=primary]({0}); way._[name]; out;".format(BBOX)), self.w(self.w1))
		self.assertEqual(self.found("way[highway=residential]({0}); way._[name]; out;".format(BBOX)), set())

	def test_bounding_boxes(self):
		self.assertEqual(self.found("node(10.15,20.15,10.35,20.35);out;"), self.n(self.n2, self.n3))
		# The global bbox limits every query, and combines with one of its own
		response = self.ask("[out:json][bbox:10.15,20.15,10.35,20.35];node[amenity];out;")
		self.assertEqual([e["id"] for e in json.loads(response.content)["elements"]], [self.n2])
		response = self.ask("[out:json][bbox:10.15,20.15,10.45,20.45];node[amenity];out;")
		self.assertEqual(set(e["id"] for e in json.loads(response.content)["elements"]), {self.n2, self.n4})
		response = self.ask("[out:json][bbox:10.15,20.15,10.45,20.45];node(10,20,10.35,20.35);out;")
		self.assertEqual(set(e["id"] for e in json.loads(response.content)["elements"]), {self.n2, self.n3})
		response = self.ask("[out:json][bbox:10.15,20.15,10.25,20.25];node(10.35,20.35,10.45,20.45);out;")
		self.assertEqual(json.loads(response.content)["elements"], [])
		# The separate bbox parameter is west,south,east,north
		response = self.ask("[out:json][bbox];node[amenity];out;", bbox="20.15,10.15,20.35,10.35")
		self.assertEqual([e["id"] for e in json.loads(response.content)["elements"]], [self.n2])
		self.refused("[bbox];node[amenity];out;", "no bbox parameter")
		self.refused("[bbox];node[amenity];out;", "west,south,east,north", bbox="1,2,3")
		# A way or relation is in a box if one of its nodes is, however far the rest reaches
		self.assertEqual(self.found("way(10.15,20.15,10.25,20.25);out;"), self.w(self.w1))
		self.assertEqual(self.found("way(10.25,20.25,10.35,20.35);out;"), self.w(self.w1, self.w2))
		self.assertEqual(self.found("way(10.24,20.14,10.26,20.16);out;"), set())
		self.assertEqual(self.found("rel(10.35,20.35,10.45,20.45);out;"), self.r(self.r2))
		self.assertEqual(self.found("rel(10.05,20.05,10.15,20.15);out;"), self.r(self.r1))
		self.assertEqual(self.found("rel[type=route](10.15,20.15,10.25,20.25);out;"), self.r(self.r1))
		self.assertEqual(self.found("rel[type=route](10.35,20.35,10.45,20.45);out;"), set())
		self.assertEqual(self.found("way[highway=primary](10.35,20.35,10.45,20.45);out;"), set())
		self.assertEqual(self.found("way[highway][highway!=primary](10.25,20.25,10.35,20.35);out;"), self.w(self.w2))
		self.assertEqual(self.found("way(id:{})(10.25,20.25,10.35,20.35);out;".format(self.w2)), self.w(self.w2))
		# An area too large to start from: the exact tag is searched for first
		self.assertEqual(self.found("way[highway=primary](9,19,13,23);out;") & self.w(self.w1, self.w2), self.w(self.w1))
		self.assertEqual(self.found("rel[type=network](9,19,13,23);out;") & self.r(self.r1, self.r2), self.r(self.r2))
		self.assertEqual(self.found("way[highway=primary](11,21,13,23);out;"), set())
		self.assertEqual(self.found("rel[type=network](11,21,13,23);out;"), set())
		# The same when the box holds more nodes than a query may select
		# (a tag no other way has, so that few ways are found whatever else the map holds)
		w3 = self.create_way([self.n1, self.n2], overpass_test="only way")
		with self.settings(OVERPASS_ELEMENTS_MAXIMUM=1):
			self.assertEqual(self.found('way[overpass_test="only way"]({});out;'.format(BBOX)), self.w(w3))
			self.assertEqual(self.found('way[overpass_test="only way"](10.25,20.25,10.5,20.5);out;'), set())
			self.refused('way[overpass_test!="only way"]({});out;'.format(BBOX), "more than 1 elements")
		# With too many matches to check each against the box, the search starts from the box
		from . import evaluator
		original = evaluator.TAG_FIRST_MAXIMUM
		evaluator.TAG_FIRST_MAXIMUM = 0
		try:
			self.assertEqual(self.found("way[highway=primary](10.15,20.15,10.25,20.25);out;"), self.w(self.w1))
			self.assertEqual(self.found("rel[type=route](10.15,20.15,10.25,20.25);out;"), self.r(self.r1))
			self.assertEqual(self.found("wr[highway=primary](10.35,20.35,10.45,20.45);out;"), set())
			self.refused("way[highway=primary](9,19,13,23);out;", "more than")
		finally:
			evaluator.TAG_FIRST_MAXIMUM = original
		# With many nodes in the box, the search by tag comes first; the answers are the same
		original = evaluator.REGION_FIRST_MAXIMUM
		evaluator.REGION_FIRST_MAXIMUM = 0
		try:
			self.assertEqual(self.found("way[highway=primary](10.15,20.15,10.25,20.25);out;"), self.w(self.w1))
			self.assertEqual(self.found("way[highway](10.25,20.25,10.35,20.35);out;"), self.w(self.w1, self.w2))
			self.assertEqual(self.found("way[highway=primary](10.35,20.35,10.45,20.45);out;"), set())
			self.assertEqual(self.found("rel[type=route](10.15,20.15,10.25,20.25);out;"), self.r(self.r1))
			self.assertEqual(self.found("rel[type=network](10.35,20.35,10.45,20.45);out;"), self.r(self.r2))
			self.assertEqual(self.found("rel[type=route](10.35,20.35,10.45,20.45);out;"), set())
			self.assertEqual(self.found("way[highway](around:100,10.3,20.3);out;"), self.w(self.w1, self.w2))
			self.assertEqual(self.found("way[highway=residential](around:100,10.1,20.1);out;"), set())
			self.assertEqual(self.found("rel[type](around:100,10.4,20.4);out;"), self.r(self.r2))
			self.assertEqual(self.found("way(id:{})(around:100,10.4,20.4);out;".format(self.w2)), self.w(self.w2))
		finally:
			evaluator.REGION_FIRST_MAXIMUM = original

	def test_bounding_boxes_on_a_map_that_stores_them(self):
		from unittest.mock import patch
		from .evaluator import MapSource
		self.assertIn(get_pgmap().GetTransaction("ACCESS SHARE").UseBboxInQuery(), (True, False))
		calls = []
		real_ways_of_nodes = MapSource.ways_of_nodes

		def counting(source, ids):
			calls.append(len(ids))
			return real_ways_of_nodes(source, ids)

		with patch.object(MapSource, "stores_bboxes", return_value=True), \
			patch.object(MapSource, "ways_of_nodes", counting):
			# Ways and relations are found by their own boxes, without going through nodes
			self.assertEqual(self.found("way(10.15,20.15,10.25,20.25);out;"), self.w(self.w1))
			self.assertEqual(self.found("way(10.25,20.25,10.35,20.35);out;"), self.w(self.w1, self.w2))
			self.assertEqual(self.found("way[highway=primary](10.25,20.25,10.35,20.35);out;"), self.w(self.w1))
			self.assertEqual(self.found("way[highway!=primary](10.25,20.25,10.35,20.35);out;"), self.w(self.w2))
			self.assertEqual(self.found("way[highway=primary](10.35,20.35,10.45,20.45);out;"), set())
			self.assertEqual(self.found("way(id:{})(10.25,20.25,10.35,20.35);out;".format(self.w2)), self.w(self.w2))
			self.assertEqual(self.found("rel(10.35,20.35,10.45,20.45);out;"), self.r(self.r2))
			self.assertEqual(self.found("rel[type=route](10.15,20.15,10.25,20.25);out;"), self.r(self.r1))
			self.assertEqual(self.found("rel[type=route](10.35,20.35,10.45,20.45);out;"), set())
			self.assertEqual(self.found("nwr({});out;".format(BBOX)), self.n(self.n1, self.n2, self.n3, self.n4) |
				self.w(self.w1, self.w2) | self.r(self.r1, self.r2))
			self.assertEqual(self.found("way[highway=primary](9,19,13,23);out;") & self.w(self.w1, self.w2), self.w(self.w1))
			response = self.ask("[out:json][bbox:10.35,20.35,10.45,20.45];wr[type];out ids;")
			self.assertEqual(json.loads(response.content)["elements"], [{"type": "relation", "id": self.r2}])
			# A way whose box overlaps the area is found even with no node in it
			self.assertEqual(self.found("way(10.24,20.14,10.26,20.16);out;"), self.w(self.w1))
			self.assertEqual(calls, [])
			# The rules for what may be searched are unchanged
			self.refused("way(0,0,50,50);out;", "too large to search")
			self.refused("way[highway!=primary];out;", "needs a bounding box")
			# Distances are still measured from nodes
			self.assertEqual(self.found("way(around:100,10.3,20.3);out;"), self.w(self.w1, self.w2))
			self.assertGreater(len(calls), 0)
		# Without stored boxes the same way is not found
		with patch.object(MapSource, "stores_bboxes", return_value=False):
			self.assertEqual(self.found("way(10.24,20.14,10.26,20.16);out;"), set())

	def test_recursion(self):
		def after(first, second):
			return self.found(first + second + "out;")
		way = "way({});".format(self.w1)
		self.assertEqual(after(way, ">;"), self.n(self.n1, self.n2, self.n3))
		self.assertEqual(after(way, "(._;>;);"), self.n(self.n1, self.n2, self.n3) | self.w(self.w1))
		self.assertEqual(after(way, ">>;"), self.n(self.n1, self.n2, self.n3))

		route = "rel({});".format(self.r1)
		members = self.n(self.n1, self.n2, self.n3) | self.w(self.w1)
		self.assertEqual(after(route, ">;"), members)
		network = "rel({});".format(self.r2)
		# One level: the way member and its nodes, but not the member relation
		self.assertEqual(after(network, ">;"), self.n(self.n3, self.n4) | self.w(self.w2))
		# All levels, including the relations themselves
		self.assertEqual(after(network, ">>;"), self.n(self.n1, self.n2, self.n3, self.n4) |
			self.w(self.w1, self.w2) | self.r(self.r1, self.r2))
		self.assertEqual(after(route, ">>;"), members | self.r(self.r1))

		self.assertEqual(after("node({});".format(self.n4), "<;"), self.w(self.w2) | self.r(self.r2))
		self.assertEqual(after("node({});".format(self.n1), "<;"), self.w(self.w1) | self.r(self.r1))
		self.assertEqual(after("node({});".format(self.n1), "<<;"), self.w(self.w1) | self.r(self.r1, self.r2))
		self.assertEqual(after("node({});".format(self.n5), "<;"), set())
		self.assertEqual(after(way, "<;"), self.r(self.r1))
		self.assertEqual(after(way, "<<;"), self.r(self.r1, self.r2))
		# Up from a relation: not its parents with <, itself and its parents with <<
		self.assertEqual(after(route, "<;"), set())
		self.assertEqual(after(route, "<<;"), self.r(self.r1, self.r2))

		# Named input and output sets
		self.assertEqual(self.found(way + "._->.a; node({});".format(self.n5) + ".a > ->.b; out; .b out;"),
			self.n(self.n1, self.n2, self.n3, self.n5))
		# The map call: everything in a box, with ways and relations using it
		self.assertEqual(self.found("(node(10.35,20.35,10.45,20.45);<;);out meta;"),
			self.n(self.n4) | self.w(self.w2) | self.r(self.r2))
		# A relation with all its members, as in the wiki's union example
		self.assertEqual(self.found("(rel[ref=E61]({});node(id:{})->.nodes;>;);out;".format(BBOX, self.n5)),
			members | self.r(self.r1) | self.n(self.n5))

	def test_recurse_filters(self):
		route, network = "rel({});".format(self.r1), "rel({});".format(self.r2)
		way1, way2 = "way({});".format(self.w1), "way({});".format(self.w2)
		# Down from relations to their members, by type and by role
		self.assertEqual(self.found(route + "node(r);out;"), self.n(self.n1))
		self.assertEqual(self.found(route + "way(r);out;"), self.w(self.w1))
		self.assertEqual(self.found(route + "rel(r);out;"), set())
		self.assertEqual(self.found(route + "nwr(r);out;"), self.n(self.n1) | self.w(self.w1))
		self.assertEqual(self.found(network + "nwr(r);out;"), self.w(self.w2) | self.r(self.r1))
		self.assertEqual(self.found(route + 'node(r:"stop");out;'), self.n(self.n1))
		self.assertEqual(self.found(route + "node(r:stop);out;"), self.n(self.n1))
		self.assertEqual(self.found(route + 'node(r:"platform");out;'), set())
		self.assertEqual(self.found(route + 'way(r:"route");out;'), self.w(self.w1))
		self.assertEqual(self.found(network + 'nwr(r:"");out;'), self.w(self.w2) | self.r(self.r1))
		self.assertEqual(self.found(network + 'nwr(r:"route");out;'), set())
		# Down from ways to their nodes
		self.assertEqual(self.found(way1 + "node(w);out;"), self.n(self.n1, self.n2, self.n3))
		self.assertEqual(self.found(way1 + "node(w)[amenity=cafe];out;"), self.n(self.n2))
		self.assertEqual(self.found(way1 + "node(w)(10.25,20.25,10.35,20.35);out;"), self.n(self.n3))
		self.assertEqual(self.found(way1 + "way(w);out;"), set())
		self.assertEqual(self.found(way1 + "nwr(w);out;"), self.n(self.n1, self.n2, self.n3))
		# Up from nodes, ways and relations to what holds them
		node3, node1 = "node({});".format(self.n3), "node({});".format(self.n1)
		self.assertEqual(self.found(node3 + "way(bn);out;"), self.w(self.w1, self.w2))
		self.assertEqual(self.found(node3 + "way(bn)[highway=residential];out;"), self.w(self.w2))
		self.assertEqual(self.found(node3 + "rel(bn);out;"), set())
		self.assertEqual(self.found(node1 + "rel(bn);out;"), self.r(self.r1))
		self.assertEqual(self.found(node1 + "wr(bn);out;"), self.w(self.w1) | self.r(self.r1))
		self.assertEqual(self.found(node1 + 'rel(bn:"stop");out;'), self.r(self.r1))
		self.assertEqual(self.found(node1 + 'rel(bn:"route");out;'), set())
		self.assertEqual(self.found(node1 + "node(bn);out;"), set())
		self.assertEqual(self.found(way1 + "rel(bw);out;"), self.r(self.r1))
		self.assertEqual(self.found(way1 + 'rel(bw:"route");out;'), self.r(self.r1))
		self.assertEqual(self.found(way1 + 'rel(bw:"");out;'), set())
		self.assertEqual(self.found(way2 + 'rel(bw:"");out;'), self.r(self.r2))
		self.assertEqual(self.found(route + "rel(br);out;"), self.r(self.r2))
		self.assertEqual(self.found(network + "rel(br);out;"), set())
		self.assertEqual(self.found(route + "way(bw);out;"), set())
		# Named sets, and nodes shared by two sets of ways
		sets = way1 + "._->.a;" + way2 + "._->.b;" + route + "._->.c;"
		self.assertEqual(self.found(sets + "node(w.a);out;"), self.n(self.n1, self.n2, self.n3))
		self.assertEqual(self.found(sets + "node(w.a)(w.b);out;"), self.n(self.n3))
		self.assertEqual(self.found(sets + "node(w.b)(r.c);out;"), set())
		self.assertEqual(self.found(sets + "node(w.a)(r.c);out;"), self.n(self.n1))
		self.assertEqual(self.found(sets + "node(w.nothing);out;"), set())
		# The wiki's examples: bus stops of a route, and its ways with their nodes
		self.assertEqual(self.found("rel[ref=E61]({});node(r);out;".format(BBOX)), self.n(self.n1))
		self.assertEqual(self.found("rel[ref=E61]({});way(r);node(w);out;".format(BBOX)), self.n(self.n1, self.n2, self.n3))
		self.assertEqual(self.found("(rel[ref=E61]({});node(r)->.nodes;way(r);node(w););out;".format(BBOX)),
			self.r(self.r1) | self.w(self.w1) | self.n(self.n1, self.n2, self.n3))
		self.assertEqual(self.found("node({});way(bn);out;".format(BBOX)), self.w(self.w1, self.w2))
		self.assertEqual(self.found("node({});way(bn);way._[highway=primary];out;".format(BBOX)), self.w(self.w1))

	def test_difference(self):
		box = "({})".format(BBOX)
		self.assertEqual(self.found("(node{0}; - node[amenity=pub]{0};);out;".format(box)), self.n(self.n2, self.n3))
		self.assertEqual(self.found("(nwr{0}; - way{0};);out;".format(box)),
			self.n(self.n1, self.n2, self.n3, self.n4) | self.r(self.r1, self.r2))
		self.assertEqual(self.found("(node[amenity=pub]{0}; - node{0};);out;".format(box)), set())
		self.assertEqual(self.found("(node[amenity=pub]{0}; - way{0};);out;".format(box)), self.n(self.n1, self.n4))
		query = "node{0}->.all; node[amenity]{0}->.amenities;".format(box)
		self.assertEqual(self.found(query + "(.all; - .amenities;);out;"), self.n(self.n3))
		# Sent to a named set, the result leaves the default set alone
		self.assertEqual(self.found(query + "(.all; - .amenities;)->.rest; out;"), set())
		self.assertEqual(self.found(query + "(.all; - .amenities;)->.rest; .rest out;"), self.n(self.n3))
		# Each side is an ordinary statement, with its usual effect on the sets
		self.assertEqual(self.found("(node{0}->.kept; - node[amenity]{0};); .kept out;".format(box)),
			self.n(self.n1, self.n2, self.n3, self.n4))
		# Old objects: everything, less what is newer than a date
		self.assertEqual(self.found('node{0}->.all;(.all; - node.all(newer:"2020-01-01T00:00:00Z"););out;'.format(box)), set())
		self.assertEqual(self.found('node{0}->.all;(.all; - node.all(newer:"2030-01-01T00:00:00Z"););out;'.format(box)),
			self.n(self.n1, self.n2, self.n3, self.n4))

	def test_user_and_time_filters(self):
		from django.contrib.auth.models import User as Account
		box = "({})".format(BBOX)
		# An account ID that cannot be mistaken for the other user IDs used here
		account = Account.objects.create(id=555001, username="renamed mapper")
		other = self.create_node(10.11, 20.11, _uid=8, _user="other mapper", _timestamp=1800000000)
		renamed = self.create_node(10.12, 20.12, _uid=9, _user="renamed mapper")
		by_account = self.create_node(10.13, 20.13, _uid=account.id, _user="name at the time")
		original = self.n(self.n1, self.n2, self.n3, self.n4)

		self.assertEqual(self.found("node(uid:7){};out;".format(box)), original)
		self.assertEqual(self.found("node(uid:8){};out;".format(box)), self.n(other))
		self.assertEqual(self.found("node(uid:8,7){};out;".format(box)), original | self.n(other))
		self.assertEqual(self.found("node(uid:999){};out;".format(box)), set())
		self.assertEqual(self.found('node(user:"other mapper"){};out;'.format(box)), self.n(other))
		self.assertEqual(self.found('node(user:mapper,"other mapper"){};out;'.format(box)), original | self.n(other))
		self.assertEqual(self.found('node(user:"Other Mapper"){};out;'.format(box)), set())
		# A name finds edits stored under it, and edits by this server's account of that name
		self.assertEqual(self.found('node(user:"renamed mapper"){};out;'.format(box)), self.n(renamed, by_account))
		self.assertEqual(self.found('node(user:"name at the time"){};out;'.format(box)), self.n(by_account))
		self.assertEqual(self.found("node(uid:7)[amenity=pub]{};out;".format(box)), self.n(self.n1, self.n4))
		self.assertEqual(self.found("wr(uid:7){};out;".format(box)), self.w(self.w1, self.w2) | self.r(self.r1, self.r2))
		self.assertEqual(self.found("wr(uid:8){};out;".format(box)), set())
		self.assertEqual(self.found("way(uid:7)(id:{});out;".format(self.w1)), self.w(self.w1))
		self.assertEqual(self.found("way(uid:8)(id:{});out;".format(self.w1)), set())

		# 1700000000 is 2023-11-14T22:13:20Z and 1800000000 is 2027-01-15T08:00:00Z
		self.assertEqual(self.found('node(newer:"2025-01-01T00:00:00Z"){};out;'.format(box)), self.n(other))
		self.assertEqual(self.found('node(newer:"2023-11-14T22:13:20Z"){};out;'.format(box)), self.n(other))
		self.assertEqual(self.found('node(newer:"2023-11-14T22:13:19Z")(uid:7){};out;'.format(box)), original)
		self.assertEqual(self.found('node(newer:"2028-01-01T00:00:00Z"){};out;'.format(box)), set())
		self.assertEqual(self.found('node(changed:"2023-11-14T22:13:20Z")(uid:7,8){};out;'.format(box)), original | self.n(other))
		self.assertEqual(self.found('node(changed:"2023-11-14T22:13:21Z"){};out;'.format(box)), self.n(other))
		self.assertEqual(self.found('node(changed:"2020-01-01T00:00:00Z","2025-01-01T00:00:00Z")(uid:7){};out;'.format(box)), original)
		self.assertEqual(self.found('node(changed:"2025-01-01T00:00:00Z","2027-01-15T08:00:00Z"){};out;'.format(box)), self.n(other))
		self.assertEqual(self.found('way(newer:"2020-01-01T00:00:00Z"){};out;'.format(box)), self.w(self.w1, self.w2))
		self.assertEqual(self.found('way(newer:"2025-01-01T00:00:00Z"){};out;'.format(box)), set())

		# Neither can be looked up alone
		self.refused("nwr(uid:7);out;", "needs a bounding box")
		self.refused('nwr(user:"mapper");out;', "needs a bounding box")
		self.refused('node(newer:"2025-01-01T00:00:00Z");out;', "needs a bounding box")
		self.refused("node(uid:7)(user:mapper){};out;".format(box), "more than one user or uid filter")
		self.assertEqual(self.found("node(uid:8)[amenity];out;") & self.nodes, set())

	def test_around(self):
		near = self.create_node(10.1005, 20.1) # About 55 metres north of n1
		further = self.create_node(10.1, 20.1012) # About 131 metres east of n1
		self.assertEqual(self.found("node(around:100,10.1,20.1);out;"), self.n(self.n1, near))
		self.assertEqual(self.found("node(around:200,10.1,20.1);out;"), self.n(self.n1, near, further))
		self.assertEqual(self.found("node(around:50,10.1,20.1);out;"), self.n(self.n1))
		self.assertEqual(self.found("node(around:0,10.1,20.1);out;"), self.n(self.n1))
		self.assertEqual(self.found("node(around:100,10.15,20.15);out;"), set())
		self.assertEqual(self.found("node(around:200,10.1,20.1)[amenity];out;"), self.n(self.n1))
		self.assertEqual(self.found("node(around:200,10.1,20.1)[!amenity];out;"), self.n(near, further))
		self.assertEqual(self.found("node(around:200,10.1,20.1)(10.1001,20,10.2,20.2);out;"), self.n(near))
		self.assertEqual(self.found("node(around:200,10.1,20.1)(10.3,20,10.4,20.2);out;"), set())
		self.assertEqual(self.found("node(around:200,10.1,20.1)(id:{});out;".format(further)), self.n(further))
		response = self.ask("[out:json][bbox:10.1001,20,10.2,20.2];node(around:200,10.1,20.1);out ids;")
		self.assertEqual(json.loads(response.content)["elements"], [{"type": "node", "id": near}])

		# Around the elements of a set, which are part of the result if they match
		pub = "node({});".format(self.n1)
		self.assertEqual(self.found(pub + "node(around:100);out;"), self.n(self.n1, near))
		self.assertEqual(self.found(pub + "._->.pub; node({});".format(self.n5) + "node(around.pub:200);out;"),
			self.n(self.n1, near, further))
		self.assertEqual(self.found(pub + "node(around:200)[!amenity];out;"), self.n(near, further))
		self.assertEqual(self.found("node[amenity=pub]({});node(around:100);out;".format(BBOX)), self.n(self.n1, self.n4, near))
		self.assertEqual(self.found("node(around.nothing:100);out;"), set())
		self.assertEqual(self.found("node(999999999999);node(around:100);out;"), set())
		# A way is measured from its nodes
		self.assertEqual(self.found("way({});node(around:100);out;".format(self.w2)), self.n(self.n3, self.n4))
		self.assertEqual(self.found("rel({});node(around:100);out;".format(self.r1)),
			self.n(self.n1, self.n2, self.n3, near))

		# Ways and relations are near a position if one of their nodes is
		self.assertEqual(self.found("way(around:100,10.1,20.1);out;"), self.w(self.w1))
		self.assertEqual(self.found("way(around:100,10.3,20.3);out;"), self.w(self.w1, self.w2))
		self.assertEqual(self.found("way(around:100,10.3,20.3)[highway=residential];out;"), self.w(self.w2))
		self.assertEqual(self.found("way[highway=residential](around:100,10.1,20.1);out;"), set())
		self.assertEqual(self.found("rel(around:100,10.1,20.1);out;"), self.r(self.r1))
		self.assertEqual(self.found("rel(around:100,10.4,20.4);out;"), self.r(self.r2))
		self.assertEqual(self.found("nwr(around:100,10.4,20.4);out;"), self.n(self.n4) | self.w(self.w2) | self.r(self.r2))
		# Pubs with no cafe within a kilometre: the wiki's "banks far from police" pattern
		self.assertEqual(self.found("node[amenity=pub]({0})->.pubs;node[amenity=cafe]({0})->.cafes;"
			"node.pubs(around.cafes:1000)->.close;(.pubs; - .close;);out;".format(BBOX)), self.n(self.n1, self.n4))
		self.assertEqual(self.found("node[amenity=pub]({0})->.pubs;node[amenity=cafe]({0})->.cafes;"
			"node.pubs(around.cafes:20000)->.close;(.pubs; - .close;);out;".format(BBOX)), self.n(self.n4))

		# A large circle is like a large box: it needs an exact tag to go with it
		self.refused("node(around:100000,10.1,20.1);out;", "too large to search")
		self.assertEqual(self.found("node[amenity=cafe](around:100000,10.1,20.1);out;"), self.n(self.n2))
		self.refused("node(around:10,10.1,20.1)(around:10,10.2,20.2);out;", "more than one around filter")
		from . import evaluator
		original = evaluator.AROUND_POSITIONS_MAXIMUM
		evaluator.AROUND_POSITIONS_MAXIMUM = 2
		try:
			self.refused("way({});node(around:10);out;".format(self.w1), "at most 2 positions")
			self.assertEqual(self.found("way({});node(around:10);out;".format(self.w2)), self.n(self.n3, self.n4))
		finally:
			evaluator.AROUND_POSITIONS_MAXIMUM = original

	def csv(self, query):
		response = self.ask(query)
		self.assertEqual(response.status_code, 200, response.content)
		self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
		return response.content.decode("utf-8")

	def test_csv(self):
		ids = sorted([self.n1, self.n2, self.n3, self.n4])
		rows = {self.n1: "node\tThe Crown\tpub", self.n2: "node\tCafé Été\tcafe", self.n3: "node\t\t", self.n4: "node\tthe\tpub"}
		self.assertEqual(self.csv("[out:csv(::id,::type,name,amenity)];node({});out;".format(BBOX)),
			"@id\t@type\tname\tamenity\n" +
			"".join("{}\t{}\n".format(i, rows[i]) for i in ids))
		self.assertEqual(self.csv('[out:csv("name";false)];node[amenity=pub]({});out;'.format(BBOX)),
			"".join(name + "\n" for i, name in sorted([(self.n1, "The Crown"), (self.n4, "the")])))
		self.assertEqual(self.csv('[out:csv(::otype, ::id, "addr:housenumber"; true; "|")];nwr(id:{});out;'.format(self.n2)).split("\n")[:2],
			["@otype|@id|addr:housenumber", "1|{}|12".format(self.n2)])

		# Positions: of nodes, and of anything printed with its center
		self.assertEqual(self.csv("[out:csv(::type,::lat,::lon;false)];node({});out;".format(self.n1)), "node\t10.1000000\t20.1000000\n")
		self.assertEqual(self.csv("[out:csv(::type,::lat,::lon;false)];way({});out;".format(self.w2)), "way\t\t\n")
		self.assertEqual(self.csv("[out:csv(::type,::lat,::lon;false)];way({});out center;".format(self.w2)),
			"way\t10.3500000\t20.3500000\n")
		self.assertEqual(self.csv("[out:csv(::otype,highway;false)];wr({});out;".format(self.w2)).split("\n")[0], "2\tresidential")
		# Metadata appears only with out meta, and tags not with out ids
		fields = "[out:csv(::id,::version,::timestamp,::changeset,::uid,::user,name;false)];node({});".format(self.n1)
		self.assertEqual(self.csv(fields + "out meta;"),
			"{}\t1\t2023-11-14T22:13:20Z\t1000\t7\tmapper\tThe Crown\n".format(self.n1))
		self.assertEqual(self.csv(fields + "out;"), "{}\t\t\t\t\t\tThe Crown\n".format(self.n1))
		self.assertEqual(self.csv(fields + "out ids;"), "{}\t\t\t\t\t\t\n".format(self.n1))

		# Counts, as in the wiki's check that a listing is complete
		text = self.csv('[out:csv(::type,::id,"name",::count)];node[amenity=pub]({});out;out count;'.format(BBOX))
		self.assertEqual(text.split("\n"), ["@type\t@id\tname\t@count"] +
			["node\t{}\t{}\t".format(i, name) for i, name in sorted([(self.n1, "The Crown"), (self.n4, "the")])] +
			["count\t0\t\t2", ""])
		self.assertEqual(self.csv('[out:csv(::count, ::"count:nodes", ::"count:ways", ::count:relations;false)];'
			"nwr({});out count;".format(BBOX)), "8\t4\t2\t2\n")
		self.assertEqual(self.csv("[out:csv(name)];node(999999999999);out;"), "name\n")
		self.refused("[out:csv(name)];node[amenity!=pub];out;", "needs a bounding box")

	def test_output_verbosity(self):
		def one(mode, kind, ident):
			return self.elements("{}({});out {};".format(kind, ident, mode))[0]
		meta = {"timestamp": "2023-11-14T22:13:20Z", "version": 1, "changeset": 1000, "user": "mapper", "uid": 7}
		tags = {"amenity": "pub", "name": "The Crown"}
		position = {"lat": 10.1, "lon": 20.1}
		base = {"type": "node", "id": self.n1}
		self.assertEqual(one("ids", "node", self.n1), base)
		self.assertEqual(one("skel", "node", self.n1), dict(base, **position))
		self.assertEqual(one("body", "node", self.n1), dict(base, tags=tags, **position))
		self.assertEqual(one("", "node", self.n1), dict(base, tags=tags, **position))
		self.assertEqual(one("tags", "node", self.n1), dict(base, tags=tags))
		self.assertEqual(one("meta", "node", self.n1), dict(base, tags=tags, **dict(position, **meta)))
		self.assertEqual(list(one("meta", "node", self.n1)), ["type", "id", "lat", "lon", "timestamp",
			"version", "changeset", "user", "uid", "tags"])
		# An element without tags has no tags member
		self.assertEqual(one("body", "node", self.n3), {"type": "node", "id": self.n3, "lat": 10.3, "lon": 20.3})

		way = {"type": "way", "id": self.w1}
		refs = [self.n1, self.n2, self.n3]
		way_tags = {"highway": "primary", "name": "High Street"}
		self.assertEqual(one("ids", "way", self.w1), way)
		self.assertEqual(one("skel", "way", self.w1), dict(way, nodes=refs))
		self.assertEqual(one("body", "way", self.w1), dict(way, nodes=refs, tags=way_tags))
		self.assertEqual(one("tags", "way", self.w1), dict(way, tags=way_tags))
		self.assertEqual(one("meta", "way", self.w1), dict(way, nodes=refs, tags=way_tags, **meta))

		relation = {"type": "relation", "id": self.r1}
		members = [{"type": "node", "ref": self.n1, "role": "stop"}, {"type": "way", "ref": self.w1, "role": "route"}]
		self.assertEqual(one("skel", "relation", self.r1), dict(relation, members=members))
		self.assertEqual(one("body", "relation", self.r1),
			dict(relation, members=members, tags={"type": "route", "ref": "E61"}))
		self.assertEqual(one("tags", "relation", self.r1), dict(relation, tags={"type": "route", "ref": "E61"}))

	def test_output_geometry(self):
		line = [{"lat": 10.1, "lon": 20.1}, {"lat": 10.2, "lon": 20.2}, {"lat": 10.3, "lon": 20.3}]
		bounds = {"minlat": 10.1, "minlon": 20.1, "maxlat": 10.3, "maxlon": 20.3}
		way = self.elements("way({});out geom;".format(self.w1))[0]
		self.assertEqual(way["nodes"], [self.n1, self.n2, self.n3])
		self.assertEqual(way["geometry"], line)
		self.assertEqual(way["bounds"], bounds)
		self.assertEqual(way["tags"]["highway"], "primary")
		self.assertEqual(list(way), ["type", "id", "bounds", "nodes", "geometry", "tags"])
		# The nodes do not have to be part of the set printed, but may be
		self.assertEqual(self.elements("way({});(._;>;);out geom;".format(self.w1))[-1]["geometry"], line)

		way = self.elements("way({});out bb;".format(self.w1))[0]
		self.assertEqual(way["bounds"], bounds)
		self.assertNotIn("geometry", way)
		way = self.elements("way({});out center;".format(self.w1))[0]
		self.assertAlmostEqual(way["center"]["lat"], 10.2)
		self.assertAlmostEqual(way["center"]["lon"], 20.2)
		self.assertNotIn("bounds", way)
		way = self.elements("way({});out tags center;".format(self.w1))[0]
		self.assertEqual(sorted(way), ["center", "id", "tags", "type"])
		way = self.elements("way({});out ids geom;".format(self.w1))[0]
		self.assertEqual(sorted(way), ["bounds", "geometry", "id", "type"])
		# A node always has its position as its geometry
		self.assertEqual(self.elements("node({});out ids center;".format(self.n1))[0],
			{"type": "node", "id": self.n1, "lat": 10.1, "lon": 20.1})

		relation = self.elements("rel({});out geom;".format(self.r1))[0]
		self.assertEqual(relation["bounds"], bounds)
		self.assertEqual(relation["members"], [
			{"type": "node", "ref": self.n1, "role": "stop", "lat": 10.1, "lon": 20.1},
			{"type": "way", "ref": self.w1, "role": "route", "geometry": line}])
		network = self.elements("rel({});out geom;".format(self.r2))[0]
		# A member that is a relation adds no geometry
		self.assertEqual(network["members"][0], {"type": "relation", "ref": self.r1, "role": ""})
		self.assertEqual(network["members"][1]["geometry"], line[2:] + [{"lat": 10.4, "lon": 20.4}])
		self.assertEqual(network["bounds"], {"minlat": 10.3, "minlon": 20.3, "maxlat": 10.4, "maxlon": 20.4})
		center = self.elements("rel({});out center;".format(self.r2))[0]["center"]
		self.assertAlmostEqual(center["lat"], 10.35)
		self.assertAlmostEqual(center["lon"], 20.35)

	def test_output_order_limit_and_count(self):
		everything = "nwr({});".format(BBOX)
		listed = [(e["type"], e["id"]) for e in self.elements(everything + "out ids;")]
		self.assertEqual(listed, [("node", i) for i in sorted([self.n1, self.n2, self.n3, self.n4])] +
			[("way", i) for i in sorted([self.w1, self.w2])] + [("relation", i) for i in sorted([self.r1, self.r2])])
		self.assertEqual([(e["type"], e["id"]) for e in self.elements(everything + "out ids qt;")], listed)
		self.assertEqual([(e["type"], e["id"]) for e in self.elements(everything + "out ids 5;")], listed[:5])
		self.assertEqual(self.elements(everything + "out ids 0;"), [])

		count = {"type": "count", "id": 0, "tags": {"nodes": "4", "ways": "2", "relations": "2", "total": "8"}}
		self.assertEqual(self.elements(everything + "out count;"), [count])
		# Several out statements each add to the output, repeating elements
		both = self.elements("node[amenity=pub]({});out ids;out count;out ids 1;".format(BBOX))
		self.assertEqual([e["type"] for e in both], ["node", "node", "count", "node"])
		self.assertEqual(both[2]["tags"], {"nodes": "2", "ways": "0", "relations": "0", "total": "2"})
		self.assertEqual(self.elements("out count;")[0]["tags"]["total"], "0")

	@override_settings(GENERATOR="test server", COPYRIGHT="Map data from testers")
	def test_json_document(self):
		response = self.ask("[out:json];node({});out;".format(self.n1))
		self.assertEqual(response["Content-Type"], "application/json")
		self.assertEqual(response["Access-Control-Allow-Origin"], "*")
		doc = json.loads(response.content)
		self.assertEqual(list(doc), ["version", "generator", "osm3s", "elements"])
		self.assertEqual((doc["version"], doc["generator"]), (0.6, "test server"))
		self.assertEqual(doc["osm3s"]["copyright"], "Map data from testers")
		stamp = time.mktime(time.strptime(doc["osm3s"]["timestamp_osm_base"], "%Y-%m-%dT%H:%M:%SZ"))
		self.assertLess(abs(stamp - time.mktime(time.gmtime())), 120)
		self.assertIn("Café Été", self.ask("[out:json];node({});out;".format(self.n2)).content.decode("utf-8"))

	@override_settings(GENERATOR="test server", COPYRIGHT="Map data from <testers>")
	def test_xml_document(self):
		response = self.ask("nwr({});out meta geom;out count;".format(BBOX))
		self.assertEqual(response.status_code, 200, response.content)
		self.assertEqual(response["Content-Type"], "application/osm3s+xml")
		self.assertTrue(response.content.startswith(b'<?xml version="1.0" encoding="UTF-8"?>\n<osm version="0.6"'))
		root = ET.fromstring(response.content)
		self.assertEqual(root.attrib, {"version": "0.6", "generator": "test server"})
		self.assertEqual([child.tag for child in root], ["note", "meta"] + ["node"] * 4 + ["way"] * 2 +
			["relation"] * 2 + ["count"])
		self.assertEqual(root.find("note").text, "Map data from <testers>")
		self.assertRegex(root.find("meta").attrib["osm_base"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")

		node = root.find("node[@id='{}']".format(self.n2))
		self.assertEqual(node.attrib, {"id": str(self.n2), "lat": "10.2000000", "lon": "20.2000000",
			"timestamp": "2023-11-14T22:13:20Z", "version": "1", "changeset": "1000", "user": "mapper", "uid": "7"})
		self.assertEqual([(t.attrib["k"], t.attrib["v"]) for t in node],
			[("addr:housenumber", "12"), ("amenity", "cafe"), ("name", "Café Été")])
		self.assertEqual(len(root.find("node[@id='{}']".format(self.n3))), 0)

		way = root.find("way[@id='{}']".format(self.w2))
		self.assertEqual(way.find("bounds").attrib, {"minlat": "10.3000000", "minlon": "20.3000000",
			"maxlat": "10.4000000", "maxlon": "20.4000000"})
		self.assertEqual([nd.attrib for nd in way.findall("nd")], [
			{"ref": str(self.n3), "lat": "10.3000000", "lon": "20.3000000"},
			{"ref": str(self.n4), "lat": "10.4000000", "lon": "20.4000000"}])
		self.assertEqual([child.tag for child in way], ["bounds", "nd", "nd", "tag"])

		relation = root.find("relation[@id='{}']".format(self.r1))
		members = relation.findall("member")
		self.assertEqual(members[0].attrib, {"type": "node", "ref": str(self.n1), "role": "stop",
			"lat": "10.1000000", "lon": "20.1000000"})
		self.assertEqual(members[1].attrib, {"type": "way", "ref": str(self.w1), "role": "route"})
		self.assertEqual([nd.attrib for nd in members[1]][0], {"lat": "10.1000000", "lon": "20.1000000"})
		self.assertEqual(len(members[1]), 3)
		self.assertEqual([(t.attrib["k"], t.attrib["v"]) for t in root.find("count")],
			[("nodes", "4"), ("ways", "2"), ("relations", "2"), ("total", "8")])

		# Without geometry or metadata: plain members, and a center when asked
		root = ET.fromstring(self.ask("wr({});out center;".format(BBOX)).content)
		way = root.find("way[@id='{}']".format(self.w2))
		self.assertEqual(way.attrib, {"id": str(self.w2)})
		self.assertEqual(way.find("center").attrib, {"lat": "10.3500000", "lon": "20.3500000"})
		self.assertEqual([nd.attrib for nd in way.findall("nd")], [{"ref": str(self.n3)}, {"ref": str(self.n4)}])
		relation = root.find("relation[@id='{}']".format(self.r2))
		self.assertEqual([m.attrib for m in relation.findall("member")], [
			{"type": "relation", "ref": str(self.r1), "role": ""}, {"type": "way", "ref": str(self.w2), "role": ""}])
		root = ET.fromstring(self.ask("node({});out ids;".format(self.n1)).content)
		self.assertEqual(root.find("node").attrib, {"id": str(self.n1)})

	def test_ways_of_sending_a_query(self):
		query = "[out:json];node({});out ids;".format(self.n1)
		expected = [{"type": "node", "id": self.n1}]
		client = Client()
		responses = [
			client.get("/api/interpreter?data=" + quote(query)),
			client.post("/api/interpreter", {"data": query}),
			client.post("/api/interpreter", "data=" + quote(query),
				content_type="application/x-www-form-urlencoded; charset=UTF-8"),
			client.post("/api/interpreter", query, content_type="text/plain"),
			client.post("/overpass/api/interpreter", {"data": query}),
		]
		for response in responses:
			self.assertEqual(response.status_code, 200, response.content)
			self.assertEqual(json.loads(response.content)["elements"], expected)
			self.assertEqual(response["Access-Control-Allow-Origin"], "*")

		response = client.get("/api/interpreter?bbox=20.05,10.05,20.15,10.15&data=" + quote("[out:json][bbox];node;out ids;"))
		self.assertEqual(json.loads(response.content)["elements"], expected)

		response = client.options("/api/interpreter")
		self.assertEqual(response.status_code, 204)
		self.assertEqual(response["Access-Control-Allow-Origin"], "*")
		self.assertIn("POST", response["Access-Control-Allow-Methods"])
		self.assertEqual(client.put("/api/interpreter", query).status_code, 405)

		for response in (client.get("/api/interpreter"), client.post("/api/interpreter", {"data": "  "}),
			client.post("/api/interpreter", b"\xff\xfe", content_type="text/plain")):
			self.assertEqual(response.status_code, 400)
			self.assertTrue(response.content.startswith(b"Error: "))
			self.assertEqual(response["Access-Control-Allow-Origin"], "*")

	def test_errors(self):
		self.refused("node[name;out;", "line 1: expected")
		self.refused('area[name="Bonn"];node(area);out;', "area queries are not supported by this server")
		self.refused("node({{bbox}});out;", "Overpass Turbo shortcut")
		# The database refuses the expression; the next query is unaffected
		self.refused('node[name~"(unclosed"]({});out;'.format(BBOX), "regular expression in the query is not valid")
		self.assertEqual(self.found("node[amenity=cafe]({});out;".format(BBOX)), self.n(self.n2))
		# Text that could be mistaken for SQL is only ever text
		self.assertEqual(self.found("node[name=\"'; DROP TABLE x; --\"]({});out;".format(BBOX)), set())
		self.assertEqual(self.found("node[\"a'b\\\"c\"~\"'\"]({});out;".format(BBOX)), set())

	def test_limits(self):
		everything = "nwr({});out;".format(BBOX)
		with self.settings(OVERPASS_ELEMENTS_MAXIMUM=7):
			self.refused(everything, "more than 7 elements")
			self.refused("way({});>;out;".format(self.w1) * 3, "more than 7 elements")
			self.assertEqual(len(self.elements("node({});out;".format(BBOX))), 4)
		with self.settings(OVERPASS_ELEMENTS_MAXIMUM=8):
			self.assertEqual(len(self.elements(everything)), 8)
			self.refused(everything + "out;", "more than 8 elements")
		# The nodes fetched to give a way its geometry are limited as well
		with self.settings(OVERPASS_ELEMENTS_MAXIMUM=2):
			self.refused("way({});out geom;".format(self.w1), "more than 2 elements")

		# Out of time before starting, and a timeout longer than the server allows
		script = ql.parse(everything)
		with self.assertRaises(QueryTimeout) as caught:
			evaluate(script, Limits(0, 1000, 1.0))
		self.assertEqual(caught.exception.status, 504)
		with self.settings(OVERPASS_TIMEOUT_MAXIMUM=5):
			response = self.ask("[out:json][timeout:100000];" + everything)
			self.assertEqual(len(json.loads(response.content)["elements"]), 8)
		response = self.ask("[timeout:30][maxsize:5][out:json];" + everything)
		self.assertEqual(len(json.loads(response.content)["elements"]), 8)
		# The transaction that timed out was closed: the map can still be locked
		t = get_pgmap().GetTransaction("EXCLUSIVE")
		t.Abort()

	def test_statement_timeout_ends_a_query(self):
		t = get_pgmap().GetTransaction("ACCESS SHARE")
		try:
			t.SetStatementTimeout(1)
			filters = pgmap.vectoroverpasstagfilter()
			filters.append(pgmap.OverpassTagFilter(pgmap.OverpassTagFilter.Matches, "name", "^(a|b)*c$"))
			# A statement long enough that reading it alone takes over a millisecond
			ids = list(range(1, 400001))
			data = pgmap.OsmData()
			with self.assertRaisesRegex(Exception, "Query timed out"):
				t.OverpassQuery("node", filters, [], ids, 0, data)
		finally:
			t.Abort()
		# Zero removes the limit again
		t = get_pgmap().GetTransaction("ACCESS SHARE")
		try:
			t.SetStatementTimeout(1)
			t.SetStatementTimeout(0)
			data = pgmap.OsmData()
			t.OverpassQuery("node", pgmap.vectoroverpasstagfilter(), [], list(range(1, 400001)), 0, data)
			self.assertGreaterEqual(len(data.nodes), 5)
		finally:
			t.Abort()

	@override_settings(OVERPASS_RATE_LIMIT_REQUESTS=2, OVERPASS_RATE_LIMIT_WINDOW_SECONDS=60)
	def test_rate_limit(self):
		from django.core.cache import cache
		cache.clear()
		client = Client()
		query = {"data": "node({});out;".format(self.n1)}
		self.assertEqual(client.post("/api/interpreter", query).status_code, 200)
		self.assertEqual(client.post("/api/interpreter", query).status_code, 200)
		response = client.post("/api/interpreter", query)
		self.assertEqual(response.status_code, 429)
		self.assertEqual(response["Retry-After"], "60")
		cache.clear()

	# *** XAPI, which runs on the same evaluator ***

	def xapi(self, query):
		response = Client().get("/overpass/xapi/" + query)
		self.assertEqual(response.status_code, 200, response.content)
		self.assertEqual(response["Content-Type"], "text/xml")
		data = DecodeOsmdataResponse([response.content])
		return (set(n.objId for n in data.nodes), set(w.objId for w in data.ways),
			set(r.objId for r in data.relations))

	def test_xapi(self):
		box = "[bbox=20,10,20.5,10.5]"
		mine = set([self.n1, self.n2, self.n3, self.n4, self.n5])
		nodes, ways, relations = self.xapi("node[amenity=pub]" + box)
		self.assertEqual((nodes, ways, relations), ({self.n1, self.n4}, set(), set()))
		self.assertEqual(self.xapi("node[amenity=*]" + box)[0], {self.n1, self.n2, self.n4})
		self.assertEqual(self.xapi("node[amenity=pub]")[0] & mine, {self.n1, self.n4, self.n5})
		self.assertEqual(self.xapi("node" + box)[0], {self.n1, self.n2, self.n3, self.n4})
		# Ways and relations come with what they are made of
		self.assertEqual(self.xapi("way[highway=primary]" + box), ({self.n1, self.n2, self.n3}, {self.w1}, set()))
		self.assertEqual(self.xapi("relation[type=network]" + box),
			({self.n1, self.n2, self.n3, self.n4}, {self.w1, self.w2}, {self.r1, self.r2}))
		self.assertEqual(self.xapi("*[name=High Street]" + box), ({self.n1, self.n2, self.n3}, {self.w1}, set()))
		self.assertEqual(self.xapi("*[amenity=pub][name=the]" + box), ({self.n4}, set(), set()))

		response = Client().get("/overpass/xapi/way[highway=primary]" + box)
		root = ET.fromstring(response.content)
		self.assertEqual(root.attrib["version"], "0.6")
		self.assertEqual([child.tag for child in root], ["node", "node", "node", "way"])
		node = root.find("node[@id='{}']".format(self.n1))
		self.assertEqual((node.attrib["user"], node.attrib["uid"], node.attrib["version"], node.attrib["changeset"]),
			("mapper", "7", "1", "1000"))
		self.assertEqual(node.find("tag[@k='name']").attrib["v"], "The Crown")
		self.assertEqual([nd.attrib["ref"] for nd in root.find("way").findall("nd")],
			[str(self.n1), str(self.n2), str(self.n3)])

		response = Client().get("/overpass/xapi_meta?node[amenity=pub]" + box)
		self.assertEqual(response.status_code, 200)
		data = DecodeOsmdataResponse([response.content]) # Kept alive while its nodes are read
		self.assertEqual(set(n.objId for n in data.nodes), {self.n1, self.n4})

		for bad, fragment in (("lake[a=b]", "Object type not recognized"), ("node", "Specify either a bbox"),
			("node[bbox=1,2,3]", "Invalid bbox"), ("node[bbox=20,10,x,11]", "Invalid bbox"),
			("node[bbox=20,11,21,10]", "Invalid bbox"), ("node[bbox=0,0,50,50]", "too large to search")):
			response = Client().get("/overpass/xapi/" + bad)
			self.assertEqual(response.status_code, 400, bad)
			self.assertIn(fragment, response.content.decode("utf-8"))
