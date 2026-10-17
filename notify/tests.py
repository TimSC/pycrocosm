"""Tests for the edit notification server.

Most run the server, in a thread of its own, against a stand-in for the map's
edits, and talk to it over TCP with notify/client.py. The last few use a
scratch map in the map database, as the stored extract tests do: the trigger
that announces edits, areas given as WKT, and the whole path from an upload to
a client.
"""
import asyncio
import gzip
import json
import select
import threading
import time
import xml.etree.ElementTree as ET
from unittest.mock import patch

import msgpack
import pgmap
import psycopg2
from psycopg2 import sql
from django.test import Client, SimpleTestCase

import querymap.test_dbextract as extract_tests
from notify import protocol
from notify.client import NotifyClient
from notify.server import EditNotifier, EditSource, Limits, PgmapEditSource, set_keepalive
from replicate.tests import o5m_objects

TRIANGLE = "POLYGON((0 0,1 0,0 1,0 0))"

class FakeSource(EditSource):
	"""Edits as a test says they are."""

	def __init__(self, latest=(10, 5)):
		self.ids = latest
		self.areas = set() #Areas the new edits concern
		self.asked = []

	def latest(self):
		return self.ids

	def check_wkt(self, wkt):
		if not wkt.startswith(("POLYGON", "MULTIPOLYGON")):
			raise ValueError("The boundary must be a polygon or a multipolygon")
		return wkt

	def concerns(self, first_id, last_id, area):
		self.asked.append(("concerns", first_id, last_id, area))
		return area in self.areas

	def records(self, first_id, last_id, area, file_format="xml"):
		"""One record for each ID in the range, if the edits concern the area."""
		self.asked.append(("records", first_id, last_id, area, file_format))
		if area is not None and area not in self.areas:
			return None
		if file_format == "msgpack":
			return last_id - first_id + 1, [{"id": i, "updated": b"\xff\xfe"} for i in range(first_id, last_id + 1)]
		document = "<editactivities>{}</editactivities>".format(
			"".join('<editactivity id="{}"/>'.format(i) for i in range(first_id, last_id + 1))).encode()
		return last_id - first_id + 1, document

class ServerThread:
	"""An EditNotifier running in a thread of its own, on a free port."""

	def __init__(self, source, limits=None, connection_string=None, channel=None):
		self.messages = []
		self.loop = asyncio.new_event_loop()
		self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
		self.thread.start()
		self.notifier = EditNotifier(source, limits or Limits(), log=self.messages.append)
		self.run(self.notifier.start("127.0.0.1", 0, connection_string, channel))
		self.port = self.notifier.port()

	def run(self, coroutine):
		return asyncio.run_coroutine_threadsafe(coroutine, self.loop).result(10)

	def edits_changed(self):
		self.loop.call_soon_threadsafe(self.notifier.edits_changed)

	def settled(self, latest):
		"""Waits until the server has got as far as latest."""
		deadline = time.time() + 5
		while self.notifier.latest != latest and time.time() < deadline:
			time.sleep(0.01)

	def stop(self):
		self.run(self.notifier.stop())
		self.loop.call_soon_threadsafe(self.loop.stop)
		self.thread.join(10)
		self.loop.close()

	def connect(self, hello=True):
		client = NotifyClient("127.0.0.1", self.port, timeout=10)
		if hello:
			client.call("hello", {"protocol": 2, "client": "tests"})
		return client

def closed(client, wait=5):
	"""Whether the server has closed the connection, waiting up to wait seconds."""
	client.socket.settimeout(wait)
	try:
		while True:
			data = client.socket.recv(65536)
			if not data:
				return True
	except (ConnectionError, OSError):
		return True

def quiet(client):
	"""Whether nothing has been sent to the client but answers: its answer to a
	ping comes after anything sent before it."""
	return client.call("ping") == "pong" and client.waiting == []

EDITS_15 = ("edits", [{"edit_activity_id": 15, "atomic_edit_id": 9}])
NEAR = ("bbox", (0.0, 0.0, 1.0, 1.0))

class ServerTests(SimpleTestCase):

	def start(self, source=None, **limits):
		self.source = source or FakeSource()
		self.server = ServerThread(self.source, Limits(**limits))
		self.addCleanup(self.server.stop)
		return self.server

	def connect(self, hello=True):
		client = self.server.connect(hello)
		self.addCleanup(client.close)
		return client

	def new_edits(self, ids=(15, 9)):
		self.source.ids = ids
		self.server.edits_changed()

	def test_hello(self):
		self.start()
		client = self.connect(hello=False)
		# Nothing but hello first
		with self.assertRaisesRegex(RuntimeError, r"Say hello first \(-32000\)"):
			client.call("subscribe_edits")
		with self.assertRaisesRegex(RuntimeError, r"speaks protocol 2 \(-32001\)"):
			client.call("hello", {"protocol": 1})
		self.assertEqual(client.call("hello", {"protocol": 2, "client": "tests"}),
			{"protocol": 2, "server": "pycrocosm editnotifier", "edit_activity_id": 10, "atomic_edit_id": 5})
		self.assertEqual(client.call("ping"), "pong")

	def test_bad_requests(self):
		self.start()
		client = self.connect()
		for method, params, message in (("nothing", [], "No method nothing"),
				("subscribe", [], "No method subscribe"),
				("subscribe_edits", [{"bbox": [1, 2, 3]}], "four numbers"),
				("subscribe_edits", [{"bbox": [2, 0, 1, 1]}], "west <= east"),
				("subscribe_edits", [{"bbox": [0, 0, 1, 100]}], "latitude -90 to 90"),
				("subscribe_records", [{"bbox": [0, 0, float("nan"), 1]}], "finite"),
				("subscribe_records", [{"bbox": [0, 0, 1, 1], "wkt": TRIANGLE}], "not both"),
				("subscribe_records", [{"wkt": "  "}], "wkt must be the text"),
				("subscribe_records", [{"wkt": "POINT(0 0)"}], "wkt: The boundary must be a polygon"),
				("subscribe_edits", [{"area": 1}], "Unknown option: area"),
				("subscribe_edits", [1, 2], "at most one parameter"),
				("unsubscribe_edits", [], r"Not subscribed to edits \(-32004\)")):
			with self.subTest(method=method, params=params):
				with self.assertRaisesRegex(RuntimeError, message):
					client.call(method, *params)
		# Not a request at all
		client.socket.sendall(msgpack.packb([2, "edits", []]))
		error = next(client._messages())
		self.assertEqual((error[0], error[2]["code"]), (1, protocol.INVALID_REQUEST))
		# The connection is still usable after all of those
		self.assertEqual(client.call("ping"), "pong")

	def test_each_subscription_once(self):
		self.start()
		client = self.connect()
		self.assertEqual(client.call("subscribe_edits"), {"edit_activity_id": 10, "atomic_edit_id": 5})
		with self.assertRaisesRegex(RuntimeError, r"Already subscribed to edits; unsubscribe first \(-32003\)"):
			client.call("subscribe_edits", {"bbox": [0, 0, 1, 1]})
		# The other kind is separate
		client.call("subscribe_records", {"wkt": TRIANGLE})
		with self.assertRaisesRegex(RuntimeError, "Already subscribed to records"):
			client.call("subscribe_records")
		# Unsubscribed, a kind can be subscribed to again, with another area
		self.assertIs(client.call("unsubscribe_edits"), True)
		client.call("subscribe_edits", {"bbox": [0, 0, 1, 1]})
		self.assertIs(client.call("unsubscribe_records"), True)
		with self.assertRaisesRegex(RuntimeError, "Not subscribed to records"):
			client.call("unsubscribe_records")

	def test_subscribing_gives_the_latest_ids(self):
		self.start()
		watcher, client = self.connect(), self.connect()
		watcher.call("subscribe_edits")
		# Edits the server has not heard of yet: subscribing reads the map afresh
		self.source.ids = (12, 7)
		self.assertEqual(client.call("subscribe_edits"), {"edit_activity_id": 12, "atomic_edit_id": 7})
		# Those already subscribed were told on the way
		self.assertEqual(watcher.next_notification(), ("edits", [{"edit_activity_id": 12, "atomic_edit_id": 7}]))
		# but not the new subscriber, whose answer already covers them
		self.assertTrue(quiet(client))
		self.source.ids = (13, 8)
		self.assertEqual(client.call("subscribe_records"), {"edit_activity_id": 13, "atomic_edit_id": 8})
		self.assertEqual(client.next_notification(), ("edits", [{"edit_activity_id": 13, "atomic_edit_id": 8}]))
		self.assertTrue(quiet(client))
		# Later edits reach both of its subscriptions
		self.source.ids = (14, 9)
		self.server.edits_changed()
		self.assertEqual(client.next_notification(), ("edits", [{"edit_activity_id": 14, "atomic_edit_id": 9}]))
		self.assertEqual(client.next_notification()[1][0]["first_id"], 14)

	def test_edits_subscriptions(self):
		self.start()
		near, far, everything, wkt = self.connect(), self.connect(), self.connect(), self.connect()
		near.call("subscribe_edits", {"bbox": [0, 0, 1, 1]})
		far.call("subscribe_edits", {"bbox": [10, 10, 11, 11]})
		everything.call("subscribe_edits")
		wkt.call("subscribe_edits", {"wkt": TRIANGLE})
		self.source.areas = {NEAR, ("wkt", TRIANGLE)}
		self.new_edits()
		for client in (near, everything, wkt):
			self.assertEqual(client.next_notification(), EDITS_15)
		self.assertTrue(quiet(far))
		# Each area was asked about once, for the edits since the last notice;
		# everywhere needs no asking
		self.assertEqual(sorted(self.source.asked, key=str), sorted([("concerns", 11, 15, NEAR),
			("concerns", 11, 15, ("bbox", (10.0, 10.0, 11.0, 11.0))), ("concerns", 11, 15, ("wkt", TRIANGLE))], key=str))
		# Nothing new, nothing said; unsubscribed, nothing more
		self.server.edits_changed()
		self.assertTrue(quiet(everything))
		everything.call("unsubscribe_edits")
		self.new_edits((16, 10))
		self.server.settled((16, 10))
		self.assertTrue(quiet(everything))

	def test_records_subscriptions(self):
		self.start()
		records, far, both = self.connect(), self.connect(), self.connect()
		records.call("subscribe_records", {"bbox": [0, 0, 1, 1]})
		far.call("subscribe_records", {"bbox": [10, 10, 11, 11]})
		both.call("subscribe_edits", {"bbox": [0, 0, 1, 1]})
		both.call("subscribe_records")
		self.source.areas = {NEAR}
		self.new_edits()
		method, params = records.next_notification()
		self.assertEqual(method, "records")
		details = params[0]
		self.assertEqual({k: v for k, v in details.items() if k != "xml_gz"},
			{"first_id": 11, "last_id": 15, "records": 5, "edit_activity_id": 15, "atomic_edit_id": 9})
		self.assertEqual(gzip.decompress(details["xml_gz"]), b"<editactivities>" +
			b"".join('<editactivity id="{}"/>'.format(i).encode() for i in range(11, 16)) + b"</editactivities>")
		self.assertTrue(quiet(far))
		# One connection can hold both
		self.assertEqual(both.next_notification(), EDITS_15)
		self.assertEqual(both.next_notification()[0], "records")
		# The records of each area were read once, and gzipped once for all
		self.assertEqual(sorted((a for a in self.source.asked if a[0] == "records"), key=str),
			sorted([("records", 11, 15, NEAR, "xml"), ("records", 11, 15, ("bbox", (10.0, 10.0, 11.0, 11.0)), "xml"),
				("records", 11, 15, None, "xml")], key=str))

	def test_records_in_msgpack(self):
		self.start()
		xml, packed, packed_too = self.connect(), self.connect(), self.connect()
		xml.call("subscribe_records", {"bbox": [0, 0, 1, 1]})
		packed.call("subscribe_records", {"bbox": [0, 0, 1, 1], "format": "msgpack"})
		packed_too.call("subscribe_records", {"format": "msgpack"})
		self.source.areas = {NEAR}
		self.new_edits()
		self.assertIn("xml_gz", xml.next_notification()[1][0])
		# The records themselves, as maps, objects and all
		method, params = packed.next_notification()
		self.assertEqual((method, params), ("records", [{"first_id": 11, "last_id": 15, "records": 5,
			"edit_activity_id": 15, "atomic_edit_id": 9,
			"editactivities": [{"id": i, "updated": b"\xff\xfe"} for i in range(11, 16)]}]))
		self.assertEqual(packed_too.next_notification(), (method, params))
		# Read once for each area and format
		self.assertEqual(sorted((a for a in self.source.asked if a[0] == "records"), key=str),
			sorted([("records", 11, 15, NEAR, "xml"), ("records", 11, 15, NEAR, "msgpack"),
				("records", 11, 15, None, "msgpack")], key=str))
		# Only records come in formats
		client = self.connect()
		for method, options in (("subscribe_records", {"format": "o5m"}), ("subscribe_records", {"format": 1}),
				("subscribe_edits", {"format": "msgpack"})):
			with self.assertRaisesRegex(RuntimeError, r"\(-32602\)"):
				client.call(method, options)

	def test_no_limit_on_what_the_server_sends(self):
		# A large records notification goes out whole, however much is waiting
		class Large(FakeSource):
			def records(self, first_id, last_id, area, file_format="xml"):
				return 1, b"<editactivities>" + b"x" * 5000000 + b"</editactivities>"
		self.start(Large(), max_message_bytes=1024)
		client = self.connect()
		client.call("subscribe_records")
		client.socket.settimeout(30)
		self.new_edits()
		method, params = client.next_notification()
		self.assertEqual((method, len(gzip.decompress(params[0]["xml_gz"]))), ("records", 5000000 + 33))

	def test_heartbeats(self):
		self.start(heartbeat=0.1)
		client = self.connect()
		client.call("subscribe_records")
		self.assertEqual(client.next_notification(), ("heartbeat", [{"edit_activity_id": 10, "atomic_edit_id": 5}]))
		# The periodic check also finds edits that no one announced
		self.source.ids = (11, 6)
		while True:
			method, params = client.next_notification()
			if method == "records":
				break
		self.assertEqual(params[0]["last_id"], 11)

	def test_a_map_that_goes_back(self):
		server = self.start()
		client = self.connect()
		client.call("subscribe_edits")
		self.new_edits((3, 2))
		server.settled((3, 2))
		# Followed, without a notification; a later edit is announced from there
		self.assertEqual(self.connect().call("hello")["edit_activity_id"], 3)
		self.assertEqual(client.waiting, [])
		self.new_edits((4, 3))
		self.assertEqual(client.next_notification(), ("edits", [{"edit_activity_id": 4, "atomic_edit_id": 3}]))

	# Clients that fall behind

	class Incompressible(FakeSource):
		"""Records too large, even gzipped, for the system's buffers to hide."""
		def records(self, first_id, last_id, area, file_format="xml"):
			import os
			return 1, b"<editactivities>" + os.urandom(12 * 1024 * 1024).hex().encode() + b"</editactivities>"

	def test_clients_that_fall_behind_are_dropped(self):
		self.start(self.Incompressible(), write_high_water=64 * 1024, behind_timeout=0.5)
		client = self.connect()
		client.call("subscribe_records")
		self.new_edits() #And read none of it
		deadline = time.time() + 20
		while not any("fell behind" in m for m in self.server.messages) and time.time() < deadline:
			time.sleep(0.05)
		self.assertTrue(any("fell behind with what it is sent, for 0.5s" in m for m in self.server.messages))
		self.assertTrue(closed(client))
		deadline = time.time() + 5
		while self.server.notifier.connections and time.time() < deadline:
			time.sleep(0.05)
		self.assertEqual(self.server.notifier.connections, set())

	def test_clients_that_catch_up_are_kept(self):
		from notify.server import Connection
		self.start(self.Incompressible(), write_high_water=64 * 1024, behind_timeout=10)
		client = self.connect()
		client.call("subscribe_records")
		client.socket.settimeout(30)
		# Behind for a moment, while it reads a large message, but not for long
		with patch.object(Connection, "pause_writing", autospec=True, side_effect=Connection.pause_writing) as paused:
			self.new_edits()
			method, params = client.next_notification()
		self.assertTrue(paused.called)
		self.assertEqual(method, "records")
		self.assertEqual(len(gzip.decompress(params[0]["xml_gz"])), 24 * 1024 * 1024 + 33)
		self.assertEqual(client.call("ping"), "pong")
		self.assertFalse(any("fell behind" in m for m in self.server.messages))

	def test_keepalive(self):
		import socket
		sock = socket.socket()
		self.addCleanup(sock.close)
		set_keepalive(sock, 42)
		self.assertEqual(sock.getsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE), 1)
		if hasattr(socket, "TCP_KEEPIDLE"):
			self.assertEqual([sock.getsockopt(socket.IPPROTO_TCP, getattr(socket, name))
				for name in ("TCP_KEEPIDLE", "TCP_KEEPINTVL", "TCP_KEEPCNT")], [42, 10, 6])

	# Limits on what clients send, and on connections

	def test_default_limits(self):
		limits = Limits()
		self.assertEqual((limits.max_connections, limits.max_message_bytes, limits.write_high_water,
			limits.behind_timeout, limits.keepalive), (64, 65536, 1024 * 1024, 60, 60))

	def test_connections_from_one_address(self):
		self.start(max_per_address=2)
		first, second = self.connect(), self.connect()
		third = NotifyClient("127.0.0.1", self.server.port, timeout=5)
		self.addCleanup(third.close)
		self.assertTrue(closed(third))
		self.assertTrue(any("2 connections from it already" in m for m in self.server.messages))
		# A place comes free when one goes
		first.close()
		deadline = time.time() + 5
		while time.time() < deadline:
			fourth = NotifyClient("127.0.0.1", self.server.port, timeout=5)
			self.addCleanup(fourth.close)
			try:
				self.assertEqual(fourth.call("hello")["protocol"], 2)
				break
			except (ConnectionError, OSError):
				time.sleep(0.05)
		else:
			self.fail("No place came free")
		self.assertEqual(second.call("ping"), "pong")

	def test_connections_in_all(self):
		self.start(max_connections=1)
		self.connect()
		other = NotifyClient("127.0.0.1", self.server.port, timeout=5)
		self.addCleanup(other.close)
		self.assertTrue(closed(other))

	def test_messages_too_large(self):
		self.start(max_message_bytes=256)
		client = self.connect()
		# A WKT area that fits is fine; one that does not closes the connection
		client.call("subscribe_records", {"wkt": TRIANGLE})
		client.socket.sendall(msgpack.packb([0, 9, "subscribe_edits", [{"wkt": "POLYGON((" + "0 0," * 100 + "0 0))"}]]))
		self.assertTrue(closed(client))

	def test_too_many_requests(self):
		self.start(request_burst=3, requests_per_second=0.001)
		client = self.connect() #hello is the first
		client.call("ping")
		client.call("ping")
		with self.assertRaisesRegex(RuntimeError, r"More than .* requests a second \(-32002\)"):
			client.call("ping")
		self.assertTrue(closed(client))

	def test_time_to_hello_and_subscribe(self):
		self.start(hello_timeout=0.2, subscribe_timeout=0.3)
		silent = self.connect(hello=False)
		self.assertTrue(closed(silent))
		greeted = self.connect()
		self.assertTrue(closed(greeted))
		subscribed = self.connect()
		subscribed.call("subscribe_records")
		time.sleep(0.5)
		self.assertEqual(subscribed.call("ping"), "pong")
		# Holding no subscription again starts the clock again
		subscribed.call("unsubscribe_records")
		self.assertTrue(closed(subscribed))

class MapTests(SimpleTestCase):
	"""With a scratch map, as the stored extract tests make one."""
	databases = {"default"}
	setUp = extract_tests.DbExtractTestCase.setUp
	drop_tables = extract_tests.DbExtractTestCase.drop_tables
	upload = extract_tests.DbExtractTestCase.upload
	create_node = extract_tests.DbExtractTestCase.create_node
	set_schema = extract_tests.DbExtractTestCase.set_schema

	def latest(self):
		t = self.map.GetTransaction("ACCESS SHARE")
		try:
			ids = t.GetLatestEditIds()
			return {"edit_activity_id": int(ids[0]), "atomic_edit_id": int(ids[1])}
		finally:
			t.Abort()

	def triggers(self):
		with self.db.cursor() as cursor:
			cursor.execute("SELECT count(*) FROM pg_trigger WHERE tgrelid = %s::regclass AND tgname = %s",
				[self.prefixes[1] + "edit_activity", self.prefixes[1] + "notify_edits"])
			return cursor.fetchone()[0]

	def serve(self):
		source = PgmapEditSource(lambda: pgmap.PgMap(self.connection_string, self.prefixes[0],
			self.prefixes[1], self.prefixes[1], self.prefixes[2]))
		# A long heartbeat, so only the database's notification can bring news soon
		server = ServerThread(source, Limits(heartbeat=60), self.connection_string, self.prefixes[1] + "edits")
		self.addCleanup(server.stop)
		return server

	def connect(self, server):
		client = server.connect()
		self.addCleanup(client.close)
		return client

	def test_edits_are_announced(self):
		listener = psycopg2.connect(self.connection_string)
		listener.autocommit = True
		self.addCleanup(listener.close)
		with listener.cursor() as cursor:
			cursor.execute(sql.SQL("LISTEN {}").format(sql.Identifier(self.prefixes[1] + "edits")))
		self.create_node(0.5)
		self.assertNotEqual(select.select([listener], [], [], 10), ([], [], []))
		listener.poll()
		payloads = [json.loads(n.payload) for n in listener.notifies]
		# The last tells where the map has got to
		self.assertEqual(payloads[-1], self.latest())

	def test_schema_14_has_no_trigger(self):
		self.assertEqual(self.triggers(), 1)
		self.set_schema(14)
		self.assertEqual(self.triggers(), 0)
		self.set_schema(15)
		self.assertEqual(self.triggers(), 1)

	def test_wkt_areas_are_checked_and_followed(self):
		server = self.serve()
		box, triangle = self.connect(server), self.connect(server)
		box.call("subscribe_edits", {"bbox": [0, 0, 1, 1]})
		triangle.call("subscribe_edits", {"wkt": TRIANGLE})
		triangle.call("subscribe_records", {"wkt": TRIANGLE})
		# Checked by PostGIS, as an extract's boundary is
		with self.assertRaisesRegex(RuntimeError, "not a valid polygon"):
			box.call("subscribe_records", {"wkt": "POLYGON((0 0,1 1,1 0,0 1,0 0))"})
		with self.assertRaisesRegex(RuntimeError, "antimeridian"):
			box.call("subscribe_records", {"wkt": "POLYGON((-170 0,170 0,170 1,-170 1,-170 0))"})
		box.call("subscribe_records", {"wkt": "MULTIPOLYGON(((0 0,1 0,0 1,0 0)),((5 5,6 5,6 6,5 5)))"})
		# Inside the triangle's rectangle but not the triangle: the box hears of it
		self.create_node(0.9, 0.9)
		self.assertEqual(box.next_notification(), ("edits", [self.latest()]))
		self.assertTrue(quiet(triangle))
		# Inside the triangle: both, and its records come too
		inside = self.create_node(0.1, 0.1)
		self.assertEqual(box.next_notification(), ("edits", [self.latest()]))
		self.assertEqual(triangle.next_notification(), ("edits", [self.latest()]))
		method, params = triangle.next_notification()
		self.assertEqual(method, "records")
		nodes = [int(n.get("id")) for n in ET.fromstring(gzip.decompress(params[0]["xml_gz"])).iter("node")]
		self.assertIn(inside.objId, nodes)

	def test_records_are_those_the_api_gives(self):
		server = self.serve()
		client = self.connect(server)
		start = client.call("subscribe_records", {"bbox": [-1, -1, 1, 1]})["edit_activity_id"]
		nodes = [self.create_node(0.1 * i) for i in range(1, 4)]
		outside = self.create_node(5)
		received, seen = [], set()
		# Until the three inside have come (the one outside sends nothing)
		while not {n.objId for n in nodes} <= seen:
			method, params = client.next_notification()
			self.assertEqual(method, "records")
			received.append(params[0])
			seen |= {int(n.get("id")) for n in ET.fromstring(gzip.decompress(params[0]["xml_gz"])).iter("node")}
		self.assertEqual(seen, {n.objId for n in nodes})
		self.assertEqual(received[0]["first_id"], start + 1)
		for before, after in zip(received, received[1:]):
			self.assertEqual(after["first_id"], before["last_id"] + 1)
		# The same records the replication API gives for each range
		for details in received:
			path = "/replication/edit_activities?first_id={}&last_id={}&bbox=-1,-1,1,1".format(
				details["first_id"], details["last_id"])
			with patch("replicate.views.get_pgmap", return_value=self.map):
				answer = Client().get(path)
			self.assertEqual(answer.status_code, 200)
			api = [ET.tostring(e) for e in ET.fromstring(answer.content).findall("editactivity")]
			sent = [ET.tostring(e) for e in ET.fromstring(gzip.decompress(details["xml_gz"])).findall("editactivity")]
			self.assertEqual(sent, api)
			self.assertEqual(len(sent), details["records"])
		self.assertTrue(any(m.startswith("Listening for edits on") for m in server.messages))

	def test_msgpack_records_are_those_the_api_gives(self):
		server = self.serve()
		client = self.connect(server)
		client.call("subscribe_records", {"bbox": [-1, -1, 1, 1], "format": "msgpack"})
		node = self.create_node(0.5)
		method, params = client.next_notification()
		details = params[0]
		self.assertEqual(method, "records")
		self.assertNotIn("xml_gz", details)
		self.assertIn(node.objId, [o[1] for r in details["editactivities"] for o in o5m_objects(r["updated"])])
		path = "/replication/edit_activities?first_id={}&last_id={}&bbox=-1,-1,1,1&format=msgpack.gz".format(
			details["first_id"], details["last_id"])
		with patch("replicate.views.get_pgmap", return_value=self.map):
			answer = Client().get(path)
		self.assertEqual(answer.status_code, 200)
		self.assertEqual(details["editactivities"], msgpack.unpackb(gzip.decompress(answer.content))["editactivities"])
		self.assertEqual(len(details["editactivities"]), details["records"])
